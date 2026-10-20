"""Generate GitHub cover / architecture / flowchart PNGs."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "assets"
OUT.mkdir(parents=True, exist_ok=True)

BG = (7, 11, 18)
PANEL = (15, 26, 43)
PANEL_2 = (18, 32, 52)
TEAL = (20, 184, 166)
BLUE = (56, 189, 248)
WHITE = (241, 245, 249)
MUTED = (148, 163, 184)
MUTED_2 = (100, 116, 139)
FONT = "C:/Windows/Fonts/msyh.ttc"
FONT_BOLD = "C:/Windows/Fonts/msyhbd.ttc"


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_BOLD if bold else FONT, size, index=0)


def gradient(width: int, height: int) -> Image.Image:
    image = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(image)
    for y in range(height):
        ratio = y / max(1, height - 1)
        color = (
            int(7 + 6 * ratio),
            int(11 + 18 * ratio),
            int(18 + 26 * ratio),
        )
        draw.line([(0, y), (width, y)], fill=color)
    return image


def rounded(draw: ImageDraw.ImageDraw, box, radius=18, fill=PANEL, outline=(45, 65, 88), width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def center_text(draw, box, text, fnt, fill=WHITE, spacing=6):
    x0, y0, x1, y1 = box
    bbox = draw.multiline_textbbox((0, 0), text, font=fnt, spacing=spacing, align="center")
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.multiline_text(
        ((x0 + x1 - tw) / 2, (y0 + y1 - th) / 2),
        text,
        font=fnt,
        fill=fill,
        spacing=spacing,
        align="center",
    )


def arrow(draw, start, end, color=TEAL, width=3):
    draw.line([start, end], fill=color, width=width)
    x, y = end
    draw.polygon([(x, y), (x - 10, y - 5), (x - 10, y + 5)], fill=color)


def chip(draw, x, y, text, fnt):
    bbox = draw.textbbox((0, 0), text, font=fnt)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    rounded(draw, (x, y, x + w + 28, y + h + 16), 14, (16, 42, 48), (24, 100, 95))
    draw.text((x + 14, y + 8), text, font=fnt, fill=TEAL)


def make_cover():
    w, h = 1280, 640
    image = gradient(w, h)
    draw = ImageDraw.Draw(image)
    # accent glow
    for radius, alpha in ((360, 18), (250, 24), (140, 30)):
        overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        od.ellipse((w - 360 - radius // 2, -180, w - 360 + radius // 2, 60 + radius // 2),
                   fill=(20, 184, 166, alpha))
        image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
        draw = ImageDraw.Draw(image)

    draw.text((70, 108), "实时同传", font=font(76, True), fill=WHITE)
    draw.text((74, 200), "Realtime Interpreter", font=font(34), fill=TEAL)
    draw.text(
        (74, 258),
        "中文 / English · 保留你的声音 · 双向对谈",
        font=font(25),
        fill=MUTED,
    )
    draw.text(
        (74, 304),
        "Qwen Omni Realtime + CosyVoice 3.5 专业声音复刻",
        font=font(20),
        fill=MUTED_2,
    )

    chips = [
        "Omni 实时翻译",
        "CosyVoice 3.5",
        "30s 自动学习",
        "Windows / Android",
        "双向对谈",
    ]
    x = 74
    for text in chips:
        chip(draw, x, 378, text, font(19))
        bbox = draw.textbbox((0, 0), text, font=font(19))
        x += (bbox[2] - bbox[0]) + 45

    rounded(draw, (70, 468, w - 70, 576), 20, (12, 24, 38), (30, 55, 70))
    draw.text((96, 492), "实时翻译  ·  只翻译不对答  ·  专业音色  ·  低延迟",
              font=font(23), fill=WHITE)
    draw.text((96, 531), "a1135599372@163.com", font=font(18), fill=TEAL)
    draw.text((w - 330, 531), "github.com/clonebreakman", font=font(17), fill=MUTED)

    image.save(OUT / "cover.png", optimize=True)
    print("cover", (OUT / "cover.png").stat().st_size)


def box(draw, xy, title, subtitle, accent=TEAL, width=0):
    rounded(draw, xy, 18, PANEL_2, (38, 66, 88))
    x0, y0, x1, y1 = xy
    draw.rectangle((x0, y0, x0 + 5, y1), fill=accent)
    draw.text((x0 + 22, y0 + 18), title, font=font(21, True), fill=WHITE)
    draw.text((x0 + 22, y0 + 54), subtitle, font=font(16), fill=MUTED)


def make_architecture():
    w, h = 1500, 860
    image = gradient(w, h)
    draw = ImageDraw.Draw(image)
    draw.text((56, 42), "系统架构", font=font(40, True), fill=WHITE)
    draw.text((58, 98), "桌面 / Android 双端 · 翻译与专业音色分开处理", font=font(20), fill=MUTED)

    # top: capture to translation
    box(draw, (60, 180, 360, 300), "麦克风 / 对方音频",
        "16k PCM16 · 双通道 / 回环")
    box(draw, (430, 180, 760, 300), "Qwen Omni Realtime",
        "VAD 切句 · 每轮翻译指令\n只翻译，不对答", BLUE)
    box(draw, (830, 180, 1170, 300), "翻译文本",
        "中文 → English / English → 中文")
    arrow(draw, (360, 240), (430, 240))
    arrow(draw, (760, 240), (830, 240), BLUE)

    # right: tts
    box(draw, (1240, 180, 1440, 300), "CosyVoice 3.5",
        "专业音色 TTS\n24k PCM", TEAL)
    arrow(draw, (1170, 240), (1240, 240), TEAL)
    draw.text((1246, 312), "流式播放", font=font(15), fill=MUTED)

    # voice studio
    box(draw, (430, 400, 760, 520), "音色制作 Studio",
        "30 秒录音 · 试听 · 音量/峰值校验\nOmni 复刻 + CosyVoice 专业复刻", TEAL)
    box(draw, (830, 400, 1170, 520), "config.json / SharedPreferences",
        "voice / pro_voice\npeer_voice / peer_pro_voice")
    arrow(draw, (760, 460), (830, 460), TEAL)
    draw.line([(1170, 460), (1220, 320), (1240, 300)], fill=TEAL, width=3)
    draw.polygon([(1240, 300), (1228, 305), (1235, 316)], fill=TEAL)

    # auto learn
    box(draw, (430, 620, 760, 740), "对话中自动采集",
        "按 channel 保存语音段\n每约 30 秒触发一次", BLUE)
    box(draw, (830, 620, 1170, 740), "增量专业复刻",
        "后台重做 CosyVoice voice\n更新后续 TTS", BLUE)
    arrow(draw, (760, 680), (830, 680), BLUE)
    arrow(draw, (1000, 620), (1000, 520), BLUE)
    draw.text((60, 790), "Windows: FastAPI 网关 / Android: 直连 DashScope",
             font=font(16), fill=MUTED_2)

    image.save(OUT / "architecture.png", optimize=True)
    print("architecture", (OUT / "architecture.png").stat().st_size)


def make_flowchart():
    w, h = 1500, 1000
    image = gradient(w, h)
    draw = ImageDraw.Draw(image)
    draw.text((56, 40), "项目流程图", font=font(40, True), fill=WHITE)
    draw.text((58, 96), "从音色准备到双向对谈，再到自动优化", font=font(20), fill=MUTED)

    # row 1
    box(draw, (60, 170, 360, 280), "1. 准备",
        "填 API Key\n我的/对方音色可选", BLUE)
    box(draw, (470, 170, 770, 280), "2. 双向对谈",
        "对方英文 → 中文\n我中文 → English", TEAL)
    box(draw, (880, 170, 1180, 280), "3. 翻译",
        "Omni 实时 ASR\n每轮只翻译不对答", BLUE)
    arrow(draw, (360, 225), (470, 225))
    arrow(draw, (770, 225), (880, 225), BLUE)

    # row 2
    box(draw, (880, 350, 1180, 460), "4. 专业 TTS",
        "CosyVoice 3.5\n对应 channel 音色", TEAL)
    box(draw, (470, 350, 770, 460), "5. 播放",
        "24k PCM\n你听中文 / 对方听英文", TEAL)
    box(draw, (60, 350, 360, 460), "6. 自动采集",
        "保存该 channel 语音段\n累计约 30 秒", BLUE)
    arrow(draw, (1180, 225), (1180, 350), TEAL)
    arrow(draw, (880, 405), (770, 405), TEAL)
    arrow(draw, (470, 405), (360, 405), BLUE)

    # row 3
    box(draw, (60, 540, 360, 650), "7. 后台复刻",
        "CosyVoice 3.5\n新专业音色", TEAL)
    box(draw, (470, 540, 770, 650), "8. 更新",
        "写回 pro_voice /\npeer_pro_voice", BLUE)
    box(draw, (880, 540, 1180, 650), "9. 后续轮次",
        "新音色用于后续 TTS\n无需手动重录", TEAL)
    arrow(draw, (360, 595), (470, 595), BLUE)
    arrow(draw, (770, 595), (880, 595), TEAL)

    # loop arrow
    draw.arc((120, 700, 1080, 870), 200, 340, fill=BLUE, width=3)
    draw.text((430, 880), "持续对话 → 采集 → 更新（约 30 秒/次）", font=font(17), fill=MUTED)
    draw.polygon([(1080, 785), (1070, 795), (1080, 805)], fill=BLUE)

    # legend / notes
    draw.text((60, 930), "安全：API Key 仅本机明文存储，不进入浏览器 localStorage；config.json / SharedPreferences 不分享。",
              font=font(15), fill=MUTED_2)

    image.save(OUT / "flowchart.png", optimize=True)
    print("flowchart", (OUT / "flowchart.png").stat().st_size)


def main():
    make_cover()
    make_architecture()
    make_flowchart()


if __name__ == "__main__":
    main()
