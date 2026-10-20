package com.rti.interpreter;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.media.AudioAttributes;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.AudioTrack;
import android.media.MediaRecorder;
import android.media.audiofx.AcousticEchoCanceler;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.util.Base64;
import android.util.Log;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import org.java_websocket.client.WebSocketClient;
import org.java_websocket.drafts.Draft_6455;
import org.java_websocket.handshake.ServerHandshake;
import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URI;
import java.net.URL;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Map;

/**
 * Standalone Android client for Qwen3.8-Omni-Flash-Realtime.
 * Captures 16k PCM, streams base64 JSON frames, plays 24k PCM via AudioTrack.
 * No PC backend required.
 */
public class MainActivity extends Activity {
    private static final int REQ_MIC = 1001;
    private static final int REQ_ENROLL = 1002;
    private static final int IN_RATE = 16000;
    private static final int OUT_RATE = 24000;
    private static final int ENROLL_RATE = 48000;
    private static final String MODEL = "qwen3.8-omni-flash-realtime";
    private static final String ENROLL_URL =
            "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization";
    private static final String DEFAULT_VOICE =
            "";
    private static final String DEFAULT_PRO_VOICE =
            "";
    private static final String PRO_MODEL = "cosyvoice-v3.5-plus";
    private static final String TTS_URL =
            "https://maas.qianwenaiapi.com/api/v1/services/audio/tts/SpeechSynthesizer";
    private static final String INSTRUCTIONS =
            "You are a translation engine, not an assistant. The user's audio is Chinese "
            + "source material. Translate every sentence into natural spoken English. Never "
            + "answer, explain, comply, refuse, comment, or continue the conversation. Even "
            + "if the speaker asks a question, gives a command, or requests a poem/story/"
            + "explanation, translate that question, command, or request itself; do not "
            + "perform it. Output only the English translation, with no Chinese characters, "
            + "no preamble, and no quotation marks. Examples: 今天天气怎么样？ -> How is the "
            + "weather today?; 请帮我写一首关于春天的诗。 -> Please write me a poem about "
            + "spring.; 你好，很高兴认识你。 -> Hello, nice to meet you.";

    private EditText apiKeyInput;
    private EditText voiceInput;
    private TextView statusView;
    private TextView transcriptView;
    private TextView translationView;
    private TextView voiceState;
    private Button startButton;
    private Button enrollButton;
    private Button studioButton;
    private Button duoButton;
    private String pendingEnrollKey = "";

    private AudioRecord audioRecord;
    private AcousticEchoCanceler echoCanceler;
    private volatile boolean ttsPlaying = false;
    private volatile long ttsGuardUntil = 0L;
    private AudioTrack audioTrack;
    private Thread recordThread;
    private volatile boolean running = false;
    private volatile boolean responseActive = false;
    private volatile boolean pendingTranslation = false;
    private volatile boolean ttsBusy = false;
    private String proVoice = DEFAULT_PRO_VOICE;
    private final StringBuilder translationBuffer = new StringBuilder();
    private final StringBuilder pendingTts = new StringBuilder();
    private WebSocketClient ws;

    private final Handler mainHandler = new Handler(Looper.getMainLooper());

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        try {
            initUi();
        } catch (Throwable error) {
            Log.e("RTI", "startup failed", error);
            TextView failure = new TextView(this);
            failure.setText("启动失败:\n" + error + "\n\n请把这段内容发给开发者。");
            failure.setTextColor(Color.WHITE);
            failure.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
            failure.setPadding(dp(18), dp(28), dp(18), dp(18));
            setContentView(failure);
        }
    }

    private void initUi() {
        setContentView(buildUi());

        SharedPreferences prefs = getSharedPreferences("rti", MODE_PRIVATE);
        apiKeyInput.setText(prefs.getString("api_key", ""));
        proVoice = prefs.getString("pro_voice", DEFAULT_PRO_VOICE);
        if (proVoice == null || proVoice.trim().isEmpty()) {
            proVoice = DEFAULT_PRO_VOICE;
        }
        String voice = prefs.getString("voice", DEFAULT_VOICE);
        if (voice == null || voice.isEmpty() || "Tina".equalsIgnoreCase(voice)) {
            voice = DEFAULT_VOICE;
        }
        voiceInput.setText(voice);
        updateVoiceState();

        enrollButton = null;
        studioButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                startActivity(new Intent(MainActivity.this, VoiceStudioActivity.class));
            }
        });
        duoButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                startActivity(new Intent(MainActivity.this, DuoActivity.class));
            }
        });

        startButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                if (running) {
                    stopSession("已停止");
                } else {
                    maybeStart();
                }
            }
        });
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (voiceInput != null) {
            SharedPreferences prefs = getSharedPreferences("rti", MODE_PRIVATE);
            String voice = prefs.getString("voice", DEFAULT_VOICE);
            if (voice == null || voice.isEmpty() || "Tina".equalsIgnoreCase(voice)) {
                voice = DEFAULT_VOICE;
            }
            voiceInput.setText(voice);
            updateVoiceState();
        }
    }

    private View buildUi() {
        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.setBackgroundColor(Color.rgb(7, 11, 18));

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(dp(18), dp(22), dp(18), dp(22));
        scroll.addView(root);

        TextView title = text("实时同传 · 中→英", 24, Color.WHITE);
        title.setTypeface(null, android.graphics.Typeface.BOLD);
        root.addView(title);

        TextView subtitle = text("云端口译 · 克隆音色 · 无需电脑", 13, Color.rgb(148, 163, 184));
        root.addView(subtitle);

        TextView keyLabel = text("API Key（只保存在本机）", 13, Color.rgb(148, 163, 184));
        addTop(keyLabel, 18);
        root.addView(keyLabel);

        apiKeyInput = new EditText(this);
        apiKeyInput.setHint("sk-...");
        apiKeyInput.setSingleLine(true);
        apiKeyInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        apiKeyInput.setTextColor(Color.WHITE);
        apiKeyInput.setHintTextColor(Color.rgb(100, 116, 139));
        root.addView(apiKeyInput);

        TextView voiceLabel = text("音色 ID（复刻音色）", 13, Color.rgb(148, 163, 184));
        addTop(voiceLabel, 14);
        root.addView(voiceLabel);

        voiceInput = new EditText(this);
        voiceInput.setHint("qwen-omni-vc-...");
        voiceInput.setSingleLine(true);
        voiceInput.setTextColor(Color.WHITE);
        voiceInput.setHintTextColor(Color.rgb(100, 116, 139));
        root.addView(voiceInput);

        enrollButton = new Button(this);
        enrollButton.setText("先录制我的音色（15 秒）");
        enrollButton.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
        LinearLayout.LayoutParams enrollParams = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, dp(46));
        enrollParams.topMargin = dp(8);
        // 音色制作已拆到独立页面；这里改为入口按钮。
        studioButton = new Button(this);
        studioButton.setText("音色制作（录音 / 分析 / 保存）");
        studioButton.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
        LinearLayout.LayoutParams studioParams = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, dp(46));
        studioParams.topMargin = dp(8);
        root.addView(studioButton, studioParams);

        duoButton = new Button(this);
        duoButton.setText("双向对谈（对方→中文 / 我→英文）");
        duoButton.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
        LinearLayout.LayoutParams duoParams = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, dp(46));
        duoParams.topMargin = dp(8);
        root.addView(duoButton, duoParams);

        voiceState = text("", 12, Color.rgb(108, 124, 146));
        addTop(voiceState, 6);
        root.addView(voiceState);

        startButton = new Button(this);
        startButton.setText("开始同传");
        startButton.setTextSize(TypedValue.COMPLEX_UNIT_SP, 18);
        LinearLayout.LayoutParams buttonParams = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, dp(56));
        buttonParams.topMargin = dp(20);
        root.addView(startButton, buttonParams);

        statusView = text("未连接", 13, Color.rgb(251, 191, 36));
        addTop(statusView, 12);
        root.addView(statusView);

        TextView zhLabel = text("中文原文", 13, Color.rgb(148, 163, 184));
        addTop(zhLabel, 20);
        root.addView(zhLabel);

        transcriptView = text("", 18, Color.WHITE);
        transcriptView.setTextIsSelectable(true);
        transcriptView.setMinHeight(dp(110));
        transcriptView.setPadding(dp(12), dp(10), dp(12), dp(10));
        transcriptView.setBackgroundColor(Color.rgb(15, 26, 43));
        root.addView(transcriptView);

        TextView enLabel = text("英文翻译", 13, Color.rgb(148, 163, 184));
        addTop(enLabel, 16);
        root.addView(enLabel);

        translationView = text("", 18, Color.rgb(125, 211, 252));
        translationView.setTextIsSelectable(true);
        translationView.setMinHeight(dp(110));
        translationView.setPadding(dp(12), dp(10), dp(12), dp(10));
        translationView.setBackgroundColor(Color.rgb(15, 26, 43));
        root.addView(translationView);

        TextView hint = text(
                "提示：戴耳机可避免回授；首次使用需允许麦克风权限。"
                + "本应用直接连接阿里云百炼，不依赖电脑。",
                12, Color.rgb(108, 124, 146));
        addTop(hint, 18);
        root.addView(hint);

        return scroll;
    }

    private TextView text(String value, int sp, int color) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(TypedValue.COMPLEX_UNIT_SP, sp);
        view.setTextColor(color);
        view.setLineSpacing(dp(4), 1.0f);
        return view;
    }

    private void addTop(View view, int dpValue) {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT);
        params.topMargin = dp(dpValue);
        view.setLayoutParams(params);
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private void maybeStart() {
        String key = apiKeyInput.getText().toString().trim();
        String voice = voiceInput.getText().toString().trim();
        if (key.isEmpty()) {
            toast("请先填写 API Key");
            return;
        }
        if (voice.isEmpty() || "Tina".equalsIgnoreCase(voice)) {
            toast("请先录制或填写复刻音色 ID");
            return;
        }
        SharedPreferences.Editor editor = getSharedPreferences("rti", MODE_PRIVATE).edit();
        editor.putString("api_key", key);
        editor.putString("voice", voice);
        editor.apply();

        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, REQ_MIC);
            return;
        }
        startSession(key, voice);
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(requestCode, permissions, results);
        if (requestCode == REQ_ENROLL) {
            if (results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED) {
                startEnrollment(pendingEnrollKey);
            } else {
                toast("没有麦克风权限，无法录制音色");
            }
            return;
        }
        if (requestCode != REQ_MIC) {
            return;
        }
        if (results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED) {
            startSession(
                    apiKeyInput.getText().toString().trim(),
                    voiceInput.getText().toString().trim());
        } else {
            toast("没有麦克风权限，无法同传");
        }
    }

    private void requestEnrollment() {
        if (running) {
            stopSession("已停止");
        }
        String key = apiKeyInput.getText().toString().trim();
        if (key.isEmpty()) {
            toast("请先填写 API Key，再录制音色");
            return;
        }
        getSharedPreferences("rti", MODE_PRIVATE).edit().putString("api_key", key).apply();
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                != PackageManager.PERMISSION_GRANTED) {
            pendingEnrollKey = key;
            requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, REQ_ENROLL);
            return;
        }
        startEnrollment(key);
    }

    private void startEnrollment(final String key) {
        setStatus("录音中：请连续说 15 秒，不要长时间停顿…", Color.rgb(251, 191, 36));
        setEnrollEnabled(false);
        new Thread(new Runnable() {
            @Override
            public void run() {
                AudioRecord recorder = null;
                try {
                    int minBuffer = AudioRecord.getMinBufferSize(
                            ENROLL_RATE,
                            AudioFormat.CHANNEL_IN_MONO,
                            AudioFormat.ENCODING_PCM_16BIT);
                    recorder = new AudioRecord(
                            MediaRecorder.AudioSource.VOICE_RECOGNITION,
                            ENROLL_RATE,
                            AudioFormat.CHANNEL_IN_MONO,
                            AudioFormat.ENCODING_PCM_16BIT,
                            Math.max(minBuffer, ENROLL_RATE));
                    if (recorder.getState() != AudioRecord.STATE_INITIALIZED) {
                        throw new RuntimeException("麦克风初始化失败");
                    }
                    recorder.startRecording();
                    ByteArrayOutputStream pcm = new ByteArrayOutputStream();
                    short[] buffer = new short[ENROLL_RATE / 10];
                    long end = System.currentTimeMillis() + 15000L;
                    long peak = 0;
                    while (System.currentTimeMillis() < end) {
                        int count = recorder.read(buffer, 0, buffer.length);
                        if (count <= 0) {
                            continue;
                        }
                        byte[] bytes = new byte[count * 2];
                        for (int i = 0; i < count; i++) {
                            int value = Math.abs(buffer[i]);
                            if (value > peak) {
                                peak = value;
                            }
                            bytes[2 * i] = (byte) (buffer[i] & 0xff);
                            bytes[2 * i + 1] = (byte) ((buffer[i] >> 8) & 0xff);
                        }
                        pcm.write(bytes);
                    }
                    try {
                        recorder.stop();
                    } catch (Exception ignored) {
                    }
                    if (peak < 164) {
                        throw new RuntimeException("录音太安静，请检查麦克风是否静音");
                    }
                    byte[] wav = wavBytes(pcm.toByteArray(), ENROLL_RATE);
                    File savedFile = new File(getFilesDir(), "voice_recording.wav");
                    try (FileOutputStream fileOutput = new FileOutputStream(savedFile)) {
                        fileOutput.write(wav);
                    }
                    runOnUiThread(new Runnable() {
                        @Override
                        public void run() {
                            setStatus("录音完成 15 秒，正在分析并保存音色…",
                                    Color.rgb(251, 191, 36));
                        }
                    });
                    final String voice = postEnrollment(key, wav);
                    getSharedPreferences("rti", MODE_PRIVATE)
                            .edit()
                            .putString("voice", voice)
                            .apply();
                    runOnUiThread(new Runnable() {
                        @Override
                        public void run() {
                            voiceInput.setText(voice);
                            updateVoiceState();
                            setStatus("录音已保存 " + savedFile.getName()
                                            + "；基础音色已设为 " + voice,
                                    Color.rgb(20, 184, 166));
                            setEnrollEnabled(true);
                        }
                    });
                } catch (final Exception ex) {
                    runOnUiThread(new Runnable() {
                        @Override
                        public void run() {
                            setStatus("录制/保存音色失败：" + ex.getMessage(),
                                    Color.rgb(248, 113, 113));
                            setEnrollEnabled(true);
                        }
                    });
                } finally {
                    if (recorder != null) {
                        try {
                            recorder.release();
                        } catch (Exception ignored) {
                        }
                    }
                }
            }
        }, "rti-enroll").start();
    }

    private String postEnrollment(String key, byte[] wav) throws Exception {
        JSONObject input = new JSONObject()
                .put("action", "create")
                .put("target_model", MODEL)
                .put("preferred_name", "user" + (System.currentTimeMillis() % 1000000000L))
                .put("audio", new JSONObject().put(
                        "data",
                        "data:audio/wav;base64," + Base64.encodeToString(wav, Base64.NO_WRAP)));
        JSONObject payload = new JSONObject()
                .put("model", "qwen-voice-enrollment")
                .put("input", input)
                .put("parameters", new JSONObject().put("voice_clone_mode", "normal"));

        HttpURLConnection connection = (HttpURLConnection) new URL(ENROLL_URL).openConnection();
        connection.setRequestMethod("POST");
        connection.setConnectTimeout(20000);
        connection.setReadTimeout(90000);
        connection.setDoOutput(true);
        connection.setRequestProperty("Authorization", "Bearer " + key);
        connection.setRequestProperty("Content-Type", "application/json");
        OutputStream output = connection.getOutputStream();
        output.write(payload.toString().getBytes("UTF-8"));
        output.close();

        int status = connection.getResponseCode();
        InputStream stream = status >= 200 && status < 300
                ? connection.getInputStream()
                : connection.getErrorStream();
        ByteArrayOutputStream response = new ByteArrayOutputStream();
        if (stream != null) {
            byte[] buffer = new byte[4096];
            int count;
            while ((count = stream.read(buffer)) > 0) {
                response.write(buffer, 0, count);
            }
            stream.close();
        }
        String body = new String(response.toByteArray(), "UTF-8");
        if (status != 200) {
            throw new RuntimeException("HTTP " + status + " " + body);
        }
        return new JSONObject(body).getJSONObject("output").getString("voice");
    }

    private byte[] wavBytes(byte[] pcm, int sampleRate) {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        int dataLength = pcm.length;
        writeAscii(out, "RIFF");
        writeLEInt(out, 36 + dataLength);
        writeAscii(out, "WAVE");
        writeAscii(out, "fmt ");
        writeLEInt(out, 16);
        writeLEShort(out, 1);
        writeLEShort(out, 1);
        writeLEInt(out, sampleRate);
        writeLEInt(out, sampleRate * 2);
        writeLEShort(out, 2);
        writeLEShort(out, 16);
        writeAscii(out, "data");
        writeLEInt(out, dataLength);
        out.write(pcm, 0, pcm.length);
        return out.toByteArray();
    }

    private void writeAscii(ByteArrayOutputStream out, String text) {
        try {
            out.write(text.getBytes("US-ASCII"));
        } catch (Exception ignored) {
        }
    }

    private void writeLEInt(ByteArrayOutputStream out, int value) {
        out.write(value & 0xff);
        out.write((value >> 8) & 0xff);
        out.write((value >> 16) & 0xff);
        out.write((value >> 24) & 0xff);
    }

    private void writeLEShort(ByteArrayOutputStream out, int value) {
        out.write(value & 0xff);
        out.write((value >> 8) & 0xff);
    }

    private void updateVoiceState() {
        if (voiceState == null) {
            return;
        }
        String voice = voiceInput.getText().toString().trim();
        voiceState.setText(voice.isEmpty()
                ? "未保存音色：请先点「先录制我的音色」"
                : "当前音色：" + voice);
        voiceState.setTextColor(voice.isEmpty()
                ? Color.rgb(251, 191, 36)
                : Color.rgb(108, 124, 146));
    }

    private void setEnrollEnabled(final boolean enabled) {
        mainHandler.post(new Runnable() {
            @Override
            public void run() {
                if (enrollButton != null) {
                    enrollButton.setEnabled(enabled);
                }
            }
        });
    }

    private void startSession(String key, String voice) {
        if (running) {
            return;
        }
        running = true;
        responseActive = false;
        pendingTranslation = false;
        startButton.setText("停止同传");
        setStatus("连接中…", Color.rgb(251, 191, 36));
        transcriptView.setText("");
        translationView.setText("");
        initAudioTrack();

        Map<String, String> headers = new HashMap<>();
        headers.put("Authorization", "Bearer " + key);
        URI uri = URI.create("wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model=" + MODEL);

        ws = new WebSocketClient(uri, new Draft_6455(), headers, 15000) {
            @Override
            public void onOpen(ServerHandshake handshake) {
                sendSessionUpdate(voice);
            }

            @Override
            public void onMessage(String message) {
                handleServerMessage(message);
            }

            @Override
            public void onClose(int code, String reason, boolean remote) {
                if (running) {
                    running = false;
                    releaseAudio();
                    setStatus("连接已断开：" + reason, Color.rgb(248, 113, 113));
                    setStartLabel("开始同传");
                }
            }

            @Override
            public void onError(Exception ex) {
                setStatus("连接错误：" + (ex == null ? "unknown" : ex.getMessage()),
                        Color.rgb(248, 113, 113));
            }
        };
        ws.setConnectionLostTimeout(60);
        ws.connect();
    }

    private void requestTranslation() {
        if (ws == null) {
            return;
        }
        if (responseActive) {
            pendingTranslation = true;
            return;
        }
        try {
            JSONObject response = new JSONObject()
                    .put("instructions", INSTRUCTIONS)
                    .put("modalities", new JSONArray().put("text"));
            JSONObject message = new JSONObject()
                    .put("type", "response.create")
                    .put("response", response);
            responseActive = true;
            ws.send(message.toString());
        } catch (Exception ex) {
            responseActive = false;
            setStatus("翻译请求失败：" + ex.getMessage(), Color.rgb(248, 113, 113));
        }
    }

    private void startProfessionalTts(final String text) {
        synchronized (this) {
            if (ttsBusy) {
                pendingTts.append(' ').append(text);
                return;
            }
            ttsBusy = true;
        }
        setStatus("专业音色合成中…", Color.rgb(251, 191, 36));
        new Thread(new Runnable() {
            @Override
            public void run() {
                String failure = null;
                ttsPlaying = true;
                ttsGuardUntil = Long.MAX_VALUE;
                try {
                    speakProfessional(text);
                } catch (Exception ex) {
                    failure = ex.getMessage();
                } finally {
                    ttsPlaying = false;
                    ttsGuardUntil = System.currentTimeMillis() + 900;
                    ttsBusy = false;
                    String next;
                    synchronized (MainActivity.this) {
                        next = pendingTts.toString().trim();
                        pendingTts.setLength(0);
                    }
                    if (next.isEmpty()) {
                        final String status = failure == null
                                ? "本轮完成，继续说话…"
                                : "专业音色合成失败：" + failure;
                        final int color = failure == null
                                ? Color.rgb(20, 184, 166)
                                : Color.rgb(248, 113, 113);
                        setStatus(status, color);
                    } else {
                        startProfessionalTts(next);
                    }
                }
            }
        }, "rti-pro-tts").start();
    }

    private void speakProfessional(String text) throws Exception {
        String key = apiKeyInput.getText().toString().trim();
        if (key.isEmpty()) {
            throw new RuntimeException("API Key 为空");
        }
        if (proVoice == null || proVoice.trim().isEmpty()) {
            throw new RuntimeException("专业音色 ID 为空");
        }
        JSONObject input = new JSONObject()
                .put("text", text)
                .put("voice", proVoice)
                .put("format", "pcm")
                .put("sample_rate", OUT_RATE)
                .put("language_hints", new JSONArray().put("en"));
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
            InputStream error = connection.getErrorStream();
            String detail = readAll(error);
            throw new RuntimeException("HTTP " + status + " " + detail);
        }
        BufferedReader reader = new BufferedReader(
                new InputStreamReader(connection.getInputStream(), StandardCharsets.UTF_8));
        StringBuilder sseData = new StringBuilder();
        int audioBytes = 0;
        String line;
        while ((line = reader.readLine()) != null) {
            if (line.startsWith("data:")) {
                sseData.append(line.substring(5).trim()).append('\n');
                continue;
            }
            if (!line.isEmpty() || sseData.length() == 0) {
                continue;
            }
            String body = sseData.toString().trim();
            sseData.setLength(0);
            if (body.isEmpty()) {
                continue;
            }
            JSONObject event = new JSONObject(body);
            JSONObject eventOutput = event.optJSONObject("output");
            if (eventOutput == null) {
                continue;
            }
            JSONObject audio = eventOutput.optJSONObject("audio");
            String encoded = audio == null ? "" : audio.optString("data", "");
            if (!encoded.isEmpty()) {
                byte[] pcm = Base64.decode(encoded, Base64.DEFAULT);
                audioBytes += writePlaybackPcm(pcm);
            }
            if ("stop".equals(eventOutput.optString("finish_reason"))) {
                break;
            }
        }
        reader.close();
        if (audioBytes == 0) {
            throw new RuntimeException("TTS 没有返回音频");
        }
    }

    private int writePlaybackPcm(byte[] pcm) {
        if (pcm.length < 2 || audioTrack == null) {
            return 0;
        }
        short[] samples = new short[pcm.length / 2];
        ByteBuffer.wrap(pcm).order(ByteOrder.LITTLE_ENDIAN).asShortBuffer().get(samples);
        audioTrack.write(samples, 0, samples.length);
        return samples.length * 2;
    }

    private String readAll(InputStream stream) {
        if (stream == null) {
            return "";
        }
        try {
            ByteArrayOutputStream buffer = new ByteArrayOutputStream();
            byte[] chunk = new byte[4096];
            int count;
            while ((count = stream.read(chunk)) > 0) {
                buffer.write(chunk, 0, count);
            }
            return new String(buffer.toByteArray(), StandardCharsets.UTF_8);
        } catch (Exception ex) {
            return "";
        } finally {
            try {
                stream.close();
            } catch (Exception ignored) {
            }
        }
    }

    private void sendSessionUpdate(String voice) {
        try {
            JSONObject session = new JSONObject();
            session.put("modalities", new JSONArray().put("text"));
            session.put("instructions", INSTRUCTIONS);
            session.put(
                    "turn_detection",
                    new JSONObject().put("type", "server_vad").put("create_response", false));

            JSONObject inputFormat = new JSONObject()
                    .put("type", "pcm")
                    .put("sample_rate", IN_RATE)
                    .put("sample_format", "s16le")
                    .put("channels", 1)
                    .put("packing", "interleaved")
                    .put("channel_layout", "mono");
            JSONObject outputFormat = new JSONObject()
                    .put("type", "pcm")
                    .put("sample_rate", OUT_RATE);

            JSONObject audio = new JSONObject()
                    .put("input", new JSONObject().put("format", inputFormat))
                    .put("output", new JSONObject()
                            .put("voice", voice)
                            .put("format", outputFormat));
            session.put("audio", audio);

            JSONObject message = new JSONObject()
                    .put("type", "session.update")
                    .put("session", session);
            ws.send(message.toString());
        } catch (Exception ex) {
            setStatus("会话配置失败：" + ex.getMessage(), Color.rgb(248, 113, 113));
        }
    }

    private void handleServerMessage(String message) {
        try {
            JSONObject event = new JSONObject(message);
            String type = event.optString("type", "");
            if ("session.updated".equals(type)) {
                startRecording();
                setStatus("已连接，请说中文", Color.rgb(20, 184, 166));
            } else if ("response.created".equals(type)) {
                responseActive = true;
                translationBuffer.setLength(0);
            } else if ("input_audio_buffer.speech_stopped".equals(type)) {
                requestTranslation();
            } else if ("response.audio.delta".equals(type)) {
                byte[] pcmBytes = Base64.decode(event.optString("delta", ""), Base64.DEFAULT);
                short[] samples = new short[pcmBytes.length / 2];
                ByteBuffer.wrap(pcmBytes).order(ByteOrder.LITTLE_ENDIAN).asShortBuffer().get(samples);
                // 克隆音色输出电平偏低，固定提升 1.6 倍，避免“有的话听不见”。
                for (int i = 0; i < samples.length; i++) {
                    int amplified = Math.round(samples[i] * 1.6f);
                    if (amplified > 32767) {
                        amplified = 32767;
                    } else if (amplified < -32768) {
                        amplified = -32768;
                    }
                    samples[i] = (short) amplified;
                }
                if (audioTrack != null) {
                    audioTrack.write(samples, 0, samples.length);
                }
            } else if ("response.audio_transcript.delta".equals(type)) {
                String delta = event.optString("delta", "");
                translationBuffer.append(delta);
                append(translationView, delta);
            } else if ("response.text.delta".equals(type)) {
                String delta = event.optString("delta", "");
                translationBuffer.append(delta);
                append(translationView, delta);
            } else if ("conversation.item.input_audio_transcription.delta".equals(type)) {
                append(transcriptView, event.optString("delta", ""));
            } else if ("conversation.item.input_audio_transcription.completed".equals(type)) {
                append(transcriptView, event.optString("transcript", "") + "\n");
            } else if ("response.done".equals(type)) {
                responseActive = false;
                String translated = translationBuffer.toString().trim();
                translationBuffer.setLength(0);
                if (!translated.isEmpty()) {
                    startProfessionalTts(translated);
                } else {
                    setStatus("本轮完成，继续说话…", Color.rgb(20, 184, 166));
                }
                if (pendingTranslation) {
                    pendingTranslation = false;
                    requestTranslation();
                }
            } else if ("error".equals(type)) {
                JSONObject error = event.optJSONObject("error");
                String text = error == null ? event.toString() : error.optString("message", "");
                setStatus("错误：" + text, Color.rgb(248, 113, 113));
            }
        } catch (Exception ex) {
            setStatus("解析消息失败：" + ex.getMessage(), Color.rgb(248, 113, 113));
        }
    }

    private void startRecording() {
        if (!running || audioRecord != null) {
            return;
        }
        int minBuffer = AudioRecord.getMinBufferSize(
                IN_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
        int bufferSize = Math.max(minBuffer, IN_RATE / 2);
        try {
            audioRecord = new AudioRecord(
                    MediaRecorder.AudioSource.VOICE_COMMUNICATION,
                    IN_RATE,
                    AudioFormat.CHANNEL_IN_MONO,
                    AudioFormat.ENCODING_PCM_16BIT,
                    bufferSize);
        } catch (Exception ex) {
            setStatus("打开麦克风失败：" + ex.getMessage(), Color.rgb(248, 113, 113));
            return;
        }
        if (audioRecord.getState() != AudioRecord.STATE_INITIALIZED) {
            setStatus("麦克风初始化失败", Color.rgb(248, 113, 113));
            return;
        }
        try {
            if (AcousticEchoCanceler.isAvailable()) {
                echoCanceler = AcousticEchoCanceler.create(audioRecord.getAudioSessionId());
                if (echoCanceler != null) {
                    echoCanceler.setEnabled(true);
                }
            }
        } catch (Exception ignored) {
        }
        audioRecord.startRecording();
        recordThread = new Thread(new Runnable() {
            @Override
            public void run() {
                short[] frame = new short[320];
                while (running) {
                    int count = audioRecord.read(frame, 0, frame.length);
                    if (count <= 0) {
                        continue;
                    }
                    // 半双工防回授：TTS 播放期间和结束后 900ms 内不上传麦克风，
                    // 否则扬声器里的译文会被再次识别，造成重复翻译。
                    if (ttsPlaying || System.currentTimeMillis() < ttsGuardUntil) {
                        continue;
                    }
                    byte[] bytes = new byte[count * 2];
                    for (int i = 0; i < count; i++) {
                        bytes[2 * i] = (byte) (frame[i] & 0xff);
                        bytes[2 * i + 1] = (byte) ((frame[i] >> 8) & 0xff);
                    }
                    String encoded = Base64.encodeToString(bytes, Base64.NO_WRAP);
                    try {
                        if (ws != null && ws.isOpen()) {
                            ws.send(new JSONObject()
                                    .put("type", "input_audio_buffer.append")
                                    .put("audio", encoded)
                                    .toString());
                        }
                    } catch (Exception ignored) {
                        // Keep recording; a single dropped frame is harmless.
                    }
                }
            }
        }, "rti-mic");
        recordThread.start();
    }

    private void initAudioTrack() {
        if (audioTrack != null) {
            return;
        }
        int minBuffer = AudioTrack.getMinBufferSize(
                OUT_RATE, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT);
        audioTrack = new AudioTrack.Builder()
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
        audioTrack.play();
    }

    private void stopSession(String status) {
        running = false;
        responseActive = false;
        pendingTranslation = false;
        releaseAudio();
        if (ws != null) {
            try {
                ws.close();
            } catch (Exception ignored) {
            }
            ws = null;
        }
        setStatus(status, Color.rgb(148, 163, 184));
        setStartLabel("开始同传");
    }

    private void releaseAudio() {
        if (echoCanceler != null) {
            try {
                echoCanceler.setEnabled(false);
                echoCanceler.release();
            } catch (Exception ignored) {
            }
            echoCanceler = null;
        }
        if (recordThread != null) {
            try {
                recordThread.join(500);
            } catch (InterruptedException ignored) {
            }
            recordThread = null;
        }
        if (audioRecord != null) {
            try {
                audioRecord.stop();
            } catch (Exception ignored) {
            }
            audioRecord.release();
            audioRecord = null;
        }
        if (audioTrack != null) {
            try {
                audioTrack.pause();
                audioTrack.flush();
            } catch (Exception ignored) {
            }
            audioTrack.release();
            audioTrack = null;
        }
    }

    @Override
    protected void onDestroy() {
        if (running) {
            stopSession("已停止");
        } else {
            releaseAudio();
        }
        super.onDestroy();
    }

    private void append(final TextView view, final String text) {
        if (text == null || text.isEmpty()) {
            return;
        }
        mainHandler.post(new Runnable() {
            @Override
            public void run() {
                view.append(text);
            }
        });
    }

    private void setStatus(final String text, final int color) {
        mainHandler.post(new Runnable() {
            @Override
            public void run() {
                statusView.setText(text);
                statusView.setTextColor(color);
            }
        });
    }

    private void setStartLabel(final String text) {
        mainHandler.post(new Runnable() {
            @Override
            public void run() {
                startButton.setText(text);
            }
        });
    }

    private void toast(final String text) {
        Toast.makeText(this, text, Toast.LENGTH_SHORT).show();
    }
}
