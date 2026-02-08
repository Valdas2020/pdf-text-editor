"""Tiled diagonal watermark overlay for preview images."""

import math

from PIL import Image, ImageDraw, ImageFont


def add_watermark(
    image: Image.Image,
    text: str = "pdf-text-editor.onrender.com",
    opacity: int = 80,
) -> Image.Image:
    """Overlay a repeating diagonal watermark on an image.

    Args:
        image: PIL Image (RGB).
        text: Watermark text.
        opacity: Transparency 0-255 (80 ~ 30%).

    Returns:
        New Image with watermark applied.
    """
    base = image.convert("RGBA")
    w, h = base.size

    # Transparent overlay
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Adaptive font size: ~3.5% of image width
    font_size = max(int(w * 0.035), 14)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", font_size)
    except OSError:
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", font_size)
        except OSError:
            font = ImageFont.load_default()

    # Measure text
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]

    # Tile spacing
    step_x = tw + int(tw * 0.6)
    step_y = th + 150

    angle = -35
    fill = (140, 140, 140, opacity)

    # Create a larger canvas to rotate, then paste
    diag = int(math.sqrt(w * w + h * h))
    txt_layer = Image.new("RGBA", (diag * 2, diag * 2), (0, 0, 0, 0))
    txt_draw = ImageDraw.Draw(txt_layer)

    y = 0
    while y < diag * 2:
        x = 0
        while x < diag * 2:
            txt_draw.text((x, y), text, font=font, fill=fill)
            x += step_x
        y += step_y

    # Rotate
    txt_layer = txt_layer.rotate(angle, resample=Image.BICUBIC, expand=False)

    # Crop center to match original size
    cx, cy = txt_layer.size[0] // 2, txt_layer.size[1] // 2
    crop_box = (cx - w // 2, cy - h // 2, cx + w // 2, cy + h // 2)
    txt_layer = txt_layer.crop(crop_box).resize((w, h))

    # Composite
    result = Image.alpha_composite(base, txt_layer)
    return result.convert("RGB")
