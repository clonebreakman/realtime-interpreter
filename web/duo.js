/* 双向对谈：两个独立 WebSocket 通道 + 两个麦克风输入 */
(function () {
  'use strict';

  const UPLOAD_RATE = 16000;
  const UPLOAD_FRAME_SAMPLES = 320;
  const PLAYBACK_RATE = 24000;
  const PLAYOUT_BUFFER_SEC = 0.05;
  const MAX_QUEUE_AHEAD_SEC = 2.0;

  const $ = (id) => document.getElementById(id);
  const els = {};
  const state = {
    config: null,
    running: false,
    channels: [],
  };

  function cacheDom() {
    els.notice = $('duoNotice');
    els.start = $('duoStart');
    els.stop = $('duoStop');
    els.refresh = $('duoRefresh');
    els.hint = $('duoHint');
    els.keyState = $('duoKeyState');
    els.myVoice = $('duoMyVoice');
    els.peerVoice = $('duoPeerVoice');
    els.peerDevice = $('peerDevice');
    els.selfDevice = $('selfDevice');
    els.peerSource = $('peerSource');
    els.peerTarget = $('peerTarget');
    els.selfSource = $('selfSource');
    els.selfTarget = $('selfTarget');
    els.peerStatus = $('peerStatus');
    els.selfStatus = $('selfStatus');
    els.peerDot = $('peerDot');
    els.selfDot = $('selfDot');
  }

  function showNotice(text, kind) {
    if (!els.notice) return;
    els.notice.textContent = text || '';
    els.notice.hidden = !text;
    els.notice.className = 'notice ' + (kind === 'error' ? 'is-error' : 'is-ok');
  }

  async function apiGet(path) {
    const response = await fetch(path, { cache: 'no-store' });
    if (!response.ok) throw new Error('HTTP ' + response.status);
    return response.json();
  }

  function setPill(el, text, ok) {
    if (!el) return;
    el.textContent = text;
    el.className = 'duo-pill ' + (ok ? 'ok' : 'bad');
  }

  async function refreshConfig() {
    try {
      const config = await apiGet('/api/config');
      state.config = config;
      setPill(els.keyState, config.has_key ? '已配置' : '未配置', !!config.has_key);
      setPill(els.myVoice, config.pro_voice ? '已启用' : '默认音色', true);
      setPill(els.peerVoice, config.peer_pro_voice ? '已启用' : '默认音色（自动学习）', true);
      if (els.hint) {
        if (!config.has_key) {
          els.hint.textContent = '请先在「设置」里填 API Key。';
        } else if (!config.peer_pro_voice) {
          els.hint.textContent = '对方音色未初始化：先用默认音色开始，累计约 30 秒对方语音后会自动复刻。';
        } else {
          els.hint.textContent = '建议戴耳机：你听中文，对方听英文；对方音频请选第二麦克风或“立体声混音”。';
        }
      }
      if (!config.peer_pro_voice) {
        showNotice('对方音色尚未建立：先用默认音色开始，系统会在对话中自动学习对方音色。', 'ok');
      } else {
        showNotice('', 'ok');
      }
      return config;
    } catch (err) {
      setPill(els.keyState, '后端未就绪', false);
      showNotice('后端未就绪：' + (err.message || err), 'error');
      return null;
    }
  }

  async function refreshDevices() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) return;
    let devices = [];
    try {
      devices = await navigator.mediaDevices.enumerateDevices();
    } catch (err) {
      return;
    }
    const inputs = devices.filter((device) => device.kind === 'audioinput');
    const fill = (select, storageKey) => {
      if (!select) return;
      const previous = select.value || localStorage.getItem(storageKey) || '';
      select.innerHTML = '';
      const def = document.createElement('option');
      def.value = '';
      def.textContent = '默认麦克风';
      select.appendChild(def);
      inputs.forEach((device, index) => {
        const option = document.createElement('option');
        option.value = device.deviceId;
        option.textContent = device.label || ('音频输入 ' + (index + 1));
        select.appendChild(option);
      });
      if (previous && inputs.some((item) => item.deviceId === previous)) {
        select.value = previous;
      }
    };
    fill(els.peerDevice, 'rti.duo.peerDevice');
    fill(els.selfDevice, 'rti.duo.selfDevice');
  }

  function persistDevices() {
    try {
      localStorage.setItem('rti.duo.peerDevice', els.peerDevice ? els.peerDevice.value : '');
      localStorage.setItem('rti.duo.selfDevice', els.selfDevice ? els.selfDevice.value : '');
    } catch (err) { /* ignore */ }
  }

  function setStatus(channel, text) {
    if (channel.statusEl) channel.statusEl.textContent = text;
  }

  function setDot(channel, stateName) {
    if (channel.dotEl) channel.dotEl.dataset.state = stateName;
  }

  function append(el, text) {
    if (!el || !text) return;
    el.textContent += text;
    el.scrollTop = el.scrollHeight;
  }

  function newline(el) {
    if (!el) return;
    if (!el.textContent.endsWith('\n')) el.textContent += '\n';
    el.scrollTop = el.scrollHeight;
  }

  function createChannel(profile, deviceEl, sourceEl, targetEl, statusEl, dotEl) {
    return {
      profile,
      deviceEl,
      sourceEl,
      targetEl,
      statusEl,
      dotEl,
      captureCtx: null,
      playCtx: null,
      playGain: null,
      nextPlayTime: 0,
      sources: [],
      stream: null,
      source: null,
      node: null,
      mute: null,
      ws: null,
      wsReady: false,
    };
  }

  function ensurePlayback(channel) {
    if (!channel.playCtx || channel.playCtx.state === 'closed') {
      let ctx;
      try {
        ctx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: PLAYBACK_RATE });
      } catch (err) {
        ctx = new (window.AudioContext || window.webkitAudioContext)();
      }
      channel.playCtx = ctx;
      channel.playGain = ctx.createGain();
      channel.playGain.gain.value = 1.0;
      channel.playGain.connect(ctx.destination);
      channel.nextPlayTime = 0;
    }
    if (channel.playCtx.state === 'suspended') {
      channel.playCtx.resume().catch(() => {});
    }
  }

  function resampleLinear(input, inRate, outRate) {
    const ratio = inRate / outRate;
    const outLength = Math.max(1, Math.round(input.length / ratio));
    const output = new Float32Array(outLength);
    const last = input.length - 1;
    for (let i = 0; i < outLength; i += 1) {
      const position = i * ratio;
      const left = Math.floor(position);
      const right = Math.min(last, left + 1);
      const frac = position - left;
      output[i] = input[left] * (1 - frac) + input[right] * frac;
    }
    return output;
  }

  function enqueueAudio(channel, buffer) {
    if (!(buffer instanceof ArrayBuffer) || buffer.byteLength < 2) return;
    ensurePlayback(channel);
    const ctx = channel.playCtx;
    if (!ctx || ctx.state === 'closed') return;
    if (buffer.byteLength % 2 === 1) buffer = buffer.slice(0, buffer.byteLength - 1);
    const count = buffer.byteLength >> 1;
    if (!count) return;
    const pcm = new Int16Array(buffer, 0, count);
    let samples = new Float32Array(count);
    for (let i = 0; i < count; i += 1) {
      samples[i] = pcm[i] < 0 ? pcm[i] / 32768 : pcm[i] / 32767;
    }
    const outRate = ctx.sampleRate || PLAYBACK_RATE;
    if (outRate !== PLAYBACK_RATE) samples = resampleLinear(samples, PLAYBACK_RATE, outRate);
    let audioBuffer;
    try {
      audioBuffer = ctx.createBuffer(1, samples.length, outRate);
      audioBuffer.copyToChannel(samples, 0);
    } catch (err) {
      return;
    }
    const source = ctx.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(channel.playGain || ctx.destination);
    const now = ctx.currentTime;
    let startAt = channel.nextPlayTime;
    if (startAt < now + 0.005 || startAt > now + MAX_QUEUE_AHEAD_SEC) {
      channel.sources.forEach((item) => { try { item.stop(); } catch (err) { /* ignore */ } });
      channel.sources = [];
      startAt = now + PLAYOUT_BUFFER_SEC;
    }
    try {
      source.start(startAt);
    } catch (err) {
      return;
    }
    channel.nextPlayTime = startAt + audioBuffer.duration;
    channel.sources.push(source);
    source.onended = () => {
      const index = channel.sources.indexOf(source);
      if (index >= 0) channel.sources.splice(index, 1);
    };
  }

  async function openStream(channel) {
    const selected = channel.deviceEl ? channel.deviceEl.value : '';
    const constraints = {
      channelCount: 1,
      echoCancellation: channel.profile === 'self',
      noiseSuppression: true,
      autoGainControl: true,
    };
    if (selected) constraints.deviceId = { exact: selected };
    try {
      return await navigator.mediaDevices.getUserMedia({ audio: constraints, video: false });
    } catch (err) {
      if (selected && (err.name === 'OverconstrainedError' || err.name === 'NotFoundError')) {
        delete constraints.deviceId;
        return navigator.mediaDevices.getUserMedia({ audio: constraints, video: false });
      }
      throw err;
    }
  }

  async function startChannel(channel) {
    const Ctor = window.AudioContext || window.webkitAudioContext;
    if (!navigator.mediaDevices || !Ctor) throw new Error('当前浏览器不支持双麦克风采集');
    if (typeof window.AudioWorkletNode === 'undefined') throw new Error('当前浏览器不支持 AudioWorklet');

    // 在点击手势内同步创建播放上下文，避免 iOS 自动播放限制
    ensurePlayback(channel);
    channel.captureCtx = new Ctor();
    channel.stream = await openStream(channel);
    channel.captureCtx.resume().catch(() => {});
    await channel.captureCtx.audioWorklet.addModule('./pcm-worklet.js');

    channel.source = channel.captureCtx.createMediaStreamSource(channel.stream);
    channel.node = new window.AudioWorkletNode(channel.captureCtx, 'pcm16-capture', {
      numberOfInputs: 1,
      numberOfOutputs: 1,
      outputChannelCount: [1],
      processorOptions: { targetRate: UPLOAD_RATE, frameSamples: UPLOAD_FRAME_SAMPLES },
    });
    channel.mute = channel.captureCtx.createGain();
    channel.mute.gain.value = 0;
    channel.source.connect(channel.node);
    channel.node.connect(channel.mute);
    channel.mute.connect(channel.captureCtx.destination);

    const wsScheme = location.protocol === 'https:' ? 'wss' : 'ws';
    channel.ws = new WebSocket(wsScheme + '://' + location.host + '/ws');
    channel.ws.binaryType = 'arraybuffer';
    channel.ws.onopen = () => {
      channel.ws.send(JSON.stringify({
        type: 'start',
        profile: channel.profile,
        collect: true,
      }));
    };
    channel.ws.onmessage = (event) => {
      if (event.data instanceof ArrayBuffer) {
        enqueueAudio(channel, event.data);
        return;
      }
      let message;
      try {
        message = JSON.parse(event.data);
      } catch (err) {
        return;
      }
      if (message.type === 'ready') {
        channel.wsReady = true;
        setDot(channel, 'live');
        setStatus(channel, '已连接 · 说话即可');
      } else if (message.type === 'transcript') {
        if (message.final) newline(channel.sourceEl);
        else append(channel.sourceEl, message.text || '');
      } else if (message.type === 'translation') {
        append(channel.targetEl, message.text || '');
      } else if (message.type === 'done') {
        newline(channel.targetEl);
        setStatus(channel, '本轮完成 · 继续说话');
      } else if (message.type === 'error') {
        setDot(channel, 'idle');
        setStatus(channel, '错误：' + (message.message || 'unknown'));
        showNotice((channel.profile === 'peer' ? '对方通道：' : '我方通道：') + message.message, 'error');
      }
    };
    channel.ws.onerror = () => {
      setStatus(channel, 'WebSocket 错误');
    };
    channel.ws.onclose = () => {
      channel.wsReady = false;
      if (state.running) setDot(channel, 'idle');
    };

    channel.node.port.onmessage = (event) => {
      if (!(event.data instanceof ArrayBuffer)) return;
      if (!channel.wsReady || !channel.ws || channel.ws.readyState !== WebSocket.OPEN) return;
      if (channel.ws.bufferedAmount > 256 * 1024) return;
      channel.ws.send(event.data);
    };
    channel.node.port.postMessage({ type: 'start' });
    setStatus(channel, '连接中…');
  }

  function stopChannel(channel) {
    channel.wsReady = false;
    if (channel.ws) {
      try { channel.ws.close(); } catch (err) { /* ignore */ }
      channel.ws = null;
    }
    if (channel.node) {
      try { channel.node.port.postMessage({ type: 'stop' }); } catch (err) { /* ignore */ }
      try { channel.node.disconnect(); } catch (err) { /* ignore */ }
      channel.node = null;
    }
    if (channel.source) {
      try { channel.source.disconnect(); } catch (err) { /* ignore */ }
      channel.source = null;
    }
    if (channel.mute) {
      try { channel.mute.disconnect(); } catch (err) { /* ignore */ }
      channel.mute = null;
    }
    if (channel.stream) {
      channel.stream.getTracks().forEach((track) => { try { track.stop(); } catch (err) { /* ignore */ } });
      channel.stream = null;
    }
    if (channel.captureCtx) {
      try { channel.captureCtx.close(); } catch (err) { /* ignore */ }
      channel.captureCtx = null;
    }
    if (channel.playCtx) {
      try { channel.playCtx.close(); } catch (err) { /* ignore */ }
      channel.playCtx = null;
    }
    setDot(channel, 'idle');
    setStatus(channel, '已停止');
  }

  function stopDuo() {
    state.running = false;
    state.channels.forEach(stopChannel);
    state.channels = [];
    if (els.start) els.start.disabled = false;
    if (els.stop) els.stop.disabled = true;
    if (els.hint) els.hint.textContent = '已停止。点击“开始双向对谈”重新连接。';
  }

  async function startDuo() {
    if (state.running) return;
    const config = state.config || await refreshConfig();
    if (!config || !config.has_key) {
      showNotice('请先配置 API Key。', 'error');
      return;
    }
    if (!els.peerDevice || !els.selfDevice) return;
    if (els.peerDevice.value && els.peerDevice.value === els.selfDevice.value) {
      showNotice('两个通道选择了同一个输入设备；建议对方用第二麦克风或系统“立体声混音”。', 'error');
    } else {
      showNotice('', 'ok');
    }
    persistDevices();
    state.running = true;
    els.start.disabled = true;
    els.stop.disabled = false;
    els.peerSource.textContent = '';
    els.peerTarget.textContent = '';
    els.selfSource.textContent = '';
    els.selfTarget.textContent = '';
    const peer = createChannel('peer', els.peerDevice, els.peerSource, els.peerTarget, els.peerStatus, els.peerDot);
    const self = createChannel('self', els.selfDevice, els.selfSource, els.selfTarget, els.selfStatus, els.selfDot);
    state.channels = [peer, self];
    try {
      await Promise.all([startChannel(peer), startChannel(self)]);
    } catch (err) {
      showNotice('启动失败：' + (err.message || err), 'error');
      stopDuo();
    }
  }

  async function init() {
    cacheDom();
    els.start.addEventListener('click', startDuo);
    els.stop.addEventListener('click', stopDuo);
    els.refresh.addEventListener('click', async () => {
      await refreshConfig();
      await refreshDevices();
    });
    [els.peerDevice, els.selfDevice].forEach((select) => {
      if (select) select.addEventListener('change', persistDevices);
    });
    window.addEventListener('pagehide', () => { if (state.running) stopDuo(); });
    await refreshConfig();
    await refreshDevices();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
