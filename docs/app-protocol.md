# 本地 Web 应用协议（桌面 / 手机 PWA）

## 架构

```
浏览器（电脑 / 手机）
  麦克风 48k → AudioWorklet 降采样 16k PCM16 → WebSocket 二进制
        ⇅
  FastAPI 网关（本机 127.0.0.1:8765 / 局域网 HTTPS）
        ⇅
  Qwen3.8-Omni-Flash-Realtime（服务端 VAD，音频进/音频出）
```

- **API Key 保存到本机 `config.json`**：前端设置面板 POST 给本机后端，后端写盘（明文），下次启动自动加载；不写入浏览器 localStorage。
- 桌面端由设置面板保存；Android 端保存在 SharedPreferences（明文，仅本机）。
- **音频不经过文件**：上行 PCM16 16k 20ms 帧；专业音色模式下 Omni 只出译文文本，后端用 CosyVoice 3.5 流式 TTS 生成 24k PCM 下发；浏览器 AudioContext 排队播放。
- **桌面页面拆成两个**：`index.html` 同传；`studio.html` 音色制作。同传默认使用 `pro_voice`（CosyVoice 3.5），不再强制录音。
- **手机**：
  - **原生 APK（推荐，独立）**：`dist/RTI-android.apk`，`MainActivity` 同传 + `VoiceStudioActivity` 音色制作；Omni 文本 + CosyVoice TTS；API Key 存在手机 SharedPreferences。
  - **PWA（备用）**：同一套 `web/`，通过 HTTPS 打开后可「添加到主屏幕」，但需要电脑跑后端。

## HTTP

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | `{"ok":true,"model":"...","voice":"...","pro_voice":"...","has_key":bool}` |
| GET | `/api/config` | 同上（不含 Key 明文） |
| POST | `/api/settings` | `{"api_key":"...","voice":"...","pro_voice":"..."}`；写入 config.json，空值不覆盖 |
| POST | `/api/enroll` | `{"audio_base64":"...","mime_type":"audio/wav","profile":"self|peer","name":"..."}`；创建 Omni 复刻 + CosyVoice 3.5 专业音色，保存录音 |
| GET | `/api/voices` | `{"default":"...","pro_voice":"...","peer_voice":"...","peer_pro_voice":"...","voices":[...]}` |
| POST | `/api/voices/default` | `{"voice":"...","profile":"self|peer"}`；切默认音色 |
| GET | `/`、`/studio.html`、`/duo.html` | 同传页 / 音色制作页 / 双向对谈页 |

## WebSocket `/ws`

客户端 → 服务端：

| 帧 | 内容 |
|---|---|
| 文本 | `{"type":"start","profile":"self|peer","voice":"<id 或空>","collect":true}`，连接后一次 |
| 二进制 | PCM16LE / 16000Hz / 单声道 / 320 samples（640B） |
| 文本 | `{"type":"stop"}` |
| 文本 | `{"type":"ping"}` → 服务端回 `pong` |

服务端 → 客户端：

| 帧 | 内容 |
|---|---|
| 文本 | `{"type":"ready","model":"...","voice":"...","profile":"self|peer","pro_voice":"...","tts_language":"en|zh"}` |
| 文本 | `{"type":"transcript","text":"...","final":false}` 中文增量；`final=true` 为整句 |
| 文本 | `{"type":"translation","text":"...","final":false}` 英文增量 |
| 文本 | `{"type":"done"}` 一轮回复结束（专业 TTS 合成完成后才发） |
| 文本 | `{"type":"error","message":"..."}` |
| 二进制 | PCM16LE / 24000Hz / 单声道，直接播放（专业音色模式下由 CosyVoice 3.5 产生） |

- 服务端 VAD 使用 `{"type":"server_vad","create_response":false}`，只负责切句；收到 `input_audio_buffer.speech_stopped` 后，网关主动发送 `response.create`，并携带“翻译引擎，不是助手；问题/命令只翻译不执行”的指令，避免模型对答。
- 专业音色模式：`pro_voice` 存在时 Omni `modalities=["text"]`，只出译文文本；网关把整轮译文交给 CosyVoice 3.5 流式 TTS，合成 24k PCM 下发。`profile="self"` 用 `pro_voice` + 英文 TTS；`profile="peer"` 用 `peer_pro_voice` + 中文 TTS，Omni 指令为英文→中文。
- 双向对谈：前端打开两个 `/ws`，分别发送 `profile=self` / `peer`；两个独立输入设备、两个音色、两个播放通道。`collect=true` 时服务端按通道保存语音段，累计约 30 秒后后台重新创建专业音色并更新后续 TTS。
- 没有预先录制时使用默认专业音色（`pro_voice` / 预置 CosyVoice ID）；不阻塞对谈。对谈中累计约 30 秒语音后，`collect=true` 的通道会自动复刻并替换后续 TTS voice。
- `final=true` 下发整句，前端按“增量追加”处理，不会重复。
- 音频为 float32 → PCM16 转换后传输；前端按 24k 播放，若设备不支持 24k 则线性重采样；设置面板可调播放音量。

## 延迟（实测）

| 路径 | 句末/语音结束 → 首音 |
|---|---:|
| 专业 CosyVoice 3.5 + Omni 文本（网关） | **359–594ms** |
| 本地 WebSocket 网关 + Omni 音频旧路径 | 703ms |
| 浏览器（假麦克风，Omni 旧路径） | 738ms |
| Omni 文件往返（Tina） | 766–859ms |
| Omni 文件往返（复刻音色） | 937–968ms |

浏览器播放队列默认 40ms 缓冲；超过 2s 积压会重置，避免延迟滚雪球。

## 打包 / 运行

```powershell
# 源码运行
.\.venv\Scripts\python.exe -m rti.server

# 桌面 exe
scripts\build_desktop.py      # -> dist\RTI\RTI.exe

# 手机（局域网 HTTPS）
scripts\make_cert.py --ip <电脑局域网IP>
.\.venv\Scripts\python.exe -m rti.server --host 0.0.0.0 `
  --ssl-keyfile certs\key.pem --ssl-certfile certs\cert.pem
# 手机安装 certs\cert.pem 为受信任证书，打开 https://<IP>:8765/
```

## Android 独立版

- APK：`dist/RTI-android.apk`，包名 `com.rti.interpreter`，minSdk 26 / target 34。
- **三个 Activity**：`MainActivity` 同传；`VoiceStudioActivity` 30 秒录音/试听/分析/保存；`DuoActivity` 双通道对谈（两个 AudioRecord、两个 Omni 文本 WS、两个 CosyVoice TTS，输入设备用 Spinner 选择）。
- 对谈默认音色：没有初始录音时两个通道都先用预置专业音色；桌面版会在 30 秒后自动复刻，Android 当前先用输入框里的音色 ID（可在桌面录好后填入）。
- 会话：`session.update` 用 `modalities:["text"]`、`turn_detection:{"type":"server_vad","create_response":false}`；收到 `input_audio_buffer.speech_stopped` 后主动发 `response.create`（携带翻译指令），只取译文文本。
- 音频：`AudioRecord` 16k PCM16 → base64 → `input_audio_buffer.append`；译文本轮结束后用 CosyVoice 3.5 的 HTTP SSE 流式 TTS 合成 24k PCM，`AudioTrack` 播放。
- 安全：API Key 存在 Android `SharedPreferences`（明文，仅本机）。
- 构建：`python android/build_apk.py`（build-tools **35** + platform 34 + JDK；依赖含 Java-WebSocket + slf4j-api/nop；脚本直接调用 aapt2/javac/jar/d8/zipalign/jarsigner/apksigner，不用 Gradle/Studio）。
- 限制：两个 AudioRecord 能否同时打开取决于设备 ROM；对方输入建议用第二麦克风/蓝牙/USB 输入；自动增量复刻目前以桌面版为主。
- 未验证：本机无 Android 真机，仅校验签名、manifest、dex 类；安装/麦克风/播放需真机回归。

## 音色制作（独立）

- 桌面：`studio.html` 用 AudioWorklet 采 24k PCM（**30 秒**）→ 浏览器内封装 WAV → base64 POST `/api/enroll`；后端校验时长/峰值，保存为 `<profile>_voice_<时间戳>.wav`，同时创建：
  - `qwen-voice-enrollment` 的 Omni 兜底音色；
  - **CosyVoice 3.5 专业音色**（上传 48h 临时 OSS → `cosyvoice-v3.5-plus` 复刻，`enable_volume_normalization=true`）。
  - 录制对象可选“我的音色/对方音色”，分别写 `pro_voice` / `peer_pro_voice`；页面显示时长/峰值、可试听、可看音色 ID。
- 对谈自动优化：每个通道 `collect=true` 时，服务端保存语音段到 `profiles/<profile>/segments/`；累计约 30 秒后后台创建新专业音色，写入配置并替换当前会话后续 TTS voice。
- Android：`VoiceStudioActivity` 原生 `AudioRecord` 48k 录 **30 秒** → WAV → Omni 复刻 → SharedPreferences；专业音色默认使用预置 CosyVoice 3.5 ID。
- 同传默认使用 `pro_voice`；没有专业音色时才回退 Omni 音频。
- `config.json` 保存 `api_key`、`voice`、`pro_voice`、`peer_voice`、`peer_pro_voice`、`model`（明文）；Android SharedPreferences 保存对应字段（明文）。

## 双向对谈（桌面）

- 页面：`duo.html`；两个独立 `/ws`，`profile=self` 与 `profile=peer`。
- 输入：两个 `getUserMedia` 设备；对方设备建议用第二麦克风、系统“立体声混音”或会议 loopback。
- 输出：两个播放队列；建议戴耳机并把对方输出送到对方（浏览器默认同输出，实际会议需要声卡路由）。
- 自动采集：每个通道 `collect=true`，基于 speech_stopped 分段保存；约 30 秒后后台重做专业音色。
- 限制：当前浏览器同时采集两个设备受系统/驱动支持影响；手机双通道尚未做。

## 已知限制

- 手机版已提供原生 APK（两个 Activity）；iOS 仍需 macOS/Xcode 签名。
- 自签名证书需要手机手动信任；生产可换正式证书或反向代理。
- 未在 iOS/Android 真机实测；桌面 Chrome/Edge 与无头 Chrome 已测。
- API Key 与 voice/pro_voice/model 持久化在 `config.json`（桌面）或 SharedPreferences（Android），均为明文；`config.json` 不要分享给他人。
