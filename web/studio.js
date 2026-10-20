/* 音色制作独立页：录音 30 秒 → 试听 → 分析复刻 → 保存专业音色 */
(function () {
  'use strict';

  const TARGET_RATE = 24000;
  const FRAME_SAMPLES = 480;
  const RECORD_SECONDS = 30;

  const $ = (id) => document.getElementById(id);
  const els = {};
  const state = {
    recording: false,
    analyzing: false,
    hasKey: false,
    chunks: [],
    wavBuffer: null,
    wavUrl: '',
    duration: 0,
    peak: 0,
    stream: null,
    ctx: null,
    node: null,
    source: null,
    mute: null,
    timer: 0,
    lastVoice: '',
  };

  function cacheDom() {
    els.apiKey = $('studioApiKey');
    els.profileSelect = $('profileSelect');
    els.device = $('studioDevice');
    els.refreshDevices = $('studioRefreshDevices');
    els.configStatus = $('studioConfigStatus');
    els.recordBtn = $('recordBtn');
    els.reRecordBtn = $('reRecordBtn');
    els.levelBar = $('levelBar');
    els.recordStatus = $('recordStatus');
    els.player = $('recordingPlayer');
    els.name = $('voiceName');
    els.analyzeBtn = $('analyzeBtn');
    els.analyzeStatus = $('analyzeStatus');
    els.resultCard = $('resultCard');
    els.resultVoice = $('resultVoice');
    els.resultProVoice = $('resultProVoice');
    els.setDefaultBtn = $('setDefaultBtn');
    els.defaultVoice = $('defaultVoice');
    els.voiceList = $('voiceList');
  }

  function describeError(err) {
    if (!err) return '未知错误';
    return err.message || String(err);
  }

  async function apiGet(path) {
    const res = await fetch(path, { cache: 'no-store', headers: { Accept: 'application/json' } });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    return res.json();
  }

  async function apiPost(path, body) {
    const res = await fetch(path, {
      method: 'POST',
      cache: 'no-store',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      let message = 'HTTP ' + res.status;
      try {
        const data = await res.json();
        if (data && data.detail) {
          message = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
        }
      } catch (err) { /* keep status */ }
      throw new Error(message);
    }
    return res.json();
  }

  function setText(el, text) {
    if (el) el.textContent = text;
  }

  async function refreshConfig() {
    try {
      const config = await apiGet('/api/config');
      state.hasKey = !!config.has_key;
      setText(els.configStatus, state.hasKey
        ? 'API Key 已配置 · 模型 ' + (config.model || '')
        : 'API Key 未配置：请先填写，再录制。');
      if (config.voice) {
        setText(els.defaultVoice,
          '我的专业音色：' + (config.pro_voice || '未创建')
            + ' · 对方专业音色：' + (config.peer_pro_voice || '未创建'));
      }
      return config;
    } catch (err) {
      setText(els.configStatus, '后端未就绪：' + describeError(err));
      return null;
    }
  }

  async function saveKeyIfAny() {
    const key = els.apiKey ? els.apiKey.value.trim() : '';
    if (!key) return state.hasKey;
    setText(els.configStatus, '正在保存 API Key…');
    await apiPost('/api/settings', { api_key: key });
    if (els.apiKey) els.apiKey.value = '';
    await refreshConfig();
    return true;
  }

  async function refreshDevices() {
    if (!els.device || !navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) return;
    let devices = [];
    try {
      devices = await navigator.mediaDevices.enumerateDevices();
    } catch (err) {
      return;
    }
    const inputs = devices.filter((item) => item.kind === 'audioinput');
    const previous = els.device.value;
    els.device.innerHTML = '';
    const def = document.createElement('option');
    def.value = '';
    def.textContent = '默认麦克风';
    els.device.appendChild(def);
    inputs.forEach((item, index) => {
      const option = document.createElement('option');
      option.value = item.deviceId;
      option.textContent = item.label || ('麦克风 ' + (index + 1));
      els.device.appendChild(option);
    });
    if (previous) els.device.value = previous;
  }

  async function refreshVoices() {
    try {
      const data = await apiGet('/api/voices');
      const defaultVoice = data.default || '';
      setText(els.defaultVoice, '默认音色：' + (defaultVoice || '—'));
      if (!els.voiceList) return;
      els.voiceList.innerHTML = '';
      const rows = Array.isArray(data.voices) ? data.voices : [];
      if (rows.length === 0) {
        const empty = document.createElement('p');
        empty.className = 'studio-note';
        empty.textContent = '本机还没有录音生成的音色；当前默认是预置的复刻音色。';
        els.voiceList.appendChild(empty);
        return;
      }
      rows.forEach((item) => {
        const row = document.createElement('div');
        row.className = 'voice-row';
        const meta = document.createElement('div');
        meta.className = 'voice-meta';
        const name = document.createElement('div');
        name.className = 'voice-name';
        name.textContent = item.name || '我的音色';
        const id = document.createElement('div');
        id.className = 'voice-id';
        id.textContent = item.id || '';
        meta.appendChild(name);
        meta.appendChild(id);
        row.appendChild(meta);
        if (item.id === defaultVoice) {
          const badge = document.createElement('span');
          badge.className = 'voice-badge';
          badge.textContent = '默认';
          row.appendChild(badge);
        } else {
          const button = document.createElement('button');
          button.type = 'button';
          button.className = 'ghost-btn';
          button.textContent = '设为默认';
          button.addEventListener('click', () => setDefaultVoice(item.id));
          row.appendChild(button);
        }
        els.voiceList.appendChild(row);
      });
    } catch (err) {
      setText(els.defaultVoice, '读取音色失败：' + describeError(err));
    }
  }

  async function setDefaultVoice(voice) {
    try {
      await apiPost('/api/voices/default', {
        voice,
        profile: els.profileSelect ? els.profileSelect.value : 'self',
      });
      state.lastVoice = voice;
      setText(els.analyzeStatus, '已设为默认音色：' + voice);
      await refreshVoices();
      await refreshConfig();
    } catch (err) {
      setText(els.analyzeStatus, '设为默认失败：' + describeError(err));
    }
  }

  function setLevel(ratio) {
    if (!els.levelBar) return;
    const percent = Math.max(0, Math.min(100, ratio * 100));
    els.levelBar.style.width = percent.toFixed(1) + '%';
  }

  function levelFromChunk(chunk) {
    let sum = 0;
    for (let i = 0; i < chunk.length; i += 1) {
      const value = chunk[i] / 32768;
      sum += value * value;
    }
    return Math.sqrt(sum / Math.max(1, chunk.length));
  }

  function stopGraph() {
    if (state.node) {
      try { state.node.port.postMessage({ type: 'stop' }); } catch (err) { /* ignore */ }
      try { state.node.disconnect(); } catch (err) { /* ignore */ }
    }
    if (state.source) {
      try { state.source.disconnect(); } catch (err) { /* ignore */ }
    }
    if (state.mute) {
      try { state.mute.disconnect(); } catch (err) { /* ignore */ }
    }
    if (state.stream) {
      try { state.stream.getTracks().forEach((track) => track.stop()); } catch (err) { /* ignore */ }
    }
    if (state.ctx) {
      try { state.ctx.close(); } catch (err) { /* ignore */ }
    }
    state.node = null;
    state.source = null;
    state.mute = null;
    state.stream = null;
    state.ctx = null;
  }

  function encodeWav(pcm, sampleRate) {
    const buffer = new ArrayBuffer(44 + pcm.length * 2);
    const view = new DataView(buffer);
    const writeString = (offset, text) => {
      for (let i = 0; i < text.length; i += 1) view.setUint8(offset + i, text.charCodeAt(i));
    };
    writeString(0, 'RIFF');
    view.setUint32(4, 36 + pcm.length * 2, true);
    writeString(8, 'WAVE');
    writeString(12, 'fmt ');
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    writeString(36, 'data');
    view.setUint32(40, pcm.length * 2, true);
    new Int16Array(buffer, 44).set(pcm);
    return buffer;
  }

  function base64FromBuffer(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = '';
    const chunk = 0x8000;
    for (let i = 0; i < bytes.length; i += chunk) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
    }
    return btoa(binary);
  }

  async function startRecording() {
    if (state.recording) return;
    if (state.analyzing) return;
    const Ctor = window.AudioContext || window.webkitAudioContext;
    if (!navigator.mediaDevices || typeof navigator.mediaDevices.getUserMedia !== 'function') {
      setText(els.recordStatus, '当前浏览器不支持麦克风录音。');
      return;
    }
    if (!Ctor || typeof window.AudioWorkletNode === 'undefined') {
      setText(els.recordStatus, '当前浏览器不支持 AudioWorklet，无法录音。');
      return;
    }
    try {
      if (!(await saveKeyIfAny())) {
        setText(els.recordStatus, '请先填写 API Key，再录制音色。');
        if (els.apiKey) els.apiKey.focus();
        return;
      }
    } catch (err) {
      setText(els.recordStatus, 'API Key 保存失败：' + describeError(err));
      return;
    }

    state.recording = true;
    state.chunks = [];
    state.wavBuffer = null;
    state.duration = 0;
    state.peak = 0;
    els.analyzeBtn.disabled = true;
    els.resultCard.hidden = true;
    els.player.hidden = true;
    els.reRecordBtn.hidden = true;
    els.recordBtn.disabled = true;
    setText(els.recordStatus, '准备麦克风…');
    setLevel(0);

    try {
      const deviceId = els.device ? els.device.value : '';
      const constraints = {
        channelCount: 1,
        echoCancellation: false,
        noiseSuppression: true,
        autoGainControl: true,
      };
      if (deviceId) constraints.deviceId = { exact: deviceId };
      state.stream = await navigator.mediaDevices.getUserMedia({ audio: constraints, video: false });
      state.ctx = new Ctor({ latencyHint: 'interactive' });
      await state.ctx.audioWorklet.addModule('./pcm-worklet.js');
      state.source = state.ctx.createMediaStreamSource(state.stream);
      state.node = new window.AudioWorkletNode(state.ctx, 'pcm16-capture', {
        numberOfInputs: 1,
        numberOfOutputs: 1,
        outputChannelCount: [1],
        processorOptions: { targetRate: TARGET_RATE, frameSamples: FRAME_SAMPLES },
      });
      state.mute = state.ctx.createGain();
      state.mute.gain.value = 0;
      state.source.connect(state.node);
      state.node.connect(state.mute);
      state.mute.connect(state.ctx.destination);
      state.node.port.onmessage = (event) => {
        if (!(event.data instanceof ArrayBuffer)) return;
        const chunk = new Int16Array(event.data);
        state.chunks.push(chunk);
        setLevel(levelFromChunk(chunk) * 900);
      };
      state.node.port.postMessage({ type: 'start' });
      const startedAt = Date.now();
      const timer = window.setInterval(() => {
        const left = Math.max(0, RECORD_SECONDS - Math.floor((Date.now() - startedAt) / 1000));
        setText(els.recordStatus, '录音中…还剩 ' + left + ' 秒，请连续说中文。');
      }, 200);
      state.timer = timer;
      await new Promise((resolve) => setTimeout(resolve, RECORD_SECONDS * 1000));
      window.clearInterval(timer);
      state.timer = 0;
      stopGraph();
      finishRecording();
    } catch (err) {
      window.clearInterval(state.timer);
      state.timer = 0;
      stopGraph();
      state.recording = false;
      els.recordBtn.disabled = false;
      setText(els.recordStatus, '录音失败：' + describeError(err));
    }
  }

  function finishRecording() {
    state.recording = false;
    els.recordBtn.disabled = false;
    const total = state.chunks.reduce((sum, chunk) => sum + chunk.length, 0);
    if (total < TARGET_RATE * 3) {
      setText(els.recordStatus, '录音太短（少于 3 秒），请重新录制。');
      setLevel(0);
      return;
    }
    const pcm = new Int16Array(total);
    let offset = 0;
    let peak = 0;
    state.chunks.forEach((chunk) => {
      pcm.set(chunk, offset);
      offset += chunk.length;
      for (let i = 0; i < chunk.length; i += 1) {
        const value = Math.abs(chunk[i]);
        if (value > peak) peak = value;
      }
    });
    state.duration = total / TARGET_RATE;
    state.peak = peak / 32768;
    if (state.peak < 0.005) {
      setText(els.recordStatus, '录音太安静（峰值 ' + state.peak.toFixed(4) + '），请检查麦克风。');
      setLevel(0);
      return;
    }
    state.wavBuffer = encodeWav(pcm, TARGET_RATE);
    if (state.wavUrl) {
      try { URL.revokeObjectURL(state.wavUrl); } catch (err) { /* ignore */ }
    }
    state.wavUrl = URL.createObjectURL(new Blob([state.wavBuffer], { type: 'audio/wav' }));
    els.player.src = state.wavUrl;
    els.player.hidden = false;
    els.reRecordBtn.hidden = false;
    els.analyzeBtn.disabled = false;
    setLevel(state.peak * 900);
    setText(
      els.recordStatus,
      '录音完成：' + state.duration.toFixed(1) + ' 秒，峰值 ' + state.peak.toFixed(3)
        + '；可试听后点「分析并保存音色」。'
    );
  }

  async function analyze() {
    if (state.analyzing || !state.wavBuffer) return;
    state.analyzing = true;
    els.analyzeBtn.disabled = true;
    setText(els.analyzeStatus, '正在上传录音并分析，请稍候…');
    try {
      const result = await apiPost('/api/enroll', {
        audio_base64: base64FromBuffer(state.wavBuffer),
        mime_type: 'audio/wav',
        name: els.name ? els.name.value.trim() || '我的音色' : '我的音色',
        profile: els.profileSelect ? els.profileSelect.value : 'self',
      });
      if (!result || !result.voice) throw new Error('服务端没有返回音色 ID');
      state.lastVoice = result.voice;
      els.resultCard.hidden = false;
      setText(els.resultVoice, result.voice);
      if (els.resultProVoice) {
        setText(
          els.resultProVoice,
          result.pro_voice
            ? result.pro_voice + '（CosyVoice 3.5）'
            : '未创建：' + (result.pro_error || '专业音色接口不可用')
        );
      }
      setText(
        els.analyzeStatus,
        '分析完成：录音 ' + result.duration_s + ' 秒；'
          + (result.pro_voice ? '专业音色已启用。' : '已保存普通复刻音色。')
      );
      await refreshVoices();
      await refreshConfig();
    } catch (err) {
      setText(els.analyzeStatus, '分析失败：' + describeError(err));
      els.analyzeBtn.disabled = false;
    } finally {
      state.analyzing = false;
    }
  }

  async function init() {
    cacheDom();
    if (els.profileSelect) {
      const requested = new URLSearchParams(window.location.search).get('profile');
      if (requested === 'peer' || requested === 'self') {
        els.profileSelect.value = requested;
      }
      if (requested === 'peer' && els.name) {
        els.name.value = '对方音色';
      }
      els.profileSelect.addEventListener('change', () => {
        if (els.name) {
          els.name.value = els.profileSelect.value === 'peer' ? '对方音色' : '我的音色';
        }
      });
    }
    els.recordBtn.addEventListener('click', startRecording);
    els.reRecordBtn.addEventListener('click', startRecording);
    els.analyzeBtn.addEventListener('click', analyze);
    els.setDefaultBtn.addEventListener('click', () => {
      if (state.lastVoice) setDefaultVoice(state.lastVoice);
    });
    els.refreshDevices.addEventListener('click', refreshDevices);
    els.apiKey.addEventListener('change', () => {
      saveKeyIfAny().catch((err) => setText(els.configStatus, '保存失败：' + describeError(err)));
    });
    await refreshConfig();
    await refreshDevices();
    await refreshVoices();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
