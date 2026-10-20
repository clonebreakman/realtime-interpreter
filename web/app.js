/*
 * 实时同传 · 中→英 — 前端主逻辑（原生 JS，无任何外部依赖）
 *
 * 与后端约定的协议（同源）：
 *   WebSocket: ws(s)://<host>/ws
 *     客户端 → 服务端
 *       {"type":"start","voice":"<音色 id 或空>"}   连接建立后发送一次
 *       二进制 PCM16 LE / 16000Hz / 单声道 / 每帧 320 samples（640 bytes）
 *       {"type":"stop"}                              停止
 *     服务端 → 客户端
 *       {"type":"ready","model":"...","voice":"..."}
 *       {"type":"transcript","text":"...","final":false}   中文识别增量；final=true 为一句最终文本
 *       {"type":"translation","text":"...","final":false}  英文翻译增量
 *       {"type":"done"}                                    一轮回复结束
 *       {"type":"error","message":"..."}
 *       二进制 PCM16 LE / 24000Hz / 单声道（直接排队播放）
 *   HTTP:
 *       GET  /api/config   → {"voice":"...","model":"...","has_key":true|false}
 *       POST /api/settings → JSON {"api_key":"...","voice":"..."} → {"ok":true}
 *       GET  /api/health   → {"ok":true,...}
 *
 * 语法检查：node --check web/app.js
 */
(function () {
  'use strict';

  /* ============================== 常量 ============================== */

  const UPLOAD_RATE = 16000;              // 上行采样率
  const UPLOAD_FRAME_SAMPLES = 320;       // 20ms
  const UPLOAD_FRAME_BYTES = UPLOAD_FRAME_SAMPLES * 2; // 640
  const PLAYBACK_RATE = 24000;            // 下行采样率
  const PLAYOUT_BUFFER_SEC = 0.04;        // 起播缓冲 ~40ms
  const MAX_QUEUE_AHEAD_SEC = 2.0;        // 播放队列积压上限，超过则重置
  const MAX_WS_BUFFERED_BYTES = 256 * 1024; // 上行拥塞时丢帧
  const WS_CONNECT_TIMEOUT_MS = 10000;
  const MAX_LINES = 400;                  // 每个文本区最多保留行数
  const LS_VOICE = 'rti.voice';
  const LS_DEVICE = 'rti.inputDevice';
  const LS_VOLUME = 'rti.playbackVolume';
  const SW_PATH = './sw.js';

  /* ============================== 状态 ============================== */

  const state = {
    running: false,          // 用户会话是否进行中（含连接中）
    blocked: false,          // 环境不支持
    ws: null,
    wsReady: false,          // 是否已收到服务端 ready
    wsConnectTimer: 0,
    readyInfo: null,         // {model, voice}
    config: null,            // GET /api/config 结果
    healthOk: false,
    enrolling: false,        // 正在录 15s 音色
    lastEnrollUrl: '',       // 最近一次录音的 blob URL
    captureCtx: null,        // 麦克风侧 AudioContext
    captureSource: null,
    workletNode: null,
    muteGain: null,
    micStream: null,
    playCtx: null,           // 播放侧 AudioContext（优先 24k）
    playGain: null,          // 播放侧总音量（克隆音色偏小时可调大）
    scheduledSources: [],
    nextPlayTime: 0,
    sessionStartedAt: 0,
    firstFrameSent: false,
    firstPacketMs: null,
    turnStartAt: 0,          // 最近一次 transcript final 的时间戳
    lastTurnLatencyMs: null, // 句末 → 首包 的估算延迟
    receivedAudioBytes: 0,
    textState: {
      transcript: { el: null, line: null, text: '' },
      translation: { el: null, line: null, text: '' }
    }
  };

  const els = {};

  /* ============================== DOM ============================== */

  function $(id) {
    return document.getElementById(id);
  }

  function cacheDom() {
    els.statusDot = $('statusDot');
    els.statusText = $('statusText');
    els.statusMeta = $('statusMeta');
    els.notice = $('notice');
    els.transcript = $('transcript');
    els.translation = $('translation');
    els.startBtn = $('startBtn');
    els.startBtnLabel = $('startBtnLabel');
    els.hint = $('hint');
    els.settingsBtn = $('settingsBtn');
    els.settingsPanel = $('settingsPanel');
    els.settingsClose = $('settingsClose');
    els.settingsBackdrop = els.settingsPanel ? els.settingsPanel.querySelector('[data-close-settings]') : null;
    els.apiKeyInput = $('apiKeyInput');
    els.voiceInput = $('voiceInput');
    els.enrollBtn = $('enrollBtn');
    els.playEnrollBtn = $('playEnrollBtn');
    els.enrollMsg = $('enrollMsg');
    els.deviceSelect = $('deviceSelect');
    els.refreshDevicesBtn = $('refreshDevicesBtn');
    els.volumeInput = $('volumeInput');
    els.saveSettingsBtn = $('saveSettingsBtn');
    els.backendInfo = $('backendInfo');
    els.settingsMsg = $('settingsMsg');
    els.toast = $('toast');
    state.textState.transcript.el = els.transcript;
    state.textState.translation.el = els.translation;
  }

  /* ============================== 初始化 ============================== */

  function init() {
    cacheDom();
    bindEvents();
    loadStoredSettings();
    if (els.volumeInput) {
      els.volumeInput.value = String(storedVolume());
    }
    setStatus('idle', '未连接', '正在检测后端…');
    checkSupport();
    checkBackend();
    refreshDevices(false);
    registerServiceWorker();
    window.setInterval(tick, 500);
  }

  function bindEvents() {
    if (els.startBtn) els.startBtn.addEventListener('click', onPrimaryClick);
    if (els.settingsBtn) els.settingsBtn.addEventListener('click', () => openSettings(true));
    if (els.settingsClose) els.settingsClose.addEventListener('click', () => openSettings(false));
    if (els.settingsBackdrop) els.settingsBackdrop.addEventListener('click', () => openSettings(false));
    if (els.saveSettingsBtn) els.saveSettingsBtn.addEventListener('click', saveSettings);
    if (els.enrollBtn) els.enrollBtn.addEventListener('click', startEnrollment);
    if (els.playEnrollBtn) els.playEnrollBtn.addEventListener('click', playLastEnrollment);
    if (els.apiKeyInput) els.apiKeyInput.addEventListener('change', saveApiKeyIfAny);
    if (els.refreshDevicesBtn) els.refreshDevicesBtn.addEventListener('click', () => refreshDevices(true));
    if (els.deviceSelect) els.deviceSelect.addEventListener('change', persistDeviceChoice);
    if (els.voiceInput) els.voiceInput.addEventListener('change', persistVoiceChoice);
    if (els.volumeInput) {
      els.volumeInput.addEventListener('input', () => {
        setPlaybackVolume(parseFloat(els.volumeInput.value));
      });
    }
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && els.settingsPanel && !els.settingsPanel.hidden) openSettings(false);
    });
    window.addEventListener('pagehide', () => {
      if (state.running) stopSession('pagehide');
    });
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState !== 'visible') return;
      if (state.running && state.playCtx && state.playCtx.state === 'suspended') {
        state.playCtx.resume().catch(() => {});
      }
    });
  }

  function checkSupport() {
    const issues = [];
    if (!window.isSecureContext) {
      issues.push('当前页面不是安全上下文（HTTPS）：浏览器会拦截麦克风。请通过 https:// 或 http://localhost 打开。');
    }
    if (!navigator.mediaDevices || typeof navigator.mediaDevices.getUserMedia !== 'function') {
      issues.push('当前浏览器不支持麦克风采集（getUserMedia）。');
    }
    if (typeof window.AudioWorkletNode === 'undefined') {
      issues.push('当前浏览器不支持 AudioWorklet，无法做 16kHz 降采样。请使用新版 Chrome / Edge / Safari。');
    }
    if (!(window.AudioContext || window.webkitAudioContext)) {
      issues.push('当前浏览器不支持 Web Audio。');
    }
    if (issues.length > 0) {
      state.blocked = true;
      if (els.startBtn) els.startBtn.disabled = true;
      showNotice(issues.join('\n'), 'error');
    }
  }

  /* ============================== 后端 / 设置 ============================== */

  async function apiGet(path) {
    const res = await fetch(path, {
      method: 'GET',
      cache: 'no-store',
      headers: { Accept: 'application/json' }
    });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    return res.json();
  }

  async function apiPost(path, body) {
    const res = await fetch(path, {
      method: 'POST',
      cache: 'no-store',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(body)
    });
    if (!res.ok) {
      let message = 'HTTP ' + res.status;
      try {
        const data = await res.json();
        if (data && data.detail) {
          message = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
        }
      } catch (err) {
        /* keep HTTP status */
      }
      throw new Error(message);
    }
    return res.json();
  }

  async function checkBackend() {
    let health = null;
    let config = null;
    try {
      health = await apiGet('/api/health');
    } catch (err) {
      health = null;
    }
    try {
      config = await apiGet('/api/config');
    } catch (err) {
      config = null;
    }
    state.healthOk = !!(health && health.ok);
    state.config = config;

    if (config) {
      const keyState = config.has_key ? 'API Key 已配置' : 'API Key 未配置';
      if (els.backendInfo) {
        els.backendInfo.textContent = '后端正常 · 模型 ' + (config.model || '未知') + ' · ' + keyState;
      }
      const configVoice =
        config.voice && String(config.voice).toLowerCase() !== 'tina'
          ? String(config.voice)
          : '';
      if (
        els.voiceInput &&
        (!els.voiceInput.value || els.voiceInput.value.toLowerCase() === 'tina') &&
        configVoice
      ) {
        els.voiceInput.value = configVoice;
      }
      if (els.hint) {
        if (!config.has_key) {
          els.hint.textContent = '请点击右上角设置填写 API Key；专业音色已启用。';
        } else if (!configVoice && !els.voiceInput.value) {
          els.hint.textContent = '请点击右上角 🎙 进入「音色制作」页录制音色。';
        } else {
          els.hint.textContent = config.pro_voice ? '专业音色已启用（CosyVoice 3.5），点击「开始同传」直接说中文。' : '点击「开始同传」，直接说中文即可。';
        }
      }
    } else if (els.backendInfo) {
      els.backendInfo.textContent = state.healthOk
        ? '后端正常，但 /api/config 暂不可用'
        : '后端未就绪（请确认服务已启动）';
    }

    if (!state.running) {
      const meta = config
        ? '后端就绪 · 模型 ' + (config.model || '未知')
        : (state.healthOk ? '后端就绪 · 配置接口不可用' : '后端未就绪');
      setStatus('idle', '未连接', meta);
      if (els.hint && config && !config.has_key) {
        els.hint.textContent = '请点击右上角设置填写 API Key；专业音色已启用。';
      }
      if (
        els.hint &&
        config &&
        config.has_key &&
        (!config.voice || String(config.voice).toLowerCase() === 'tina') &&
        !els.voiceInput.value
      ) {
        els.hint.textContent = '请点击右上角 🎙 进入「音色制作」页录制音色。';
      }
      if (els.hint && !config) {
        els.hint.textContent = '后端未就绪：请确认同传服务已启动后刷新页面。';
      }
    }
  }

  function loadStoredSettings() {
    let voice = '';
    let device = '';
    try {
      voice = localStorage.getItem(LS_VOICE) || '';
      device = localStorage.getItem(LS_DEVICE) || '';
    } catch (err) {
      voice = '';
      device = '';
    }
    if (els.voiceInput) {
      const cleaned = voice && voice.toLowerCase() === 'tina' ? '' : voice;
      els.voiceInput.value = cleaned;
    }
    if (els.deviceSelect) els.deviceSelect.value = device;
  }

  function storedVoice() {
    try {
      const value = (localStorage.getItem(LS_VOICE) || '').trim();
      return value.toLowerCase() === 'tina' ? '' : value;
    } catch (err) {
      return '';
    }
  }

  function storedDevice() {
    try {
      return localStorage.getItem(LS_DEVICE) || '';
    } catch (err) {
      return '';
    }
  }

  function persistVoiceChoice() {
    try {
      const value = els.voiceInput ? els.voiceInput.value.trim() : '';
      if (value) localStorage.setItem(LS_VOICE, value);
      else localStorage.removeItem(LS_VOICE);
    } catch (err) {
      /* localStorage 不可用时忽略 */
    }
  }

  function persistDeviceChoice() {
    try {
      const value = els.deviceSelect ? els.deviceSelect.value : '';
      if (value) localStorage.setItem(LS_DEVICE, value);
      else localStorage.removeItem(LS_DEVICE);
    } catch (err) {
      /* ignore */
    }
  }

  function getVoice() {
    const stored = storedVoice();
    if (stored) return stored;
    if (state.config && state.config.voice) {
      const value = String(state.config.voice).trim();
      return value.toLowerCase() === 'tina' ? '' : value;
    }
    return '';
  }

  function openSettings(open) {
    if (!els.settingsPanel) return;
    els.settingsPanel.hidden = !open;
    if (els.settingsBtn) els.settingsBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
    document.body.classList.toggle('sheet-open', open);
    if (!open) return;
    refreshDevices(false).catch(() => {});
    if (!state.config) checkBackend();
    if (els.settingsClose) els.settingsClose.focus();
  }

  function focusEnrollment() {
    window.setTimeout(() => {
      if (!els.enrollBtn) return;
      try {
        els.enrollBtn.scrollIntoView({ block: 'center', behavior: 'smooth' });
      } catch (err) {
        /* older browsers */
      }
      try { els.enrollBtn.focus(); } catch (err) { /* ignore */ }
    }, 120);
  }

  async function saveApiKeyIfAny() {
    const key = els.apiKeyInput ? els.apiKeyInput.value.trim() : '';
    if (!key) return false;
    try {
      await apiPost('/api/settings', { api_key: key });
      if (els.apiKeyInput) els.apiKeyInput.value = '';
      await checkBackend();
      showSettingsMsg('API Key 已保存，下次启动自动加载。', 'ok');
      return true;
    } catch (err) {
      showSettingsMsg('API Key 保存失败：' + describeError(err), 'error');
      return false;
    }
  }

  async function saveSettings() {
    if (!els.saveSettingsBtn || els.saveSettingsBtn.disabled) return;
    els.saveSettingsBtn.disabled = true;
    const voice = els.voiceInput ? els.voiceInput.value.trim() : '';
    const apiKey = els.apiKeyInput ? els.apiKeyInput.value.trim() : '';
    persistVoiceChoice();
    // 只提交用户本次输入的 Key；为空时不发送，避免覆盖后端已配置的值。
    const body = { voice: voice };
    if (apiKey) body.api_key = apiKey;
    try {
      const result = await apiPost('/api/settings', body);
      if (result && result.ok === false) throw new Error('服务端拒绝保存');
      if (apiKey && els.apiKeyInput) els.apiKeyInput.value = '';
      showSettingsMsg('已保存' + (apiKey ? '；API Key 已写入本机 config.json' : '') + '。', 'ok');
      await checkBackend();
    } catch (err) {
      showSettingsMsg('保存失败：' + describeError(err) + '。请确认后端已启动。', 'error');
    } finally {
      els.saveSettingsBtn.disabled = false;
    }
  }

  let settingsMsgTimer = 0;

  function setEnrollMsg(text, kind) {
    if (!els.enrollMsg) return;
    els.enrollMsg.hidden = !text;
    els.enrollMsg.textContent = text || '';
    els.enrollMsg.className = 'settings-msg ' + (kind === 'error' ? 'is-error' : 'is-ok');
  }

  function setEnrollButton(busy) {
    if (!els.enrollBtn) return;
    els.enrollBtn.disabled = busy;
    els.enrollBtn.textContent = busy
      ? '录音中…请连续说 15 秒'
      : '重新录制我的音色（15 秒）';
  }

  function playLastEnrollment() {
    if (!state.lastEnrollUrl) return;
    try {
      const audio = new Audio(state.lastEnrollUrl);
      audio.play().catch(() => {
        setEnrollMsg('浏览器阻止了播放，请再点一次「试听刚才录音」。', 'error');
      });
    } catch (err) {
      setEnrollMsg('试听失败：' + describeError(err), 'error');
    }
  }

  function encodeWav(pcm, sampleRate) {
    const buffer = new ArrayBuffer(44 + pcm.length * 2);
    const view = new DataView(buffer);
    const writeString = (offset, text) => {
      for (let i = 0; i < text.length; i += 1) {
        view.setUint8(offset + i, text.charCodeAt(i));
      }
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

  function arrayBufferToBase64(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = '';
    const chunk = 0x8000;
    for (let i = 0; i < bytes.length; i += chunk) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
    }
    return btoa(binary);
  }

  async function startEnrollment() {
    if (state.running) {
      setEnrollMsg('请先停止同传，再录制音色。', 'error');
      return;
    }
    if (state.enrolling) return;
    const Ctor = window.AudioContext || window.webkitAudioContext;
    if (!navigator.mediaDevices || !Ctor) {
      setEnrollMsg('当前浏览器不支持麦克风录音。', 'error');
      return;
    }
    if (typeof window.AudioWorkletNode === 'undefined') {
      setEnrollMsg('当前浏览器不支持 AudioWorklet，无法录音。', 'error');
      return;
    }

    let countdown = 0;
    try {
      const typedKey = els.apiKeyInput ? els.apiKeyInput.value.trim() : '';
      if (typedKey) {
        await apiPost('/api/settings', { api_key: typedKey });
        if (els.apiKeyInput) els.apiKeyInput.value = '';
        await checkBackend();
      }
      if (state.config && state.config.has_key === false) {
        setEnrollMsg('请先填写 API Key，再录制音色。', 'error');
        return;
      }

      state.enrolling = true;
      setEnrollButton(true);
      setEnrollMsg('录音中：请连续说 15 秒，不要长时间停顿…', 'ok');
      const startedAt = Date.now();
      countdown = window.setInterval(() => {
        const left = Math.max(0, 15 - Math.floor((Date.now() - startedAt) / 1000));
        setEnrollMsg('录音中…还剩 ' + left + ' 秒', 'ok');
      }, 250);

      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: false,
          noiseSuppression: true,
          autoGainControl: true
        },
        video: false
      });
      const ctx = new Ctor({ latencyHint: 'interactive' });
      await ctx.audioWorklet.addModule('./pcm-worklet.js');
      const source = ctx.createMediaStreamSource(stream);
      const node = new window.AudioWorkletNode(ctx, 'pcm16-capture', {
        numberOfInputs: 1,
        numberOfOutputs: 1,
        outputChannelCount: [1],
        processorOptions: { targetRate: 24000, frameSamples: 480 }
      });
      const chunks = [];
      node.port.onmessage = (event) => {
        if (event.data instanceof ArrayBuffer) {
          chunks.push(new Int16Array(event.data));
        }
      };
      const mute = ctx.createGain();
      mute.gain.value = 0;
      source.connect(node);
      node.connect(mute);
      mute.connect(ctx.destination);
      node.port.postMessage({ type: 'start' });

      await new Promise((resolve) => setTimeout(resolve, 15000));
      window.clearInterval(countdown);
      node.port.postMessage({ type: 'stop' });
      try { stream.getTracks().forEach((track) => track.stop()); } catch (err) { /* ignore */ }
      try { source.disconnect(); } catch (err) { /* ignore */ }
      try { node.disconnect(); } catch (err) { /* ignore */ }
      try { ctx.close(); } catch (err) { /* ignore */ }

      const total = chunks.reduce((sum, item) => sum + item.length, 0);
      if (total < 24000 * 3) {
        throw new Error('录音太短或被静音，请检查麦克风');
      }
      const pcm = new Int16Array(total);
      let offset = 0;
      let peak = 0;
      chunks.forEach((item) => {
        pcm.set(item, offset);
        offset += item.length;
        for (let i = 0; i < item.length; i += 1) {
          const value = Math.abs(item[i]);
          if (value > peak) peak = value;
        }
      });
      const peakRatio = peak / 32768;
      if (peakRatio < 0.005) {
        throw new Error('录音太安静（峰值 ' + peakRatio.toFixed(4) + '），请检查麦克风是否静音');
      }
      const duration = total / 24000;
      const wav = encodeWav(pcm, 24000);
      if (state.lastEnrollUrl) {
        try { URL.revokeObjectURL(state.lastEnrollUrl); } catch (err) { /* ignore */ }
      }
      state.lastEnrollUrl = URL.createObjectURL(new Blob([wav], { type: 'audio/wav' }));
      if (els.playEnrollBtn) {
        els.playEnrollBtn.hidden = false;
      }
      setEnrollMsg(
        '录音完成：' + duration.toFixed(1) + ' 秒，峰值 ' + peakRatio.toFixed(3)
          + '；正在分析并保存音色…',
        'ok'
      );
      const base64 = arrayBufferToBase64(wav);
      const result = await apiPost('/api/enroll', {
        audio_base64: base64,
        mime_type: 'audio/wav'
      });
      if (!result || !result.voice) {
        throw new Error('服务端没有返回音色 ID');
      }
      if (els.voiceInput) els.voiceInput.value = result.voice;
      persistVoiceChoice();
      state.config = state.config || {};
      state.config.voice = result.voice;
      setEnrollMsg(
        '录音已保存（' + duration.toFixed(1) + ' 秒，'
          + (result.saved || 'voice_recording.wav') + '）；基础音色已设为 '
          + result.voice,
        'ok'
      );
      if (els.hint) els.hint.textContent = '音色已保存并设为默认，点击「开始同传」。';
    } catch (err) {
      setEnrollMsg('录制/保存音色失败：' + describeError(err), 'error');
    } finally {
      window.clearInterval(countdown);
      state.enrolling = false;
      setEnrollButton(false);
    }
  }

  function showSettingsMsg(text, kind) {
    if (!els.settingsMsg) return;
    els.settingsMsg.hidden = false;
    els.settingsMsg.textContent = text;
    els.settingsMsg.className = 'settings-msg ' + (kind === 'error' ? 'is-error' : 'is-ok');
    window.clearTimeout(settingsMsgTimer);
    if (kind !== 'error') {
      settingsMsgTimer = window.setTimeout(() => {
        if (els.settingsMsg) els.settingsMsg.hidden = true;
      }, 4000);
    }
  }

  async function refreshDevices(askPermission) {
    if (!navigator.mediaDevices || typeof navigator.mediaDevices.enumerateDevices !== 'function') return;
    let devices = [];
    try {
      devices = await navigator.mediaDevices.enumerateDevices();
    } catch (err) {
      return;
    }
    let inputs = devices.filter((d) => d.kind === 'audioinput');
    const needPermission = askPermission && inputs.some((d) => !d.label);
    if (needPermission) {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        stream.getTracks().forEach((track) => {
          try { track.stop(); } catch (err) { /* ignore */ }
        });
        devices = await navigator.mediaDevices.enumerateDevices();
        inputs = devices.filter((d) => d.kind === 'audioinput');
      } catch (err) {
        /* 用户拒绝授权：保留无标签的设备列表 */
      }
    }
    if (!els.deviceSelect) return;
    const previous = els.deviceSelect.value || storedDevice();
    els.deviceSelect.innerHTML = '';
    const defaultOption = document.createElement('option');
    defaultOption.value = '';
    defaultOption.textContent = '默认麦克风';
    els.deviceSelect.appendChild(defaultOption);
    inputs.forEach((device, index) => {
      const option = document.createElement('option');
      option.value = device.deviceId;
      option.textContent = device.label || '麦克风 ' + (index + 1);
      els.deviceSelect.appendChild(option);
    });
    if (previous && inputs.some((d) => d.deviceId === previous)) {
      els.deviceSelect.value = previous;
    } else {
      els.deviceSelect.value = '';
    }
  }

  /* ============================== 状态 / 提示 ============================== */

  function setStatus(kind, text, meta) {
    if (els.statusDot) els.statusDot.dataset.state = kind;
    if (els.statusText) els.statusText.textContent = text;
    if (els.statusMeta) els.statusMeta.textContent = meta || '';
  }

  function composeLiveMeta() {
    if (!state.wsReady) {
      if (state.ws) return '已连接服务器，等待 ready…';
      return '正在连接服务器…';
    }
    const parts = [];
    if (state.readyInfo && state.readyInfo.model) parts.push('模型 ' + state.readyInfo.model);
    if (state.readyInfo && state.readyInfo.voice) parts.push('音色 ' + state.readyInfo.voice);
    parts.push(state.firstPacketMs === null ? '等待首包…' : '首包 ' + state.firstPacketMs + 'ms');
    if (state.lastTurnLatencyMs !== null) parts.push('上轮译音 ' + state.lastTurnLatencyMs + 'ms');
    if (state.receivedAudioBytes > 0) {
      parts.push('音频 ' + (state.receivedAudioBytes / (PLAYBACK_RATE * 2)).toFixed(1) + 's');
    }
    return parts.join(' · ');
  }

  function updateStatusMeta() {
    if (!state.running || !els.statusMeta) return;
    els.statusMeta.textContent = composeLiveMeta();
  }

  function ensureLiveStatus() {
    if (!state.wsReady || !state.running) return;
    if (els.statusDot && els.statusDot.dataset.state !== 'live') {
      setStatus('live', '同传中', composeLiveMeta());
    }
  }

  function tick() {
    if (!state.running) return;
    updateStatusMeta();
  }

  function showNotice(text, kind) {
    if (!els.notice) return;
    if (!text) {
      clearNotice();
      return;
    }
    els.notice.hidden = false;
    els.notice.textContent = text;
    els.notice.className = 'notice ' + (kind === 'warn' ? 'is-warn' : 'is-error');
  }

  function clearNotice() {
    if (!els.notice) return;
    els.notice.hidden = true;
    els.notice.textContent = '';
    els.notice.className = 'notice';
  }

  let toastTimer = 0;

  function toast(message) {
    if (!els.toast) return;
    els.toast.textContent = message;
    els.toast.hidden = false;
    els.toast.classList.add('show');
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => {
      if (!els.toast) return;
      els.toast.classList.remove('show');
      els.toast.hidden = true;
    }, 2200);
  }

  function describeError(err) {
    if (!err) return '未知错误';
    if (err.name === 'TypeError') return '网络请求失败（后端可能未启动）';
    return err.message || String(err);
  }

  function startErrorMessage(err) {
    const name = err && err.name ? err.name : '';
    if (name === 'NotAllowedError' || name === 'PermissionDeniedError' || name === 'SecurityError') {
      return '麦克风权限被拒绝。请在浏览器的站点权限中允许麦克风后重试。';
    }
    if (name === 'NotFoundError' || name === 'DevicesNotFoundError') {
      return '没有检测到可用的麦克风设备。';
    }
    if (name === 'NotReadableError' || name === 'TrackStartError') {
      return '麦克风被其他应用占用，无法读取。';
    }
    if (name === 'OverconstrainedError') {
      return '所选麦克风不可用，请在设置中重新选择输入设备。';
    }
    return describeError(err);
  }

  /* ============================== 文本面板 ============================== */

  function appendText(kind, text, final) {
    const pane = state.textState[kind];
    if (!pane || !pane.el) return;
    if (!pane.line) {
      const line = document.createElement('div');
      line.className = 'line interim';
      pane.el.appendChild(line);
      pane.line = line;
      pane.text = '';
    }
    if (text) {
      if (pane.text && text.length > pane.text.length && text.indexOf(pane.text) === 0) {
        // 兼容服务端下发“累计全文”的情况：整体替换，避免重复
        pane.text = text;
      } else if (text !== pane.text) {
        // 常规增量：追加
        pane.text += text;
      }
    }
    if (pane.line) pane.line.textContent = pane.text;
    if (final) {
      finalizePane(kind);
    }
    scrollPaneToBottom(pane.el);
  }

  function finalizePane(kind) {
    const pane = state.textState[kind];
    if (!pane || !pane.line) return;
    if (pane.text) {
      pane.line.classList.remove('interim');
      pane.line.classList.add('final');
    } else if (pane.line.parentNode) {
      pane.line.parentNode.removeChild(pane.line);
    }
    pane.line = null;
    pane.text = '';
    trimPane(pane.el);
  }

  function clearPanes() {
    ['transcript', 'translation'].forEach((kind) => {
      const pane = state.textState[kind];
      if (!pane || !pane.el) return;
      pane.el.innerHTML = '';
      pane.line = null;
      pane.text = '';
    });
  }

  function trimPane(element) {
    if (!element) return;
    while (element.childElementCount > MAX_LINES) {
      if (element.firstElementChild) element.removeChild(element.firstElementChild);
      else break;
    }
  }

  function scrollPaneToBottom(element) {
    if (!element) return;
    const distance = element.scrollHeight - element.scrollTop - element.clientHeight;
    if (distance < 80) element.scrollTop = element.scrollHeight;
  }

  /* ============================== WebSocket ============================== */

  function wsUrl() {
    if (!window.location.host) return '';
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return protocol + '//' + window.location.host + '/ws';
  }

  function connectSocket() {
    const url = wsUrl();
    if (!url) {
      failSession(new Error('无法确定 WebSocket 地址：请通过 http/https 打开页面'));
      return;
    }
    let ws;
    try {
      ws = new WebSocket(url);
    } catch (err) {
      failSession(err);
      return;
    }
    state.ws = ws;
    state.wsReady = false;
    state.firstFrameSent = false;
    ws.binaryType = 'arraybuffer';

    window.clearTimeout(state.wsConnectTimer);
    state.wsConnectTimer = window.setTimeout(() => {
      if (state.ws === ws && !state.wsReady) {
        try { ws.close(); } catch (err) { /* ignore */ }
        showNotice('连接超时：10 秒内没有收到服务端 ready，请确认后端已启动。', 'error');
      }
    }, WS_CONNECT_TIMEOUT_MS);

    ws.onopen = () => {
      if (state.ws !== ws) return;
      const payload = { type: 'start', voice: getVoice() };
      try {
        ws.send(JSON.stringify(payload));
      } catch (err) {
        failSession(err);
        return;
      }
      setStatus('connecting', '连接中', '已连接服务器，等待 ready…');
      if (els.hint) els.hint.textContent = '已连接服务器，等待服务端就绪…';
    };

    ws.onmessage = (event) => {
      if (state.ws !== ws) return;
      handleSocketMessage(event.data);
    };

    ws.onerror = () => {
      if (state.ws !== ws) return;
      showNotice('WebSocket 连接出错，请确认后端已启动。', 'error');
    };

    ws.onclose = () => {
      window.clearTimeout(state.wsConnectTimer);
      state.wsConnectTimer = 0;
      if (state.ws !== ws) return;
      state.ws = null;
      if (!state.running) return;
      const wasReady = state.wsReady;
      teardownSession();
      setStatus('error', '错误', wasReady ? '连接已断开' : '连接失败');
      showNotice(
        wasReady
          ? '与服务器的连接已断开，请重新开始同传。'
          : '无法连接服务器，请确认后端已启动后重试。',
        'error'
      );
      if (els.hint) els.hint.textContent = '连接已断开，点击「开始同传」重试。';
    };
  }

  function handleSocketMessage(data) {
    if (typeof data === 'string') {
      let msg = null;
      try {
        msg = JSON.parse(data);
      } catch (err) {
        console.warn('[rti] 无法解析服务端消息', err);
        return;
      }
      if (msg && typeof msg === 'object') handleServerMessage(msg);
      return;
    }
    if (data instanceof ArrayBuffer) {
      enqueueAudioData(data);
      return;
    }
    if (data instanceof Blob) {
      data.arrayBuffer().then(enqueueAudioData).catch(() => {});
    }
  }

  function handleServerMessage(msg) {
    switch (msg.type) {
      case 'ready':
        onReady(msg);
        break;
      case 'transcript':
        onText('transcript', msg);
        break;
      case 'translation':
        onText('translation', msg);
        break;
      case 'done':
        finalizePane('transcript');
        finalizePane('translation');
        updateStatusMeta();
        break;
      case 'error':
        onServerError(msg);
        break;
      default:
        console.debug('[rti] 未识别的消息类型', msg);
        break;
    }
  }

  function onReady(msg) {
    state.wsReady = true;
    window.clearTimeout(state.wsConnectTimer);
    state.wsConnectTimer = 0;
    state.readyInfo = {
      model: msg.model || (state.config && state.config.model) || '',
      voice: msg.voice || getVoice() || ''
    };
    // 只有收到 ready 后才允许发送麦克风音频帧
    if (state.workletNode) {
      try {
        state.workletNode.port.postMessage({ type: 'start' });
      } catch (err) {
        console.warn('[rti] 无法启动 AudioWorklet 采集', err);
      }
    }
    if (state.playCtx) resetPlaybackQueue(state.playCtx.currentTime);
    setStatus('live', '同传中', composeLiveMeta());
    if (els.hint) els.hint.textContent = '正在同传，直接说中文即可。';
  }

  function onText(kind, msg) {
    ensureLiveStatus();
    if (kind === 'transcript' && msg.final === true) {
      state.turnStartAt = performance.now();
    }
    appendText(kind, typeof msg.text === 'string' ? msg.text : '', msg.final === true);
  }

  function onServerError(msg) {
    const text = msg && msg.message ? String(msg.message) : '服务端返回未知错误';
    setStatus('error', '错误', text);
    showNotice('服务端错误：' + text, 'error');
    if (els.hint) els.hint.textContent = '发生错误，可点击「停止同传」后重试。';
  }

  /* ============================== 麦克风采集 ============================== */

  function getAudioCtor() {
    return window.AudioContext || window.webkitAudioContext;
  }

  function resumeContext(ctx) {
    if (!ctx) return;
    if (ctx.state === 'suspended') {
      try { ctx.resume().catch(() => {}); } catch (err) { /* ignore */ }
    }
  }

  function storedVolume() {
    const value = parseFloat(localStorage.getItem(LS_VOLUME) || '1.0');
    if (!Number.isFinite(value)) return 1.0;
    return Math.max(0.3, Math.min(3.0, value));
  }

  function setPlaybackVolume(value) {
    if (!Number.isFinite(value)) return;
    const clamped = Math.max(0.3, Math.min(3.0, value));
    try { localStorage.setItem(LS_VOLUME, String(clamped)); } catch (err) { /* ignore */ }
    if (els.volumeInput) els.volumeInput.value = String(clamped);
    if (state.playGain && state.playCtx) {
      try {
        state.playGain.gain.setTargetAtTime(clamped, state.playCtx.currentTime, 0.01);
      } catch (err) {
        state.playGain.gain.value = clamped;
      }
    }
  }

  function ensurePlaybackGain(ctx) {
    if (!ctx) return null;
    if (!state.playGain || state.playGain.context !== ctx) {
      state.playGain = ctx.createGain();
      state.playGain.gain.value = storedVolume();
      state.playGain.connect(ctx.destination);
    }
    return state.playGain;
  }

  // 同步创建播放上下文（在点击手势内调用，iOS Safari 才能正常起播）
  function ensurePlaybackContext() {
    const Ctor = getAudioCtor();
    if (!Ctor) throw new Error('当前浏览器不支持 Web Audio');
    if (!state.playCtx || state.playCtx.state === 'closed') {
      let ctx;
      try {
        // 优先创建 24k 上下文；Safari 可能忽略该参数，之后按实际 sampleRate 重采样
        ctx = new Ctor({ sampleRate: PLAYBACK_RATE, latencyHint: 'interactive' });
      } catch (err) {
        ctx = new Ctor();
      }
      state.playCtx = ctx;
      state.nextPlayTime = 0;
      state.scheduledSources = [];
      state.playGain = null;
    }
    ensurePlaybackGain(state.playCtx);
    resumeContext(state.playCtx);
  }

  // 同步创建采集上下文（必须在用户手势内创建，再等待 getUserMedia 授权）
  function createCaptureContext() {
    const Ctor = getAudioCtor();
    if (!Ctor) throw new Error('当前浏览器不支持 Web Audio');
    if (!state.captureCtx || state.captureCtx.state === 'closed') {
      state.captureCtx = new Ctor();
    }
    resumeContext(state.captureCtx);
    return state.captureCtx;
  }

  function stopTracks(stream) {
    if (!stream) return;
    stream.getTracks().forEach((track) => {
      try { track.stop(); } catch (err) { /* ignore */ }
    });
  }

  async function openMicStream() {
    if (!navigator.mediaDevices || typeof navigator.mediaDevices.getUserMedia !== 'function') {
      throw new Error('当前浏览器不支持麦克风采集');
    }
    const selectedId = (els.deviceSelect && els.deviceSelect.value) || storedDevice();
    const baseConstraints = {
      channelCount: 1,
      echoCancellation: false,
      noiseSuppression: true,
      autoGainControl: true
    };
    const audioConstraints = Object.assign({}, baseConstraints);
    if (selectedId) audioConstraints.deviceId = { exact: selectedId };

    let stream = null;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: audioConstraints, video: false });
    } catch (err) {
      const recoverable = selectedId && (err.name === 'OverconstrainedError' || err.name === 'NotFoundError');
      if (!recoverable) throw err;
      stream = await navigator.mediaDevices.getUserMedia({ audio: baseConstraints, video: false });
      if (els.deviceSelect) els.deviceSelect.value = '';
    }
    if (!state.running) {
      // 授权期间用户已点击停止
      stopTracks(stream);
      return;
    }
    state.micStream = stream;
    // 授权后重新枚举设备，拿到设备名称
    refreshDevices(false).catch(() => {});
  }

  async function buildCaptureGraph() {
    const ctx = state.captureCtx;
    if (!ctx || !state.micStream) throw new Error('麦克风或音频上下文未就绪');
    if (typeof window.AudioWorkletNode === 'undefined') {
      throw new Error('当前浏览器不支持 AudioWorklet，无法做 16kHz 降采样');
    }
    if (!ctx.audioWorklet || typeof ctx.audioWorklet.addModule !== 'function') {
      throw new Error('当前浏览器不支持 AudioWorklet，无法做 16kHz 降采样');
    }
    await ctx.audioWorklet.addModule('./pcm-worklet.js');
    if (!state.running) return;

    const source = ctx.createMediaStreamSource(state.micStream);
    const node = new window.AudioWorkletNode(ctx, 'pcm16-capture', {
      numberOfInputs: 1,
      numberOfOutputs: 1,
      outputChannelCount: [1],
      processorOptions: { targetRate: UPLOAD_RATE, frameSamples: UPLOAD_FRAME_SAMPLES }
    });
    node.port.onmessage = onCaptureFrame;

    const muteGain = ctx.createGain();
    muteGain.gain.value = 0;
    source.connect(node);
    node.connect(muteGain);
    muteGain.connect(ctx.destination);

    state.captureSource = source;
    state.workletNode = node;
    state.muteGain = muteGain;
    resumeContext(ctx);
  }

  function onCaptureFrame(event) {
    const data = event && event.data;
    if (!(data instanceof ArrayBuffer) || data.byteLength !== UPLOAD_FRAME_BYTES) return;
    const ws = state.ws;
    if (!state.running || !state.wsReady || !ws || ws.readyState !== WebSocket.OPEN) return;
    if (ws.bufferedAmount > MAX_WS_BUFFERED_BYTES) return; // 网络拥塞时丢帧，防止延迟堆积
    try {
      ws.send(data);
    } catch (err) {
      return;
    }
    if (!state.firstFrameSent) {
      state.firstFrameSent = true;
      updateStatusMeta();
    }
  }

  /* ============================== 24k 音频播放 ============================== */

  function enqueueAudioData(buffer) {
    if (!(buffer instanceof ArrayBuffer) || buffer.byteLength < 2) return;
    const ctx = state.playCtx;
    if (!ctx || ctx.state === 'closed') return;
    if (buffer.byteLength % 2 === 1) {
      buffer = buffer.slice(0, buffer.byteLength - 1);
    }
    const sampleCount = buffer.byteLength >> 1;
    if (sampleCount === 0) return;
    const pcm = new Int16Array(buffer, 0, sampleCount);

    state.receivedAudioBytes += sampleCount * 2;
    const now = performance.now();
    if (state.firstPacketMs === null && state.running) {
      state.firstPacketMs = Math.max(0, Math.round(now - state.sessionStartedAt));
      ensureLiveStatus();
      updateStatusMeta();
    }
    if (state.turnStartAt) {
      state.lastTurnLatencyMs = Math.max(0, Math.round(now - state.turnStartAt));
      state.turnStartAt = 0;
    }

    // PCM16 → Float32（-1..1）
    let samples = new Float32Array(sampleCount);
    for (let i = 0; i < sampleCount; i += 1) {
      const v = pcm[i];
      samples[i] = v < 0 ? v / 32768 : v / 32767;
    }
    const outRate = ctx.sampleRate || PLAYBACK_RATE;
    if (outRate !== PLAYBACK_RATE) {
      // 24k → 设备采样率（Safari 无法指定 24k 时走这里）
      samples = resampleLinear(samples, PLAYBACK_RATE, outRate);
    }
    if (samples.length === 0) return;

    let audioBuffer;
    try {
      audioBuffer = ctx.createBuffer(1, samples.length, outRate);
    } catch (err) {
      return;
    }
    if (typeof audioBuffer.copyToChannel === 'function') {
      audioBuffer.copyToChannel(samples, 0);
    } else {
      audioBuffer.getChannelData(0).set(samples);
    }

    const source = ctx.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(ensurePlaybackGain(ctx) || ctx.destination);

    const nowCtx = ctx.currentTime;
    let startAt = state.nextPlayTime;
    if (startAt < nowCtx + 0.005 || startAt > nowCtx + MAX_QUEUE_AHEAD_SEC) {
      // 队列空/已断流或积压过多：重置调度，重新加 ~40ms 缓冲
      resetPlaybackQueue(nowCtx);
      startAt = nowCtx + PLAYOUT_BUFFER_SEC;
    }
    try {
      source.start(startAt);
    } catch (err) {
      return;
    }
    state.nextPlayTime = startAt + audioBuffer.duration;
    state.scheduledSources.push(source);
    source.onended = () => removeScheduledSource(source);
  }

  function removeScheduledSource(source) {
    const index = state.scheduledSources.indexOf(source);
    if (index >= 0) state.scheduledSources.splice(index, 1);
    try { source.disconnect(); } catch (err) { /* ignore */ }
  }

  function resetPlaybackQueue(now) {
    const sources = state.scheduledSources;
    state.scheduledSources = [];
    for (let i = 0; i < sources.length; i += 1) {
      try { sources[i].stop(); } catch (err) { /* ignore */ }
      try { sources[i].disconnect(); } catch (err) { /* ignore */ }
    }
    state.nextPlayTime = now;
  }

  function resampleLinear(input, inRate, outRate) {
    const ratio = inRate / outRate;
    const outLength = Math.max(1, Math.round(input.length / ratio));
    const output = new Float32Array(outLength);
    const last = input.length - 1;
    for (let i = 0; i < outLength; i += 1) {
      const pos = i * ratio;
      const i0 = Math.floor(pos);
      const frac = pos - i0;
      const s0 = input[i0 <= last ? i0 : last];
      const i1 = i0 + 1 <= last ? i0 + 1 : last;
      const s1 = input[i1];
      output[i] = s0 + (s1 - s0) * frac;
    }
    return output;
  }

  /* ============================== 会话生命周期 ============================== */

  function onPrimaryClick() {
    if (state.blocked) {
      showNotice('当前环境不支持同传所需能力，请更换浏览器或改用 HTTPS 打开。', 'error');
      return;
    }
    if (state.running) {
      stopSession('user');
      return;
    }
    startSession();
  }

  async function startSession() {
    if (state.running) return;
    const typedKey = els.apiKeyInput ? els.apiKeyInput.value.trim() : '';
    if (typedKey) {
      const saved = await saveApiKeyIfAny();
      if (!saved) return;
    }
    const voice = getVoice();
    if (!voice || voice.toLowerCase() === 'tina') {
      showNotice('请先进入「音色制作」页录制/保存音色，再开始同传。', 'error');
      openSettings(true);
      focusEnrollment();
      return;
    }
    if (state.config && state.config.has_key === false) {
      showNotice('请先在设置里填写 API Key。', 'error');
      openSettings(true);
      focusEnrollment();
      return;
    }
    state.running = true;
    state.readyInfo = null;
    state.firstPacketMs = null;
    state.lastTurnLatencyMs = null;
    state.receivedAudioBytes = 0;
    state.firstFrameSent = false;
    state.sessionStartedAt = performance.now();
    clearNotice();
    clearPanes();
    updateButton();
    setStatus('connecting', '连接中', '正在准备麦克风…');
    if (els.hint) els.hint.textContent = '正在准备麦克风与音频通道…';

    try {
      // 两个 AudioContext 必须在点击手势内同步创建，iOS Safari 才会允许起播/采集
      ensurePlaybackContext();
      createCaptureContext();
      await openMicStream();
      if (!state.running) return; // 用户在授权期间已取消
      await buildCaptureGraph();
      if (!state.running) return;
      setStatus('connecting', '连接中', '正在连接服务器…');
      connectSocket();
    } catch (err) {
      if (!state.running) return; // 用户在启动过程中已点击停止，忽略后续错误
      console.error('[rti] 启动失败', err);
      teardownSession();
      setStatus('error', '错误', '启动失败');
      showNotice(startErrorMessage(err), 'error');
      if (els.hint) els.hint.textContent = '启动失败：请检查麦克风权限与后端服务后重试。';
    }
  }

  function failSession(err) {
    console.error('[rti] 会话错误', err);
    teardownSession();
    setStatus('error', '错误', '启动失败');
    showNotice(startErrorMessage(err), 'error');
    if (els.hint) els.hint.textContent = '启动失败：请检查麦克风权限与后端服务后重试。';
  }

  function stopSession(kind) {
    teardownSession();
    setStatus('idle', '未连接', kind === 'pagehide' ? '页面已离开，连接已断开' : '已停止');
    if (els.hint) els.hint.textContent = '点击「开始同传」重新开始。';
    if (kind === 'user') toast('已停止同传');
  }

  function teardownSession() {
    state.running = false;
    state.wsReady = false;
    state.firstFrameSent = false;
    state.readyInfo = null;
    state.turnStartAt = 0;
    window.clearTimeout(state.wsConnectTimer);
    state.wsConnectTimer = 0;

    const ws = state.ws;
    state.ws = null;
    if (ws) {
      try {
        if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'stop' }));
      } catch (err) { /* ignore */ }
      try { ws.close(1000, 'client stop'); } catch (err) { /* ignore */ }
    }

    if (state.workletNode) {
      try { state.workletNode.port.postMessage({ type: 'stop' }); } catch (err) { /* ignore */ }
      try { state.workletNode.port.onmessage = null; } catch (err) { /* ignore */ }
      try { state.workletNode.disconnect(); } catch (err) { /* ignore */ }
      state.workletNode = null;
    }
    if (state.captureSource) {
      try { state.captureSource.disconnect(); } catch (err) { /* ignore */ }
      state.captureSource = null;
    }
    if (state.muteGain) {
      try { state.muteGain.disconnect(); } catch (err) { /* ignore */ }
      state.muteGain = null;
    }
    if (state.micStream) {
      stopTracks(state.micStream);
      state.micStream = null;
    }
    if (state.captureCtx) {
      const captureCtx = state.captureCtx;
      state.captureCtx = null;
      captureCtx.close().catch(() => {});
    }
    if (state.playCtx) {
      try { resetPlaybackQueue(state.playCtx.currentTime); } catch (err) { /* ignore */ }
      try { state.playCtx.suspend().catch(() => {}); } catch (err) { /* ignore */ }
    }
    updateButton();
  }

  function updateButton() {
    if (!els.startBtn) return;
    const running = state.running;
    els.startBtn.classList.toggle('is-stop', running);
    els.startBtn.setAttribute('aria-pressed', running ? 'true' : 'false');
    if (els.startBtnLabel) els.startBtnLabel.textContent = running ? '停止同传' : '开始同传';
    document.body.classList.toggle('is-live', running);
  }

  /* ============================== Service Worker ============================== */

  function registerServiceWorker() {
    if (!('serviceWorker' in navigator) || !window.isSecureContext) return;
    window.addEventListener('load', () => {
      navigator.serviceWorker
        .register(SW_PATH)
        .then((registration) => {
          console.info('[rti] Service Worker 已注册：', registration.scope);
        })
        .catch((err) => {
          console.warn('[rti] Service Worker 注册失败（不影响使用）', err);
        });
    });
  }

  /* ============================== 启动 ============================== */

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();



