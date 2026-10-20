package com.rti.interpreter;

import android.Manifest;
import android.app.Activity;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.media.AudioAttributes;
import android.media.AudioDeviceInfo;
import android.media.AudioFormat;
import android.media.AudioManager;
import android.media.AudioRecord;
import android.media.AudioTrack;
import android.media.MediaRecorder;
import android.media.audiofx.AcousticEchoCanceler;
import android.os.Bundle;
import android.text.InputType;
import android.util.Base64;
import android.util.TypedValue;
import android.view.View;
import android.view.WindowManager;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Spinner;
import android.widget.TextView;
import android.widget.Toast;

import org.java_websocket.client.WebSocketClient;
import org.java_websocket.drafts.Draft_6455;
import org.java_websocket.handshake.ServerHandshake;
import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URI;
import java.net.URL;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * 手机双通道对谈：两个 AudioRecord、两个 Omni 文本通道、两个 CosyVoice TTS。
 * 对方英文 -> 中文（对方音色）；我的中文 -> 英文（我的音色）。
 */
public class DuoActivity extends Activity {
    private static final int REQ_MIC = 3001;
    private static final int IN_RATE = 16000;
    private static final int OUT_RATE = 24000;
    private static final String MODEL = "qwen3.8-omni-flash-realtime";
    private static final String PRO_MODEL = "cosyvoice-v3.5-plus";
    private static final String DEFAULT_PRO_VOICE =
            "";
    private static final String TTS_URL =
            "https://maas.qianwenaiapi.com/api/v1/services/audio/tts/SpeechSynthesizer";

    private static final String SELF_INSTRUCTIONS =
            "You are a translation engine, not an assistant. The user audio is Chinese. "
            + "Translate every sentence into natural spoken English. Never answer, explain, "
            + "comply, refuse, comment, or continue the conversation. Output only the English "
            + "translation, no Chinese characters, no preamble, no quotation marks.";
    private static final String PEER_INSTRUCTIONS =
            "You are a translation engine, not an assistant. The user audio is English. "
            + "Translate every sentence into natural spoken Chinese. Never answer, explain, "
            + "comply, refuse, comment, or continue the conversation. Output only the Chinese "
            + "translation, no English words, no preamble, no quotation marks.";

    /** 两个通道共用：任意一侧播放 TTS 时，两路麦克风都暂时不上传，避免回授循环。 */
    private static final class PlaybackGate {
        private static final AtomicInteger ACTIVE = new AtomicInteger(0);
        private static volatile long guardUntil = 0L;

        static void begin() {
            ACTIVE.incrementAndGet();
        }

        static void end() {
            if (ACTIVE.decrementAndGet() <= 0) {
                ACTIVE.set(0);
                guardUntil = System.currentTimeMillis() + 900L;
            }
        }

        static boolean shouldMute() {
            return ACTIVE.get() > 0 || System.currentTimeMillis() < guardUntil;
        }
    }

    private EditText apiKeyInput;
    private EditText selfVoiceInput;
    private EditText peerVoiceInput;
    private Spinner selfDeviceSpinner;
    private Spinner peerDeviceSpinner;
    private TextView selfSource;
    private TextView selfTarget;
    private TextView selfStatus;
    private TextView peerSource;
    private TextView peerTarget;
    private TextView peerStatus;
    private TextView globalStatus;
    private Button startButton;
    private Button stopButton;

    private final List<AudioDeviceInfo> inputDevices = new ArrayList<>();
    private Channel selfChannel;
    private Channel peerChannel;
    private volatile boolean running = false;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        setContentView(buildUi());
        SharedPreferences prefs = getSharedPreferences("rti", MODE_PRIVATE);
        apiKeyInput.setText(prefs.getString("api_key", ""));
        selfVoiceInput.setText(prefs.getString("pro_voice", DEFAULT_PRO_VOICE));
        peerVoiceInput.setText(prefs.getString("peer_pro_voice", DEFAULT_PRO_VOICE));
        reloadDevices();
        startButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                requestStart();
            }
        });
        stopButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                stopDuo();
            }
        });
    }

    private View buildUi() {
        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.setBackgroundColor(Color.rgb(7, 11, 18));

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(dp(16), dp(20), dp(16), dp(24));
        scroll.addView(root);

        TextView title = text("双向对谈", 24, Color.WHITE);
        title.setTypeface(null, android.graphics.Typeface.BOLD);
        root.addView(title);
        root.addView(text("对方英文→中文（对方音色） · 我中文→英文（我的音色）",
                13, Color.rgb(148, 163, 184)));

        root.addView(text("API Key", 13, Color.rgb(148, 163, 184)), topParams(16));
        apiKeyInput = new EditText(this);
        apiKeyInput.setHint("sk-...");
        apiKeyInput.setSingleLine(true);
        apiKeyInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        apiKeyInput.setTextColor(Color.WHITE);
        apiKeyInput.setHintTextColor(Color.rgb(100, 116, 139));
        root.addView(apiKeyInput);

        root.addView(text("我的专业音色 ID", 13, Color.rgb(148, 163, 184)), topParams(12));
        selfVoiceInput = editText(DEFAULT_PRO_VOICE);
        root.addView(selfVoiceInput);

        root.addView(text("对方专业音色 ID（没有可以先填默认）", 13, Color.rgb(148, 163, 184)), topParams(12));
        peerVoiceInput = editText(DEFAULT_PRO_VOICE);
        root.addView(peerVoiceInput);

        selfDeviceSpinner = new Spinner(this);
        peerDeviceSpinner = new Spinner(this);
        root.addView(text("对方输入设备", 13, Color.rgb(148, 163, 184)), topParams(14));
        root.addView(peerDeviceSpinner);
        root.addView(text("我的输入设备", 13, Color.rgb(148, 163, 184)), topParams(10));
        root.addView(selfDeviceSpinner);
        root.addView(text("提示：两个通道最好用不同输入设备（手机麦克风 / 蓝牙耳机 / USB 麦克风）。",
                12, Color.rgb(108, 124, 146)), topParams(6));

        LinearLayout peerPanel = panel("对方 · English → 中文");
        peerSource = text("", 14, Color.rgb(226, 232, 240));
        peerSource.setMinHeight(dp(74));
        peerTarget = text("", 14, Color.rgb(125, 211, 252));
        peerTarget.setMinHeight(dp(74));
        peerStatus = text("未开始", 12, Color.rgb(108, 124, 146));
        peerPanel.addView(peerSource);
        peerPanel.addView(peerTarget);
        peerPanel.addView(peerStatus);
        root.addView(peerPanel, topParams(12));

        LinearLayout selfPanel = panel("我 · 中文 → English");
        selfSource = text("", 14, Color.rgb(226, 232, 240));
        selfSource.setMinHeight(dp(74));
        selfTarget = text("", 14, Color.rgb(125, 211, 252));
        selfTarget.setMinHeight(dp(74));
        selfStatus = text("未开始", 12, Color.rgb(108, 124, 146));
        selfPanel.addView(selfSource);
        selfPanel.addView(selfTarget);
        selfPanel.addView(selfStatus);
        root.addView(selfPanel, topParams(12));

        startButton = new Button(this);
        startButton.setText("开始双向对谈");
        startButton.setTextSize(TypedValue.COMPLEX_UNIT_SP, 18);
        root.addView(startButton, topParams(16));
        stopButton = new Button(this);
        stopButton.setText("停止");
        stopButton.setEnabled(false);
        root.addView(stopButton, topParams(8));
        globalStatus = text("建议戴耳机：你听中文，对方听英文。", 12, Color.rgb(251, 191, 36));
        root.addView(globalStatus, topParams(10));
        root.addView(text("提示：手机双通道先用默认/已保存专业音色；自动复刻优化目前以桌面版为主。",
                12, Color.rgb(108, 124, 146)), topParams(8));
        return scroll;
    }

    private LinearLayout panel(String title) {
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setPadding(dp(10), dp(10), dp(10), dp(10));
        layout.setBackgroundColor(Color.rgb(15, 26, 43));
        layout.addView(text(title, 15, Color.WHITE));
        return layout;
    }

    private EditText editText(String value) {
        EditText edit = new EditText(this);
        edit.setSingleLine(true);
        edit.setText(value);
        edit.setTextColor(Color.WHITE);
        edit.setHintTextColor(Color.rgb(100, 116, 139));
        return edit;
    }

    private void reloadDevices() {
        inputDevices.clear();
        AudioManager manager = (AudioManager) getSystemService(AUDIO_SERVICE);
        if (manager != null) {
            for (AudioDeviceInfo device : manager.getDevices(AudioManager.GET_DEVICES_INPUTS)) {
                inputDevices.add(device);
            }
        }
        List<String> labels = new ArrayList<>();
        if (inputDevices.isEmpty()) {
            labels.add("默认麦克风");
        } else {
            for (AudioDeviceInfo device : inputDevices) {
                CharSequence name = device.getProductName();
                labels.add((name == null || name.length() == 0 ? "输入设备" : name.toString())
                        + "  [type " + device.getType() + "]");
            }
        }
        ArrayAdapter<String> adapter = new ArrayAdapter<>(this,
                android.R.layout.simple_spinner_dropdown_item, labels);
        selfDeviceSpinner.setAdapter(adapter);
        peerDeviceSpinner.setAdapter(adapter);
        if (inputDevices.size() > 1) {
            peerDeviceSpinner.setSelection(1);
        }
    }

    private void requestStart() {
        if (running) {
            return;
        }
        String key = apiKeyInput.getText().toString().trim();
        if (key.isEmpty()) {
            toast("请先填写 API Key");
            return;
        }
        getSharedPreferences("rti", MODE_PRIVATE).edit()
                .putString("api_key", key)
                .putString("pro_voice", selfVoiceInput.getText().toString().trim())
                .putString("peer_pro_voice", peerVoiceInput.getText().toString().trim())
                .apply();
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, REQ_MIC);
            return;
        }
        startDuo();
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(requestCode, permissions, results);
        if (requestCode == REQ_MIC) {
            if (results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED) {
                startDuo();
            } else {
                toast("没有麦克风权限");
            }
        }
    }

    private AudioDeviceInfo selectedDevice(Spinner spinner) {
        int index = spinner == null ? -1 : spinner.getSelectedItemPosition();
        if (index < 0 || index >= inputDevices.size()) {
            return null;
        }
        return inputDevices.get(index);
    }

    private void startDuo() {
        if (running) {
            return;
        }
        running = true;
        startButton.setEnabled(false);
        stopButton.setEnabled(true);
        globalStatus.setText("连接中…");
        selfSource.setText("");
        selfTarget.setText("");
        peerSource.setText("");
        peerTarget.setText("");
        String key = apiKeyInput.getText().toString().trim();
        String selfVoice = selfVoiceInput.getText().toString().trim();
        String peerVoice = peerVoiceInput.getText().toString().trim();
        if (selfVoice.isEmpty()) selfVoice = DEFAULT_PRO_VOICE;
        if (peerVoice.isEmpty()) peerVoice = DEFAULT_PRO_VOICE;
        selfChannel = new Channel("self", selfVoice, "en", SELF_INSTRUCTIONS,
                selectedDevice(selfDeviceSpinner), selfSource, selfTarget, selfStatus);
        peerChannel = new Channel("peer", peerVoice, "zh", PEER_INSTRUCTIONS,
                selectedDevice(peerDeviceSpinner), peerSource, peerTarget, peerStatus);
        try {
            selfChannel.start(key);
            peerChannel.start(key);
            globalStatus.setText("双向对谈中：请交替说话。");
        } catch (Exception ex) {
            globalStatus.setText("启动失败：" + ex.getMessage());
            stopDuo();
        }
    }

    private void stopDuo() {
        running = false;
        if (selfChannel != null) {
            selfChannel.stop();
            selfChannel = null;
        }
        if (peerChannel != null) {
            peerChannel.stop();
            peerChannel = null;
        }
        startButton.setEnabled(true);
        stopButton.setEnabled(false);
        globalStatus.setText("已停止");
    }

    private void toast(String text) {
        Toast.makeText(this, text, Toast.LENGTH_SHORT).show();
    }

    private TextView text(String value, int sp, int color) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(TypedValue.COMPLEX_UNIT_SP, sp);
        view.setTextColor(color);
        view.setLineSpacing(dp(3), 1.0f);
        return view;
    }

    private LinearLayout.LayoutParams topParams(int topDp) {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT);
        params.topMargin = dp(topDp);
        return params;
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    @Override
    protected void onDestroy() {
        stopDuo();
        super.onDestroy();
    }

    private final class Channel {
        private final String role;
        private final String voice;
        private final String ttsLanguage;
        private final String instructions;
        private final AudioDeviceInfo inputDevice;
        private final TextView sourceView;
        private final TextView targetView;
        private final TextView statusView;
        private final StringBuilder translationBuffer = new StringBuilder();
        private final StringBuilder pendingTts = new StringBuilder();
        private volatile boolean responseActive = false;
        private volatile boolean ttsBusy = false;
        private volatile boolean recording = false;
        private AudioRecord recorder;
        private AcousticEchoCanceler echoCanceler;
        private Thread recordThread;
        private AudioTrack track;
        private WebSocketClient ws;

        Channel(String role, String voice, String ttsLanguage, String instructions,
                AudioDeviceInfo inputDevice, TextView sourceView, TextView targetView,
                TextView statusView) {
            this.role = role;
            this.voice = voice;
            this.ttsLanguage = ttsLanguage;
            this.instructions = instructions;
            this.inputDevice = inputDevice;
            this.sourceView = sourceView;
            this.targetView = targetView;
            this.statusView = statusView;
        }

        void start(String key) throws Exception {
            initTrack();
            URI uri = URI.create("wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model=" + MODEL);
            Map<String, String> headers = new HashMap<>();
            headers.put("Authorization", "Bearer " + key);
            ws = new WebSocketClient(uri, new Draft_6455(), headers, 15000) {
                @Override
                public void onOpen(ServerHandshake handshake) {
                    sendSessionUpdate();
                }

                @Override
                public void onMessage(String message) {
                    handleMessage(message);
                }

                @Override
                public void onClose(int code, String reason, boolean remote) {
                    setStatus("连接已断开：" + reason);
                }

                @Override
                public void onError(Exception ex) {
                    setStatus("连接错误：" + (ex == null ? "unknown" : ex.getMessage()));
                }
            };
            ws.setConnectionLostTimeout(60);
            ws.connect();
            setStatus("连接中…");
        }

        private void sendSessionUpdate() {
            try {
                JSONObject session = new JSONObject();
                session.put("modalities", new JSONArray().put("text"));
                session.put("instructions", instructions);
                session.put("turn_detection", new JSONObject()
                        .put("type", "server_vad").put("create_response", false));
                JSONObject inputFormat = new JSONObject()
                        .put("type", "pcm").put("sample_rate", IN_RATE)
                        .put("sample_format", "s16le").put("channels", 1)
                        .put("packing", "interleaved").put("channel_layout", "mono");
                JSONObject outputFormat = new JSONObject()
                        .put("type", "pcm").put("sample_rate", OUT_RATE);
                session.put("audio", new JSONObject()
                        .put("input", new JSONObject().put("format", inputFormat))
                        .put("output", new JSONObject()
                                .put("voice", voice)
                                .put("format", outputFormat)));
                ws.send(new JSONObject().put("type", "session.update")
                        .put("session", session).toString());
            } catch (Exception ex) {
                setStatus("会话配置失败：" + ex.getMessage());
            }
        }

        private void handleMessage(String message) {
            try {
                JSONObject event = new JSONObject(message);
                String type = event.optString("type", "");
                if ("session.updated".equals(type)) {
                    startRecording();
                } else if ("input_audio_buffer.speech_stopped".equals(type)) {
                    requestResponse();
                } else if ("response.created".equals(type)) {
                    responseActive = true;
                    translationBuffer.setLength(0);
                } else if ("response.text.delta".equals(type)
                        || "response.audio_transcript.delta".equals(type)) {
                    String delta = event.optString("delta", "");
                    translationBuffer.append(delta);
                    append(targetView, delta);
                } else if ("conversation.item.input_audio_transcription.delta".equals(type)) {
                    append(sourceView, event.optString("delta", ""));
                } else if ("conversation.item.input_audio_transcription.completed".equals(type)) {
                    append(sourceView, event.optString("transcript", "") + "\n");
                } else if ("response.done".equals(type)) {
                    responseActive = false;
                    String translated = translationBuffer.toString().trim();
                    translationBuffer.setLength(0);
                    if (translated.isEmpty()) {
                        setStatus("本轮完成");
                    } else {
                        startTts(translated);
                    }
                } else if ("error".equals(type)) {
                    JSONObject error = event.optJSONObject("error");
                    setStatus("错误：" + (error == null ? event.toString() : error.optString("message")));
                }
            } catch (Exception ignored) {
            }
        }

        private void requestResponse() {
            if (ws == null) return;
            if (responseActive) return;
            try {
                JSONObject response = new JSONObject()
                        .put("instructions", instructions)
                        .put("modalities", new JSONArray().put("text"));
                responseActive = true;
                ws.send(new JSONObject().put("type", "response.create")
                        .put("response", response).toString());
            } catch (Exception ex) {
                responseActive = false;
                setStatus("翻译请求失败：" + ex.getMessage());
            }
        }

        private void startRecording() {
            if (recording) return;
            try {
                int source = MediaRecorder.AudioSource.VOICE_COMMUNICATION;
                int minBuffer = AudioRecord.getMinBufferSize(
                        IN_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
                recorder = new AudioRecord(source, IN_RATE, AudioFormat.CHANNEL_IN_MONO,
                        AudioFormat.ENCODING_PCM_16BIT, Math.max(minBuffer, IN_RATE));
                if (recorder.getState() != AudioRecord.STATE_INITIALIZED) {
                    throw new RuntimeException("AudioRecord 初始化失败");
                }
                if (inputDevice != null) {
                    recorder.setPreferredDevice(inputDevice);
                }
                try {
                    if (AcousticEchoCanceler.isAvailable()) {
                        echoCanceler = AcousticEchoCanceler.create(recorder.getAudioSessionId());
                        if (echoCanceler != null) {
                            echoCanceler.setEnabled(true);
                        }
                    }
                } catch (Exception ignored) {
                }
                recorder.startRecording();
                recording = true;
                recordThread = new Thread(new Runnable() {
                    @Override
                    public void run() {
                        short[] buffer = new short[320];
                        while (recording) {
                            int count = recorder.read(buffer, 0, buffer.length);
                            if (count <= 0) continue;
                            if (PlaybackGate.shouldMute()) continue;
                            byte[] pcm = new byte[count * 2];
                            for (int i = 0; i < count; i++) {
                                pcm[2 * i] = (byte) (buffer[i] & 0xff);
                                pcm[2 * i + 1] = (byte) ((buffer[i] >> 8) & 0xff);
                            }
                            if (ws != null) {
                                try {
                                    JSONObject msg = new JSONObject()
                                            .put("type", "input_audio_buffer.append")
                                            .put("audio", Base64.encodeToString(pcm, Base64.NO_WRAP));
                                    ws.send(msg.toString());
                                } catch (Exception ignored) {
                                }
                            }
                        }
                    }
                }, "rti-duo-" + role);
                recordThread.start();
                setStatus("已连接 · 说话即可");
            } catch (Exception ex) {
                setStatus("录音启动失败：" + ex.getMessage());
            }
        }

        private void initTrack() throws Exception {
            int minBuffer = AudioTrack.getMinBufferSize(
                    OUT_RATE, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT);
            track = new AudioTrack.Builder()
                    .setAudioAttributes(new AudioAttributes.Builder()
                            .setUsage(AudioAttributes.USAGE_MEDIA)
                            .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                            .build())
                    .setAudioFormat(new AudioFormat.Builder()
                            .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                            .setSampleRate(OUT_RATE)
                            .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                            .build())
                    .setBufferSizeInBytes(Math.max(minBuffer, OUT_RATE))
                    .setTransferMode(AudioTrack.MODE_STREAM)
                    .build();
            track.play();
        }

        private void startTts(final String text) {
            synchronized (this) {
                if (ttsBusy) {
                    pendingTts.append(' ').append(text);
                    return;
                }
                ttsBusy = true;
            }
            setStatus("专业音色合成中…");
            new Thread(new Runnable() {
                @Override
                public void run() {
                    String failure = null;
                    PlaybackGate.begin();
                    try {
                        speak(text);
                    } catch (Exception ex) {
                        failure = ex.getMessage();
                    } finally {
                        PlaybackGate.end();
                        ttsBusy = false;
                        String next;
                        synchronized (Channel.this) {
                            next = pendingTts.toString().trim();
                            pendingTts.setLength(0);
                        }
                        if (next.isEmpty()) {
                            setStatus(failure == null ? "本轮完成 · 继续说话" : "合成失败：" + failure);
                        } else {
                            startTts(next);
                        }
                    }
                }
            }, "rti-duo-tts-" + role).start();
        }

        private void speak(String text) throws Exception {
            String key = apiKeyInput.getText().toString().trim();
            JSONObject input = new JSONObject()
                    .put("text", text)
                    .put("voice", voice)
                    .put("format", "pcm")
                    .put("sample_rate", OUT_RATE)
                    .put("language_hints", new JSONArray().put(ttsLanguage));
            JSONObject payload = new JSONObject()
                    .put("model", PRO_MODEL)
                    .put("input", input);
            HttpURLConnection connection = (HttpURLConnection) new URL(TTS_URL).openConnection();
            connection.setRequestMethod("POST");
            connection.setConnectTimeout(20000);
            connection.setReadTimeout(180000);
            connection.setDoOutput(true);
            connection.setRequestProperty("Authorization", "Bearer " + key);
            connection.setRequestProperty("Content-Type", "application/json");
            connection.setRequestProperty("X-DashScope-SSE", "enable");
            OutputStream output = connection.getOutputStream();
            output.write(payload.toString().getBytes(StandardCharsets.UTF_8));
            output.close();
            int status = connection.getResponseCode();
            if (status != 200) {
                throw new RuntimeException("HTTP " + status + " " + readAll(connection.getErrorStream()));
            }
            BufferedReader reader = new BufferedReader(
                    new InputStreamReader(connection.getInputStream(), StandardCharsets.UTF_8));
            StringBuilder sse = new StringBuilder();
            int total = 0;
            String line;
            while ((line = reader.readLine()) != null) {
                if (line.startsWith("data:")) {
                    sse.append(line.substring(5).trim()).append('\n');
                    continue;
                }
                if (!line.isEmpty() || sse.length() == 0) continue;
                String body = sse.toString().trim();
                sse.setLength(0);
                if (body.isEmpty()) continue;
                JSONObject event = new JSONObject(body);
                JSONObject out = event.optJSONObject("output");
                if (out == null) continue;
                JSONObject audio = out.optJSONObject("audio");
                String encoded = audio == null ? "" : audio.optString("data", "");
                if (!encoded.isEmpty()) {
                    total += writePcm(Base64.decode(encoded, Base64.DEFAULT));
                }
                if ("stop".equals(out.optString("finish_reason"))) break;
            }
            reader.close();
            if (total == 0) throw new RuntimeException("TTS 没有返回音频");
        }

        private int writePcm(byte[] pcm) {
            if (pcm.length < 2 || track == null) return 0;
            short[] samples = new short[pcm.length / 2];
            ByteBuffer.wrap(pcm).order(ByteOrder.LITTLE_ENDIAN).asShortBuffer().get(samples);
            track.write(samples, 0, samples.length);
            return samples.length * 2;
        }

        private String readAll(InputStream stream) {
            if (stream == null) return "";
            try {
                ByteArrayOutputStream out = new ByteArrayOutputStream();
                byte[] buffer = new byte[4096];
                int count;
                while ((count = stream.read(buffer)) > 0) out.write(buffer, 0, count);
                return new String(out.toByteArray(), StandardCharsets.UTF_8);
            } catch (Exception ex) {
                return "";
            } finally {
                try { stream.close(); } catch (Exception ignored) { }
            }
        }

        private void stop() {
            recording = false;
            if (ws != null) {
                try { ws.close(); } catch (Exception ignored) { }
                ws = null;
            }
            if (recordThread != null) {
                try { recordThread.join(400); } catch (InterruptedException ignored) { }
                recordThread = null;
            }
            if (recorder != null) {
                try { recorder.stop(); } catch (Exception ignored) { }
                try { recorder.release(); } catch (Exception ignored) { }
                recorder = null;
            }
            if (echoCanceler != null) {
                try { echoCanceler.setEnabled(false); echoCanceler.release(); } catch (Exception ignored) { }
                echoCanceler = null;
            }
            if (track != null) {
                try { track.stop(); } catch (Exception ignored) { }
                try { track.release(); } catch (Exception ignored) { }
                track = null;
            }
            setStatus("已停止");
        }

        private void setStatus(final String text) {
            runOnUiThread(new Runnable() {
                @Override
                public void run() {
                    statusView.setText(text);
                }
            });
        }

        private void append(final TextView view, final String text) {
            runOnUiThread(new Runnable() {
                @Override
                public void run() {
                    view.setText(view.getText().toString() + text);
                }
            });
        }
    }
}
