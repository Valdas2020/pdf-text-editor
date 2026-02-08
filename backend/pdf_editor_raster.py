"""Raster-based PDF text replacement fallback.

Converts PDF pages to images, uses OCR to locate text,
paints over original text, and draws replacement text.
Used when PyMuPDF cannot handle embedded/encrypted fonts.
"""

import logging
from io import BytesIO
from pathlib import Path

import pytesseract
from pdf2image import convert_from_path
from PIL import Image, ImageDraw, ImageFont

from pdf_editor import ReplacementResult

logger = logging.getLogger(__name__)

# DPI for rasterization
RENDER_DPI = 300

# Try to find a usable TrueType font
_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/noto/NotoSans-Regular.ttf",
    "C:/Windows/Fonts/arial.ttf",
]


def _find_system_font() -> str | None:
    """Find the first available TrueType font on the system."""
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            return path
    return None


def _get_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Get a font at the given pixel size, with fallback to default."""
    font_path = _find_system_font()
    if font_path:
        try:
            return ImageFont.truetype(font_path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def _detect_background_color(
    img: Image.Image, bbox: tuple[int, int, int, int]
) -> tuple[int, int, int]:
    """Estimate the background color around a text bounding box.

    Samples pixels just outside the bbox edges and returns the most
    common color (simple mode estimation).
    """
    x0, y0, x1, y1 = bbox
    w, h = img.size
    pixels: list[tuple[int, int, int]] = []

    # Sample pixels above the bbox
    for x in range(max(0, x0), min(w, x1)):
        if y0 - 1 >= 0:
            pixels.append(img.getpixel((x, y0 - 1))[:3])  # type: ignore[index]
    # Sample pixels below
    for x in range(max(0, x0), min(w, x1)):
        if y1 < h:
            pixels.append(img.getpixel((x, y1))[:3])  # type: ignore[index]

    if not pixels:
        return (255, 255, 255)  # default white

    # Simple mode: most common color
    from collections import Counter

    counter = Counter(pixels)
    return counter.most_common(1)[0][0]


def _ocr_and_replace(
    img: Image.Image,
    replacements: dict[str, str],
) -> tuple[Image.Image, dict[str, int]]:
    """Run OCR on an image and replace matching text.

    Returns the modified image and a count of replacements per search term.
    """
    # Get word-level bounding boxes via pytesseract
    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    counts: dict[str, int] = {k: 0 for k in replacements}
    draw = ImageDraw.Draw(img)

    n_boxes = len(data["text"])
    for search_text, replace_text in replacements.items():
        search_lower = search_text.lower()

        # Single-word search
        for i in range(n_boxes):
            word = data["text"][i].strip()
            if not word:
                continue

            if word.lower() == search_lower or search_lower in word.lower():
                x = data["left"][i]
                y = data["top"][i]
                w = data["width"][i]
                h = data["height"][i]

                if w <= 0 or h <= 0:
                    continue

                bbox = (x, y, x + w, y + h)

                # Detect background color and paint over
                bg_color = _detect_background_color(img, bbox)
                draw.rectangle(bbox, fill=bg_color)

                # Calculate font size to fit the bbox
                font_size = max(int(h * 0.85), 8)
                font = _get_font(font_size)

                # If replacement text is the full word replacement
                if word.lower() == search_lower:
                    new_word = replace_text
                else:
                    # Partial replacement within the word
                    import re

                    new_word = re.sub(
                        re.escape(search_text), replace_text, word, flags=re.IGNORECASE
                    )

                # Draw new text
                draw.text((x, y), new_word, fill=(0, 0, 0), font=font)
                counts[search_text] += 1

        # Multi-word phrase search: concatenate consecutive words
        if " " in search_text:
            words_in_phrase = search_text.lower().split()
            phrase_len = len(words_in_phrase)

            for i in range(n_boxes - phrase_len + 1):
                # Check if consecutive words match the phrase
                window = []
                for j in range(phrase_len):
                    w = data["text"][i + j].strip().lower()
                    window.append(w)

                if window == words_in_phrase:
                    # Calculate combined bounding box
                    x0 = data["left"][i]
                    y0 = min(data["top"][i + j] for j in range(phrase_len))
                    x1 = max(
                        data["left"][i + j] + data["width"][i + j]
                        for j in range(phrase_len)
                    )
                    y1 = max(
                        data["top"][i + j] + data["height"][i + j]
                        for j in range(phrase_len)
                    )
                    h = y1 - y0

                    bbox = (x0, y0, x1, y1)
                    bg_color = _detect_background_color(img, bbox)
                    draw.rectangle(bbox, fill=bg_color)

                    font_size = max(int(h * 0.85), 8)
                    font = _get_font(font_size)
                    draw.text((x0, y0), replace_text, fill=(0, 0, 0), font=font)
                    counts[search_text] += 1

    return img, counts


def replace_text_raster(
    pdf_path: str | Path,
    replacements: dict[str, str],
) -> tuple[bytes, list[ReplacementResult]]:
    """Replace text in PDF using raster (OCR) method.

    Converts each page to an image, finds text via OCR, replaces it,
    and reassembles into a PDF.

    Args:
        pdf_path: Path to the source PDF file.
        replacements: Mapping of old_text -> new_text.

    Returns:
        Tuple of (modified PDF bytes, list of replacement results).

    Raises:
        FileNotFoundError: If pdf_path does not exist.
        RuntimeError: If OCR or image processing fails.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    # Convert PDF to images
    try:
        images = convert_from_path(str(pdf_path), dpi=RENDER_DPI)
    except Exception as e:
        raise RuntimeError(f"Failed to convert PDF to images: {e}")

    total_counts: dict[str, int] = {k: 0 for k in replacements}
    processed_images: list[Image.Image] = []

    for page_num, img in enumerate(images):
        logger.info("Processing page %d (raster method)...", page_num + 1)
        img_rgb = img.convert("RGB")
        processed, page_counts = _ocr_and_replace(img_rgb, replacements)
        processed_images.append(processed)

        for k, v in page_counts.items():
            total_counts[k] += v

    # Assemble back into PDF
    if not processed_images:
        raise RuntimeError("No pages were processed")

    output_buf = BytesIO()
    first_img = processed_images[0]
    if len(processed_images) > 1:
        first_img.save(
            output_buf,
            format="PDF",
            save_all=True,
            append_images=processed_images[1:],
            resolution=RENDER_DPI,
        )
    else:
        first_img.save(output_buf, format="PDF", resolution=RENDER_DPI)

    results = [
        ReplacementResult(
            original=k,
            replacement=replacements[k],
            page=-1,
            count=v,
        )
        for k, v in total_counts.items()
    ]

    return output_buf.getvalue(), results
