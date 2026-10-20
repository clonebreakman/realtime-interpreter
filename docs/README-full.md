# realtime-interpreter

云端实时同声传译（中文 → 英文，保留本人音色）的 Phase 0 脚手架与调研资料。

**当前状态**：**53 个测试通过**；桌面和 Android 已增加 **双向对谈**（对方英文→中文/对方音色，我中文→英文/我的音色）；单向同传保留。默认我的专业音色为 **CosyVoice 3.5 专业复刻**；音色采集扩展到 **30 秒**，对谈中每累计约 30 秒新语音会自动增量复刻并更新后续 TTS。**同传只翻译、不对答**：每轮 `response.create` 附带“翻译引擎”指令，问题/命令只翻译、不执行。专业音色实测语音结束→首音约 **359–594ms**。实测记录见 [omni-measurements-2026-10-19.md](docs/omni-measurements-2026-10-19.md)。

> 音色说明：`config.json` 保存 `voice/pro_voice`（我的）和 `peer_voice/peer_pro_voice`（对方）。**没有预先录音也可以直接开始**：先用默认专业音色，对谈中累计约 30 秒对方语音后自动创建对方专业音色并替换；我的音色同理。

## 已交付（2026-10-19，2026-10-20 更新双向/专业音色）

| 交付物 | 路径 | 说明 |
|---|---|---|
| Windows 桌面版 | [dist/RTI/RTI.exe](dist/RTI/RTI.exe) | 单向同传 + 双向对谈 + 独立音色制作；CosyVoice 3.5 |
| 免安装 zip | [dist/RTI-windows.zip](dist/RTI-windows.zip) | 解压后运行 `RTI.exe`（37MB） |
| **Android 独立版** | [dist/RTI-android.apk](dist/RTI-android.apk) | 单向同传 + **双向对谈** + 专业音色 + 30 秒音色制作；Android 8+ 侧载 |
| 双向对谈页 | [web/duo.html](web/duo.html) | 双输入设备、双 WebSocket、双音色 |
| 手机 PWA（备用） | [web/index.html](web/index.html) | 与桌面共用后端；需要电脑在同一 Wi-Fi 跑服务 |
| 应用协议 | [docs/app-protocol.md](docs/app-protocol.md) | WebSocket / PCM / 音色接口 / 专业 TTS / 双向协议 |
| 实测记录 | [docs/omni-measurements-2026-10-19.md](docs/omni-measurements-2026-10-19.md) | 延迟、成本、复刻 A/B |

> 手机独立版是原生 APK（Java + 系统 AudioRecord/AudioTrack），不再需要电脑；PWA 只作为浏览器备用方案。

## 两条路线

### 路线 A：Omni 直达（首选，已实测）

```
麦克风 48k → 16k PCM16 → qwen3.8-omni-flash-realtime（单 WebSocket）
  → 英文语音 + 你的克隆音色（voice_id）→ playout buffer → VB-Cable / 耳机
```

- 单 API 完成识别+翻译+合成，音色用 `qwen-voice-enrollment` 从 10–20s 音频零样本复刻；
- 音频 25 token/秒：1h ≈ **¥1.69**（入 ¥6/M、出 ¥12/M、文本出 ¥4.5/M）；
- 声音复刻 ¥0.01/音色，90 天内 1000 次免费；模型免费额度 100 万 token（北京，90 天）；
- **已实测**：两条中文短句，句末→首音 766–859ms（p50 797ms），译文准确；你的 15s 样本复刻音色首音 937–968ms，A/B 试听 `results/voice_ab.wav`。

### 路线 B：云端级联（兜底/可控）

```
麦克风 48k → resample 16k PCM16
  └─ 百炼 qwen3-asr-flash-realtime
       └─ DeepSeek V4.1 Flash（翻译）
            └─ 百炼 cosyvoice-v3.5-flash（克隆）
                 └─ playout buffer → VB-Cable 虚拟麦 / 耳机
```

- 成本 ≈ **$0.70/h（≈¥5）**；ASR/TTS 音频留境内，翻译仅文本；
- 可插术语表、上下文、字幕与稳定前缀；Omni 不可用/效果不达标时降级到此路线。

## 快速开始

```powershell
cd E:\文档\realtime-interpreter
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe scripts\cost_report.py
.\.venv\Scripts\python.exe scripts\bakeoff.py --system cascade --duration 2 --no-realtime
.\.venv\Scripts\python.exe scripts\bakeoff.py --provider mock --duration 3 --no-realtime
.\.venv\Scripts\python.exe scripts\list_devices.py

# Omni 直达（Workspace ID 可选；生产建议填）
$env:DASHSCOPE_API_KEY="sk-..."
$env:BAILIAN_WORKSPACE_ID="ws-..."

# 录 15 秒人声，生成 voice.wav
.\.venv\Scripts\python.exe scripts\record_voice.py --seconds 15 --out voice.wav

# 用 voice.wav 创建复刻音色
.\.venv\Scripts\python.exe scripts\enroll_voice.py --audio voice.wav --name myvoice
$env:OMNI_VOICE="qwen-omni-vc-..."
.\.venv\Scripts\python.exe scripts\omni_roundtrip.py --wav fixtures\zh_test.wav --out reply.wav
```

> `--system cascade` 当前用 Fake ASR/TTS（模拟 150ms ASR + 100ms TTS），用于验证编排与打点；接上百炼 Key 后替换为真实适配器。

真实 DeepSeek 翻译（需要 `DEEPSEEK_API_KEY`）：

```powershell
$env:DEEPSEEK_API_KEY="sk-..."
.\.venv\Scripts\python.exe scripts\translate_once.py "我们今天讨论一下供应链的预算问题。"
```

## 录 10–20 秒声音样本

1. 安静房间，麦克风离嘴 10–20cm，正常语速连续说中文，不要长时间停顿。
2. 推荐直接跑脚本（自动生成 48kHz、单声道、16bit WAV）：
   ```powershell
   .\.venv\Scripts\python.exe scripts\record_voice.py --seconds 15 --out voice.wav
   ```
   看到 `peak` 大于 0.01 说明音量正常；如果提示音量过低，先跑 `--list` 看设备，再加 `--device N`。
3. 也可以不跑脚本：Windows 开始菜单搜「录音机」录 15 秒，或手机录音后传到电脑（WAV/MP3/M4A 均可）。
4. 录音只保存在本机；只有执行 `enroll_voice.py` 时才会上传到阿里云做音色复刻。

示例朗读（约 15 秒）：

> 大家好，我是这个项目的负责人。今天想测试一下实时同声传译，看看它能不能保留我的声音和说话节奏。接下来我会用正常语速说一段话，内容大概是产品预算、交付时间和下一步计划。希望翻译出来的英文听起来自然、清楚，而且像是我本人在说。

## Windows 桌面版 / Android 独立版 / PWA

### Windows 桌面版

```powershell
# 方案一：直接双击
dist\RTI\RTI.exe

# 方案二：源码运行
.\.venv\Scripts\python.exe -m rti.server
```

- 首次打开点「设置」，填 `DASHSCOPE_API_KEY`；离开输入框或点开始时**自动保存**到同目录 `config.json`，下次启动自动加载。
- **同传页**只负责实时翻译；默认使用 `pro_voice`（CosyVoice 3.5 专业音色）。每轮 VAD 结束后用 `response.create` 附带翻译指令，**问题/命令只翻译，不回答**。
- **音色制作页**：录音 **30 秒** → 显示时长/峰值、可试听 → 同时创建 Omni 复刻音色 + **CosyVoice 3.5 专业音色**；可切换“我的音色/对方音色”，两套音色分别保存。
- 点「开始同传」授权麦克风；中文原文、英文翻译实时显示；英文语音由 CosyVoice 3.5 专业音色合成，克隆音色偏小时可在设置里调播放音量。
- 重新打包：
  ```powershell
  .\.venv\Scripts\python.exe scripts\build_desktop.py
  # 输出 dist\RTI\RTI.exe
  ```

### 双向对谈（桌面）

入口：同传页右上角 **⇄**，或 [web/duo.html](web/duo.html)。

1. 可以直接开始（不预先录音）：对方先使用默认专业音色，对谈中累计约 30 秒对方语音后自动复刻；也可以在音色制作页分别录 **我的音色 30 秒 / 对方音色 30 秒**；
2. 打开双向对谈，给两个通道分别选输入设备：
   - 我方：你的麦克风（中文）；
   - 对方：第二麦克风 / 系统“立体声混音” / 会议 loopback（英文）。
3. 点「开始双向对谈」：
   - 对方英文 → 中文，用对方专业音色读中文；
   - 我的中文 → 英文，用我的专业音色读英文。
4. 建议戴耳机：你听中文，对方听英文；对方音频不要从你的扬声器回灌到你的麦克风。
5. 对谈中每个通道会持续保存语音片段；累计约 30 秒新语音后，后台自动重做 CosyVoice 专业音色，并用于后续轮次（无需手动重录）；没有初始录音时也会自动学习。

> Android 也已加入 **双向对谈（DuoActivity）**：两个输入设备、两个 Omni 文本通道、两个 CosyVoice TTS；手机双路 AudioRecord 是否可同时工作取决于设备/ROM。桌面/浏览器版仍是最稳的双向版本。

### Android 独立版（推荐手机用）

[dist/RTI-android.apk](dist/RTI-android.apk) 是原生 APK：

- **不依赖电脑**，手机直接连阿里云百炼；
- 首次打开填 API Key（保存在手机 SharedPreferences，下次自动加载）；
- **同传屏**默认使用 CosyVoice 3.5 专业音色；Omni 只出译文文本，CosyVoice 流式 TTS 出声；
- 点「音色制作」进入**独立音色制作屏**：录音 **30 秒** → 试听 → 分析并保存；同传屏会自动带回音色；\n- 点「双向对谈」进入 **DuoActivity**：对方→中文、我→英文，两个输入设备各自选择；
- 已保存音色会自动带回同传屏；
- 允许「安装未知来源应用」→ 安装 → 授权麦克风 → 开始同传；
- 需要 Android 8.0+（minSdk 26），侧载安装，不上架应用商店。

重新构建（需要 Android SDK；本机在 `C:\Android\sdk`）：

```powershell
$env:JAVA_HOME='C:\Program Files\Microsoft\jdk-21.0.12.101-hotspot'
$env:ANDROID_HOME='C:\Android\sdk'
.\.venv\Scripts\python.exe android\build_apk.py
# 输出 dist\RTI-android.apk
```

> APK 使用 debug 签名；已验证签名、manifest、dex（含 Java-WebSocket、slf4j、两个 Activity、CosyVoice/Omni 文本逻辑），但**没有 Android 真机可测**。如果安装后闪退，请把机型/Android 版本和 `adb logcat -s RTI AndroidRuntime` 发我。
>
> **红米 K90 Pro Max 打不开**：
> - 先卸载旧版本（签名/版本不同会提示“应用未安装”）；
> - 新 APK 已补 **slf4j-api/nop**（Java-WebSocket 的运行时依赖，旧包缺它可能 `NoClassDefFoundError`），versionCode 升到 2，并加了启动错误页；
> - 安装阶段失败先检查“安装未知应用”权限；安装成功但闪退，用 USB 调试抓 logcat。
>
> 安全：桌面 `config.json` 与 Android SharedPreferences 里的 API Key 是明文；不要把文件/手机分享给他人，测试完建议轮换 Key。

### 手机 PWA（备用，需要电脑在同一 Wi-Fi）

```powershell
.\.venv\Scripts\python.exe -m rti.server --host 0.0.0.0 `
  --ssl-keyfile certs\key.pem --ssl-certfile certs\cert.pem
```

手机先安装 `certs\cert.pem` 为受信任证书，打开 `https://<电脑局域网IP>:8765/`，可「添加到主屏幕」。这条路线仍需要电脑跑后端；独立使用请用上面的 APK。

实测（本机 WebSocket 网关 + Omni）：句末→首音约 **0.70–0.97s**；浏览器最终前端实测 **738ms**。

## 目录

```
src/rti/
├─ audio/       采集、播放、线性重采样
├─ asr/         流式 ASR 接口 + Fake（百炼适配器待 M1）
├─ tts/         流式克隆 TTS 接口 + Fake（百炼适配器待 M1）
├─ providers/   RealtimeProvider 接口 + Omni 直连 + Mock + 注册表
├─ voice/       Omni 声音复刻（qwen-voice-enrollment）
├─ mt/          DeepSeek 翻译适配器、Prompt、Mock
├─ pipeline/    级联编排、分段器、延迟打点、DeepSeek 计费
└─ config.py    环境变量配置
scripts/        bake-off、成本报告、设备列表、单句翻译、声音复刻、Omni 往返
docs/           调研报告、Phase 0 执行手册
tests/          53 个契约测试
```

## 配置

复制 `.env.example` 为 `.env`（当前代码直接读环境变量，未引入 dotenv）。

关键变量：

| 变量 | 说明 |
|---|---|
| `RTI_PROVIDER` | `mock` / `omni` / `aliyun` / `tencent` / `camb` / `azure` / `palabra` |
| `RTI_SOURCE_LANGUAGE` / `RTI_TARGET_LANGUAGE` | 默认 `zh-CN` / `en-US` |
| `RTI_VOICE_ID` | 克隆音色 ID（供应商返回） |
| `DASHSCOPE_API_KEY` | 百炼 Key（Omni / ASR / TTS / 声音复刻） |
| `BAILIAN_WORKSPACE_ID` | 百炼业务空间 ID（可选；生产建议填） |
| `OMNI_MODEL` | 默认 `qwen3.8-omni-flash-realtime` |
| `OMNI_VOICE` | `Tina` 或复刻返回的 `qwen-omni-vc-...` |
| `DEEPSEEK_API_KEY` | DeepSeek Key（级联翻译） |
| `DEEPSEEK_MODEL` | 默认 `deepseek-flash`（V4.1 Flash） |

> **不要把 Key 写进仓库或 `.env.example`**。若 Key 曾在聊天/日志中暴露，请到百炼控制台轮换。

## 合规提醒

- 默认只允许克隆**本人**音色，并保留单独同意与撤回记录；
- 实时输出需要显式 AI 标识（起播语音提示 + UI 角标）；
- 会议场景需告知参会人转写/翻译；录音另需同意；
- 音频优先留境内；文本出境按 PIPL 评估。

详见 `docs/2026-10-19-同声传译-调研报告.md`。

