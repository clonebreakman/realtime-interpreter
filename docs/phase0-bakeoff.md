# Phase 0：供应商 bake-off 执行手册

目标：用同一组语料、同一说话人、同一网络，测出"首音延迟 / 音色相似度 / 翻译质量 / 成本 / 稳定性"，选出 1 主 + 1 备供应商。

## 1. 语料

- 30 句中文：日常 10、商务 10、技术 10，每句 8–20 秒。
- 同一人朗读，安静房间，48kHz/16bit WAV；同时保留 30 秒声音注册样本。
- 固定文本写入 `fixtures/phase0_sentences.tsv`（编号、领域、中文、参考英文）。

## 2. 被测系统

| # | 系统 | 前置条件 | 重点 |
|---|---|---|---|
| 1 | 阿里百炼 `qwen3.8-livetranslate-flash-realtime` | API Key；RPM 10 需提额 | `enable_voice_clone=once` 的效果；首音；¥/h |
| 2 | 腾讯云实时语音翻译 | AppID/Secret；一句话复刻 | `voice_type="200000000"` + `fast_voice_type`；首音；并发 5 |
| 3 | 讯飞同声传译 | appid/APIKey | 无克隆基线；VAD 手感 |
| 4 | Camb.ai Realtime | Key；先建 cloned voice | `fast` 是否支持 zh→en；Beta 稳定性 |
| 5 | Azure Live Interpreter + Personal Voice | Personal Voice 审批 | 审批门槛；$2.5/h；SECS |
| 6 | Palabra.ai | Key；$50 试用 | 320ms 分片实际延迟；$0.04/min |
| 7 | 全链路国产级联：百炼 `qwen3-asr-flash-realtime` + DeepSeek `deepseek-flash` + 百炼 `cosyvoice-v3.5-flash` | 百炼 Key + DeepSeek Key | 首音；SECS；¥/h |
| 8 | 备选 TTS：SiliconFlow CosyVoice2 | 硅基流动 Key | 更便宜，音质是否够用 |
| 9 | 百炼 Qwen3.8-Omni 直达 + 声音复刻 | 开通模型；Workspace ID 可选 | 复刻已跑通；Tina/克隆首音 766–968ms；SECS 待测 |

## 3. 指标与打点

- **首音延迟**：客户端单调时钟，句末 → 第一个下行音频包；p50/p95。
- **音色相似度 SECS**：CAM++/ECAPA 对注册样本与译音做说话人嵌入余弦。
- **翻译质量 COMET**：参考英文与译文；同时人工盲听 1–5 分。
- **术语命中**：20 条术语表，逐条检查。
- **成本**：按厂商账单折算 ¥/小时；DeepSeek 用 `pipeline.cost` 计算。
- **稳定性**：连续 30 分钟，记录断流/重连/错误码。

## 4. 命令

```powershell
# Mock 自检
.\.venv\Scripts\python.exe scripts\bakeoff.py --provider mock --duration 3 --no-realtime

# 真实 WAV（适配器实现后）
.\.venv\Scripts\python.exe scripts\bakeoff.py --provider aliyun --wav fixtures\zh_30s.wav --out results\aliyun.jsonl

# DeepSeek 单句延迟
$env:DEEPSEEK_API_KEY="sk-..."
.\.venv\Scripts\python.exe scripts\translate_once.py "我们今天讨论一下供应链的预算问题。"
```

## 5. 评分表

| 系统 | 首音 p50 | 首音 p95 | SECS | COMET | 术语 | ¥/h | 30min 稳定性 | 开通难度 | 结论 |
|---|---:|---:|---:|---:|---:|---:|---|---|---|
| 阿里 livetranslate | | | | | | | | | |
| 腾讯实时翻译 | | | | | | | | | |
| 讯飞同传 | | | | | | | | | |
| Camb Realtime | | | | | | | | | |
| Azure + Personal Voice | | | | | | | | | |
| Palabra | | | | | | | | | |
| 级联 + DeepSeek | | | | | | | | | |

## 6. 决策门槛

- 首音 p50 ≤ 2.0s（国内目标 ≤1.5s）；
- SECS ≥ 0.72；
- COMET 不低于最优系统 0.05；
- 成本 ≤ 预算上限（国内 ≤¥30/h，海外 ≤$10/h）；
- 30 分钟无致命断流；
- 合规可接受（音频是否出境、克隆审批、商用条款）。

满足以上条件后，选 **1 主 + 1 备**，进入 Phase 1。
