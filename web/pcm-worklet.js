/*
 * 实时同传 · 中→英 — 麦克风采集 AudioWorklet
 *
 * 运行在 AudioWorkletGlobalScope：
 *   - 输入：麦克风原始采样（任意采样率，通常 44.1k / 48k，单声道）
 *   - 输出：不产生可听声音（process 输出静音），仅通过 port 向主线程发送
 *           16000Hz / 单声道 / PCM16 little-endian、每帧 320 samples（640 bytes）的 ArrayBuffer。
 *
 * 降采样：流式线性插值。主线程在收到服务端 {"type":"ready"} 后才 postMessage({type:"start"})，
 * 在此之前不产生任何音频帧。
 *
 * 语法检查：node --check web/pcm-worklet.js
 * （AudioWorkletProcessor / registerProcessor / sampleRate 是 AudioWorkletGlobalScope 的运行时全局）
 */

/* global AudioWorkletProcessor, registerProcessor, sampleRate */

class PCM16CaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const opts = (options && options.processorOptions) || {};
    const targetRate = Number(opts.targetRate) > 0 ? Number(opts.targetRate) : 16000;
    const frameSamples = Number(opts.frameSamples) > 0 ? Math.floor(Number(opts.frameSamples)) : 320;

    this.inRate = typeof sampleRate === 'number' && sampleRate > 0 ? sampleRate : 48000;
    this.targetRate = targetRate;
    this.frameSamples = frameSamples;
    this.ratio = this.inRate / targetRate; // 每个输出采样对应的输入采样数

    this.active = false;
    this.prev = 0;      // 上一个输入采样
    this.pos = 0;       // 输出点位于 prev..cur 之间的位置（0..1）
    this.frame = new Float32Array(frameSamples);
    this.frameCount = 0;

    this.port.onmessage = (event) => {
      const msg = event && event.data ? event.data : {};
      if (msg.type === 'start') {
        this.active = true;
        this.reset();
      } else if (msg.type === 'stop') {
        this.active = false;
        this.reset();
      }
    };
  }

  reset() {
    this.prev = 0;
    this.pos = 0;
    this.frameCount = 0;
  }

  flushFrame() {
    const n = this.frameSamples;
    const pcm = new Int16Array(n);
    const frame = this.frame;
    for (let i = 0; i < n; i += 1) {
      let s = frame[i];
      if (s > 1) s = 1;
      else if (s < -1) s = -1;
      pcm[i] = s < 0 ? Math.round(s * 32768) : Math.round(s * 32767);
    }
    this.frameCount = 0;
    // 转移 ArrayBuffer 所有权，避免复制
    try {
      this.port.postMessage(pcm.buffer, [pcm.buffer]);
    } catch (err) {
      this.port.postMessage(pcm.buffer);
    }
  }

  emitSample(value) {
    this.frame[this.frameCount] = value;
    this.frameCount += 1;
    if (this.frameCount >= this.frameSamples) {
      this.flushFrame();
    }
  }

  process(inputs, outputs) {
    // 输出静音：本节点只负责采集，播放走独立的 24k AudioContext。
    const outputs0 = outputs && outputs[0];
    if (outputs0 && outputs0.length) {
      for (let c = 0; c < outputs0.length; c += 1) {
        const channel = outputs0[c];
        if (channel && typeof channel.fill === 'function') channel.fill(0);
      }
    }
    if (!this.active) return true;

    const input = inputs && inputs[0];
    if (!input || input.length === 0) return true;
    const channel = input[0];
    if (!channel || channel.length === 0) return true;

    const ratio = this.ratio;
    for (let i = 0; i < channel.length; i += 1) {
      const cur = channel[i];
      // 在 prev(0) .. cur(1) 之间按 ratio 步进产生输出采样
      while (this.pos < 1) {
        const v = this.prev + (cur - this.prev) * this.pos;
        this.emitSample(v);
        this.pos += ratio;
      }
      this.pos -= 1;
      this.prev = cur;
    }
    return true;
  }
}

registerProcessor('pcm16-capture', PCM16CaptureProcessor);
