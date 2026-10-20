# Qwen3.8-Omni 直达实测（2026-10-19）

## 条件

- 输入：Windows SAPI `Microsoft Huihui Desktop`（zh-CN）合成中文，22.05kHz/16bit/mono WAV；脚本重采样到 16kHz PCM16。
- 模型：`qwen3.8-omni-flash-realtime`，`voice=Tina`，manual 模式（`input_audio_buffer.commit` + `response.create`），instructions 要求"只输出英文翻译"。
- 地域：华北 2（北京），使用通用 WebSocket 地址（未填 Workspace ID）。
- 输出：24kHz/16bit/mono WAV。

## 延迟（句末 → 第一个音频包）

| # | 句子 | speech_end→first_audio | connect→first_audio |
|---|---|---:|---:|
| 1 | 我们今天讨论一下供应链的预算问题。 | 797ms | 1219ms |
| 2 | 这个项目的预算是三百万美元，下周一之前必须完成。 | 766ms | 1110ms |
| 3 | 同上 | 859ms | 2234ms |
| 4 | 同上 | 828ms | 1172ms |
| 5 | 同上 | 781ms | 1203ms |

- **p50 = 797ms，p95 ≈ 859ms，min 766ms，max 859ms**。
- `connect→first_audio` 首次含 WebSocket 握手，约 1.1–1.2s；有一次 2.2s 抖动，属连接/调度异常，不代表模型推理。

## 译文

| 中文 | 英文（Omni 输出） |
|---|---|
| 我们今天讨论一下供应链的预算问题。 | Let's talk about supply chain budgeting today. |
| 这个项目的预算是三百万美元，下周一之前必须完成。 | The budget for this project is three million dollars, and it must be completed by next Monday. |

第二句数字（三百万/下周一）准确；第一句把"讨论"意译为"talk about"，可接受。

## 输出音频

| 文件 | 采样率 | 声道 | 时长 |
|---|---:|---:|---:|
| `results/omni_reply1.wav` | 24000 | 1 | 2.88s |
| `results/omni_reply2.wav` | 24000 | 1 | 6.00s |

## 声音复刻实测（2026-10-19 20:43）

- 样本：`voice.wav`，15.0s，48kHz/16bit/mono，peak 0.1667，rms 0.0192，无削波。
- 复刻：`qwen-voice-enrollment`，`target_model=qwen3.8-omni-flash-realtime`，`preferred_name=myvoice`。
- 音色 ID：`qwen-omni-vc-myvoice-...`。
- 复刻后延迟：

| 句子 | speech_end→first_audio | connect→first_audio |
|---|---:|---:|
| 我们今天讨论一下供应链的预算问题。 | 968ms | 1234ms |
| 这个项目的预算是三百万美元，下周一之前必须完成。 | 937ms | 1219ms |

- 输出：`results/omni_clone_reply1.wav`（3.28s）、`results/omni_clone_reply2.wav`（7.04s），24kHz/16bit/mono。
- A/B 试听：`results/voice_ab.wav`（原声 15s + 0.5s 静音 + 克隆英文句 2，48kHz）。
- 与 Tina 相比，克隆音色首音慢约 100–170ms，属音色条件注入开销；仍 <1s。
- **SECS 尚未计算**（需要 CAM++/ECAPA 说话人嵌入）；目前以主观试听为准。

## 成本

- 音频 25 token/秒；北京音频入 ¥6/M、音频出 ¥12/M、文本出 ¥4.5/M。
- 1 小时 ≈ **¥1.69**（入 ¥0.54 + 出 ¥1.08 + 文本 ¥0.07）。
- 声音复刻 ¥0.01/音色，90 天内 1000 次免费。

## 本地 Web 应用实测（2026-10-19）

| 路径 | 句末/上轮 → 首音 | 结果 |
|---|---:|---|
| 本地网关 WebSocket smoke（无浏览器） | 703ms | 中文识别、英文翻译、24k 音频均正确 |
| 浏览器 + 假麦克风（最终前端） | 738ms | 双栏文本正确，音频 6.3s，无 JS 异常 |
| 浏览器 + 设置面板配置 Key 流程 | 370ms | 无预置 Key 时通过设置启动成功 |
| 桌面 exe `/api/health`、静态页 | — | 启动成功，页面/JS 正常返回 |

- 浏览器测试用 Chrome headless + `--use-file-for-fake-audio-capture`，不是真人麦克风。
- 手机 HTTPS 入口已渲染通过（自签名证书 + 局域网 IP）；未在真实 iPhone/Android 上测麦克风与音频输出。
- 桌面 exe：`dist/RTI/RTI.exe`；免安装包：`dist/RTI-windows.zip`。

## 专业音色 + 只翻译不对答（2026-10-20）

- **问题复现**：旧版只依赖 `session.instructions`，直接对 Omni 说问题/命令时模型会回答，例如“今天天气怎么样？”被回答成 I can't check live weather；克隆音色下更严重，甚至用中文回答。
- **根因**：`server_vad` 自动创建回复，`session.instructions` 对复杂输入约束力不足。
- **修复**：`turn_detection={"type":"server_vad","create_response":false}` 只切句；网关收到 `input_audio_buffer.speech_stopped` 后主动 `response.create`，在 `response.instructions` 中强制“翻译引擎，不是助手；问题/命令只翻译不执行”。
- **验证**：今天天气怎么样/请写诗/解释量子力学三类输入，Omni 均只输出英文翻译：
  - `How is the weather today?`
  - `Please write me a poem about spring.`
  - `Please explain quantum mechanics.`
- **专业音色**：用干净 15 秒 `voice.wav` 创建 CosyVoice 3.5-plus 复刻：
  - 音色 ID：`cosyvoice-v3.5-plus-myvoice-...`
  - 上传走 48h 临时 OSS，复刻开启 `enable_volume_normalization=true`
  - Omni 只出文本，CosyVoice 3.5 流式 TTS 输出 24k PCM；网关实测语音结束→首音 **359–594ms**
  - 输出音频峰值约 0.39–0.65、RMS 约 0.06，比旧 Omni 复刻音频更响、更稳定
- **说明**：没有客观 SECS（本机无说话人嵌入模型），相似度以 CosyVoice 3.5-plus 的高相似度模型能力 + 干净样本 + 归一化保证；可在音色制作页试听 A/B。

## 未测 / 限制

1. **SECS 音色相似度**未计算；需要 CAM++/ECAPA 说话人嵌入，当前只有主观 A/B 试听。
2. 输入是 TTS 合成音或文件回放，不是真实麦克风连续流；未测 VAD/抢话/长时稳定性。
3. 只测了 2 句短句；未测长段落、术语表、数字密集场景。
4. 通用 WebSocket 地址可通；官方建议生产使用 `wss://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/...`，需补 Workspace ID。

## 复现命令

```powershell
cd E:\文档\realtime-interpreter
$env:DASHSCOPE_API_KEY="sk-..."
.\.venv\Scripts\python.exe scripts\omni_roundtrip.py `
  --wav fixtures\zh_test2.wav --out results\omni_reply2.wav --voice Tina --timeout 45
```
