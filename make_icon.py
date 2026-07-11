"""生成应用图标 app.ico: 蓝色圆角块 + 白色"译"字, 与托盘图标一致。

多尺寸适配:
  - 背景占满整个图标区域(margin=0), 桌面快捷方式不突兀
  - 小尺寸(16/32)字体占比更大, 确保"译"字清晰
  - 大尺寸保持比例协调
"""
from PIL import Image, ImageDraw, ImageFont


def make_icon(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 蓝色圆角块占满整个图标(无透明边距, 桌面显示不突兀)
    radius = max(2, size // 6)  # 小尺寸圆角也要可见
    d.rounded_rectangle(
        [0, 0, size - 1, size - 1],
        radius=radius,
        fill=(74, 144, 226, 255),  # #4a90e2
    )

    # 白色"译"字: 小尺寸占比更大保证清晰
    font_ratio = 0.72 if size <= 32 else 0.62
    font_size = max(8, int(size * font_ratio))
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc", font_size)
    except Exception:
        try:
            font = ImageFont.truetype("C:/Windows/Fonts/simhei.ttf", font_size)
        except Exception:
            font = ImageFont.load_default()

    text = "译"
    bbox = d.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (size - tw) // 2 - bbox[0]
    y = (size - th) // 2 - bbox[1]
    d.text((x, y), text, fill=(255, 255, 255, 255), font=font)
    return img


# Windows 需要多种尺寸: 16(托盘/标题栏), 32(任务栏), 48(桌面默认),
# 64(大图标), 128(超大图标), 256(资源管理器大预览)
sizes = [16, 32, 48, 64, 128, 256]
images = [make_icon(s) for s in sizes]
images[0].save(
    "app.ico",
    format="ICO",
    sizes=[(s, s) for s in sizes],
    append_images=images[1:],
)
print("app.ico generated with sizes:", sizes)
