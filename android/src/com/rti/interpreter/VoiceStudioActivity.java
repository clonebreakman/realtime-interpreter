package com.rti.interpreter;

import android.Manifest;
import android.app.Activity;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaPlayer;
import android.media.MediaRecorder;
import android.os.Bundle;
import android.text.InputType;
import android.util.Base64;
import android.util.TypedValue;
import android.view.View;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * 独立的音色制作页：录音 30 秒 -> 试听 -> 分析复刻 -> 保存为默认音色。
 */
public class VoiceStudioActivity extends Activity {
    private static final int REQ_MIC = 2001;
    private static final int RATE = 48000;
    private static final String MODEL = "qwen3.8-omni-flash-realtime";
    private static final String ENROLL_URL =
            "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization";
    private static final String DEFAULT_VOICE =
            "";
    private static final String DEFAULT_PRO_VOICE =
            "";

    private EditText apiKeyInput;
    private EditText nameInput;
    private TextView currentVoice;
    private TextView statusView;
    private TextView resultView;
    private Button recordButton;
    private Button playButton;
    private Button analyzeButton;
    private ProgressBar levelBar;

    private MediaPlayer mediaPlayer;
    private File recordingFile;
    private byte[] recordingWav;
    private volatile boolean recording = false;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        setContentView(buildUi());

        SharedPreferences prefs = getSharedPreferences("rti", MODE_PRIVATE);
        apiKeyInput.setText(prefs.getString("api_key", ""));
        nameInput.setText("我的音色");
        updateCurrentVoice();

        recordButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                requestRecord();
            }
        });
        playButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                playRecording();
            }
        });
        analyzeButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                analyzeRecording();
            }
        });
    }

    private View buildUi() {
        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.setBackgroundColor(Color.rgb(7, 11, 18));

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(dp(18), dp(22), dp(18), dp(28));
        scroll.addView(root);

        TextView title = text("音色制作", 24, Color.WHITE);
        title.setTypeface(null, android.graphics.Typeface.BOLD);
        root.addView(title);
        root.addView(text("录音 30 秒 → 试听 → 分析 → 保存为默认音色", 13, Color.rgb(148, 163, 184)));

        root.addView(text("当前默认音色", 13, Color.rgb(148, 163, 184)), topParams(18));
        currentVoice = text("—", 13, Color.rgb(20, 184, 166));
        currentVoice.setTextIsSelectable(true);
        root.addView(currentVoice, topParams(6));

        root.addView(text("API Key（保存在本机）", 13, Color.rgb(148, 163, 184)), topParams(18));
        apiKeyInput = new EditText(this);
        apiKeyInput.setHint("sk-...");
        apiKeyInput.setSingleLine(true);
        apiKeyInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        apiKeyInput.setTextColor(Color.WHITE);
        apiKeyInput.setHintTextColor(Color.rgb(100, 116, 139));
        root.addView(apiKeyInput);

        recordButton = new Button(this);
        recordButton.setText("开始录制 30 秒");
        recordButton.setTextSize(TypedValue.COMPLEX_UNIT_SP, 17);
        LinearLayout.LayoutParams recordParams = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, dp(56));
        recordParams.topMargin = dp(18);
        root.addView(recordButton, recordParams);

        levelBar = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        levelBar.setMax(100);
        levelBar.setProgress(0);
        LinearLayout.LayoutParams levelParams = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, dp(12));
        levelParams.topMargin = dp(10);
        root.addView(levelBar, levelParams);

        statusView = text("点击开始录制，安静环境连续说 30 秒。", 13, Color.rgb(251, 191, 36));
        root.addView(statusView, topParams(10));

        playButton = new Button(this);
        playButton.setText("试听刚才录音");
        playButton.setEnabled(false);
        root.addView(playButton, topParams(8));

        root.addView(text("音色名称", 13, Color.rgb(148, 163, 184)), topParams(18));
        nameInput = new EditText(this);
        nameInput.setSingleLine(true);
        nameInput.setTextColor(Color.WHITE);
        nameInput.setHintTextColor(Color.rgb(100, 116, 139));
        root.addView(nameInput);

        analyzeButton = new Button(this);
        analyzeButton.setText("分析并保存音色");
        analyzeButton.setEnabled(false);
        analyzeButton.setTextSize(TypedValue.COMPLEX_UNIT_SP, 17);
        LinearLayout.LayoutParams analyzeParams = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, dp(54));
        analyzeParams.topMargin = dp(12);
        root.addView(analyzeButton, analyzeParams);

        resultView = text("", 13, Color.rgb(20, 184, 166));
        resultView.setTextIsSelectable(true);
        root.addView(resultView, topParams(10));

        Button back = new Button(this);
        back.setText("返回同传");
        back.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                finish();
            }
        });
        root.addView(back, topParams(22));

        root.addView(text(
                "提示：录音只保存在本机；分析需要 API Key。"
                + "音色保存后，同传页会自动使用它。",
                12, Color.rgb(108, 124, 146)), topParams(16));
        return scroll;
    }

    private void requestRecord() {
        if (recording) {
            return;
        }
        String key = apiKeyInput.getText().toString().trim();
        if (key.isEmpty()) {
            toast("请先填写 API Key");
            apiKeyInput.requestFocus();
            return;
        }
        getSharedPreferences("rti", MODE_PRIVATE).edit().putString("api_key", key).apply();
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, REQ_MIC);
            return;
        }
        startRecording();
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(requestCode, permissions, results);
        if (requestCode == REQ_MIC) {
            if (results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED) {
                startRecording();
            } else {
                toast("没有麦克风权限，无法录音");
            }
        }
    }

    private void startRecording() {
        if (recording) {
            return;
        }
        recording = true;
        recordButton.setEnabled(false);
        analyzeButton.setEnabled(false);
        playButton.setEnabled(false);
        levelBar.setProgress(0);
        status("录音中：请连续说 30 秒，不要长时间停顿…", Color.rgb(251, 191, 36));

        new Thread(new Runnable() {
            @Override
            public void run() {
                AudioRecord recorder = null;
                try {
                    int minBuffer = AudioRecord.getMinBufferSize(
                            RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
                    recorder = new AudioRecord(
                            MediaRecorder.AudioSource.VOICE_RECOGNITION,
                            RATE,
                            AudioFormat.CHANNEL_IN_MONO,
                            AudioFormat.ENCODING_PCM_16BIT,
                            Math.max(minBuffer, RATE));
                    if (recorder.getState() != AudioRecord.STATE_INITIALIZED) {
                        throw new RuntimeException("麦克风初始化失败");
                    }
                    recorder.startRecording();
                    ByteArrayOutputStream pcm = new ByteArrayOutputStream();
                    short[] buffer = new short[RATE / 10];
                    long end = System.currentTimeMillis() + 30000L;
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
                        final int level = (int) Math.min(100, peak * 100 / 12000);
                        runOnUiThread(new Runnable() {
                            @Override
                            public void run() {
                                levelBar.setProgress(level);
                            }
                        });
                    }
                    try {
                        recorder.stop();
                    } catch (Exception ignored) {
                    }
                    if (peak < 164) {
                        throw new RuntimeException("录音太安静，请检查麦克风是否静音");
                    }
                    recordingWav = wavBytes(pcm.toByteArray(), RATE);
                    recordingFile = new File(getFilesDir(), "voice_recording.wav");
                    try (FileOutputStream output = new FileOutputStream(recordingFile)) {
                        output.write(recordingWav);
                    }
                    final double seconds = recordingWav.length / 2.0 / RATE;
                    runOnUiThread(new Runnable() {
                        @Override
                        public void run() {
                            recording = false;
                            recordButton.setEnabled(true);
                            recordButton.setText("重新录制 30 秒");
                            playButton.setEnabled(true);
                            analyzeButton.setEnabled(true);
                            status(
                                "录音完成：" + String.format("%.1f", seconds)
                                    + " 秒，已保存 voice_recording.wav；可试听后分析。",
                                Color.rgb(20, 184, 166));
                        }
                    });
                } catch (final Exception ex) {
                    runOnUiThread(new Runnable() {
                        @Override
                        public void run() {
                            recording = false;
                            recordButton.setEnabled(true);
                            analyzeButton.setEnabled(recordingWav != null);
                            status("录音失败：" + ex.getMessage(), Color.rgb(248, 113, 113));
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
        }, "rti-studio-recorder").start();
    }

    private void playRecording() {
        if (recordingFile == null || !recordingFile.isFile()) {
            toast("还没有录音文件");
            return;
        }
        try {
            if (mediaPlayer != null) {
                mediaPlayer.release();
                mediaPlayer = null;
            }
            mediaPlayer = new MediaPlayer();
            mediaPlayer.setDataSource(recordingFile.getAbsolutePath());
            mediaPlayer.prepare();
            mediaPlayer.start();
            status("正在播放刚才的录音…", Color.rgb(148, 163, 184));
        } catch (Exception ex) {
            status("播放失败：" + ex.getMessage(), Color.rgb(248, 113, 113));
        }
    }

    private void analyzeRecording() {
        if (recordingWav == null) {
            toast("请先录音");
            return;
        }
        String key = apiKeyInput.getText().toString().trim();
        if (key.isEmpty()) {
            toast("请先填写 API Key");
            apiKeyInput.requestFocus();
            return;
        }
        getSharedPreferences("rti", MODE_PRIVATE).edit().putString("api_key", key).apply();
        analyzeButton.setEnabled(false);
        recordButton.setEnabled(false);
        status("正在上传录音并分析，请稍候…", Color.rgb(251, 191, 36));

        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    final String voice = postEnrollment(key, recordingWav);
                    getSharedPreferences("rti", MODE_PRIVATE)
                            .edit()
                            .putString("voice", voice)
                            .apply();
                    runOnUiThread(new Runnable() {
                        @Override
                        public void run() {
                            updateCurrentVoice();
                            resultView.setText("已保存音色 ID：\n" + voice);
                            status("分析完成：已保存为默认音色。", Color.rgb(20, 184, 166));
                            analyzeButton.setEnabled(true);
                            recordButton.setEnabled(true);
                        }
                    });
                } catch (final Exception ex) {
                    runOnUiThread(new Runnable() {
                        @Override
                        public void run() {
                            status("分析失败：" + ex.getMessage(), Color.rgb(248, 113, 113));
                            analyzeButton.setEnabled(true);
                            recordButton.setEnabled(true);
                        }
                    });
                }
            }
        }, "rti-studio-enroll").start();
    }

    private String postEnrollment(String key, byte[] wav) throws Exception {
        JSONObject input = new JSONObject()
                .put("action", "create")
                .put("target_model", MODEL)
                .put("preferred_name", "studio" + (System.currentTimeMillis() % 1000000000L))
                .put("audio", new JSONObject().put(
                        "data", "data:audio/wav;base64," + Base64.encodeToString(wav, Base64.NO_WRAP)));
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
        writeInt(out, 36 + dataLength);
        writeAscii(out, "WAVE");
        writeAscii(out, "fmt ");
        writeInt(out, 16);
        writeShort(out, 1);
        writeShort(out, 1);
        writeInt(out, sampleRate);
        writeInt(out, sampleRate * 2);
        writeShort(out, 2);
        writeShort(out, 16);
        writeAscii(out, "data");
        writeInt(out, dataLength);
        out.write(pcm, 0, pcm.length);
        return out.toByteArray();
    }

    private void writeAscii(ByteArrayOutputStream out, String text) {
        try {
            out.write(text.getBytes("US-ASCII"));
        } catch (Exception ignored) {
        }
    }

    private void writeInt(ByteArrayOutputStream out, int value) {
        out.write(value & 0xff);
        out.write((value >> 8) & 0xff);
        out.write((value >> 16) & 0xff);
        out.write((value >> 24) & 0xff);
    }

    private void writeShort(ByteArrayOutputStream out, int value) {
        out.write(value & 0xff);
        out.write((value >> 8) & 0xff);
    }

    private void updateCurrentVoice() {
        SharedPreferences prefs = getSharedPreferences("rti", MODE_PRIVATE);
        String voice = prefs.getString("voice", DEFAULT_VOICE);
        if (voice == null || voice.isEmpty() || "Tina".equalsIgnoreCase(voice)) {
            voice = DEFAULT_VOICE;
        }
        String pro = prefs.getString("pro_voice", DEFAULT_PRO_VOICE);
        if (pro == null || pro.trim().isEmpty()) {
            pro = DEFAULT_PRO_VOICE;
        }
        currentVoice.setText("Omni 复刻：" + voice + "\n专业音色（CosyVoice 3.5）：" + pro);
    }

    private void status(final String text, final int color) {
        runOnUiThread(new Runnable() {
            @Override
            public void run() {
                statusView.setText(text);
                statusView.setTextColor(color);
            }
        });
    }

    private TextView text(String value, int sp, int color) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(TypedValue.COMPLEX_UNIT_SP, sp);
        view.setTextColor(color);
        view.setLineSpacing(dp(4), 1.0f);
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

    private void toast(String text) {
        Toast.makeText(this, text, Toast.LENGTH_SHORT).show();
    }

    @Override
    protected void onDestroy() {
        recording = false;
        if (mediaPlayer != null) {
            try {
                mediaPlayer.release();
            } catch (Exception ignored) {
            }
            mediaPlayer = null;
        }
        super.onDestroy();
    }
}

