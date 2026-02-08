"""Core PDF text replacement engine using PyMuPDF (fitz).

Finds target words/phrases in a PDF and replaces them with new text,
preserving original font, size, color, and position.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)


@dataclass
class SpanInfo:
    """Metadata for a single text span extracted from PDF."""

    text: str
    font: str
    size: float
    color: int
    flags: int
    bbox: fitz.Rect
    origin: tuple[float, float]  # baseline insertion point (x, y)
    page_num: int


@dataclass
class ReplacementResult:
    """Result of a single text replacement operation."""

    original: str
    replacement: str
    page: int
    count: int


def _extract_spans(page: fitz.Page, page_num: int) -> list[SpanInfo]:
    """Extract all text spans from a page with full metadata including origin."""
    spans: list[SpanInfo] = []
    blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]

    for block in blocks:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                if not span["text"].strip():
                    continue
                origin = span.get("origin", (span["bbox"][0], span["bbox"][3]))
                spans.append(
                    SpanInfo(
                        text=span["text"],
                        font=span["font"],
                        size=span["size"],
                        color=span["color"],
                        flags=span["flags"],
                        bbox=fitz.Rect(span["bbox"]),
                        origin=(origin[0], origin[1]),
                        page_num=page_num,
                    )
                )
    return spans


def _resolve_font(original_font: str) -> str:
    """Map original font name to a usable Base-14 font for insertion."""
    base14 = [
        "Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Helvetica-BoldOblique",
        "Times-Roman", "Times-Bold", "Times-Italic", "Times-BoldItalic",
        "Courier", "Courier-Bold", "Courier-Oblique", "Courier-BoldOblique",
        "Symbol", "ZapfDingbats",
    ]

    font_lower = original_font.lower()
    for b14 in base14:
        if b14.lower() in font_lower:
            return b14

    if "arial" in font_lower or "helvetica" in font_lower or "sans" in font_lower:
        if "bold" in font_lower and ("italic" in font_lower or "oblique" in font_lower):
            return "Helvetica-BoldOblique"
        if "bold" in font_lower:
            return "Helvetica-Bold"
        if "italic" in font_lower or "oblique" in font_lower:
            return "Helvetica-Oblique"
        return "Helvetica"

    if "times" in font_lower or "serif" in font_lower:
        if "bold" in font_lower and "italic" in font_lower:
            return "Times-BoldItalic"
        if "bold" in font_lower:
            return "Times-Bold"
        if "italic" in font_lower:
            return "Times-Italic"
        return "Times-Roman"

    if "courier" in font_lower or "mono" in font_lower:
        if "bold" in font_lower:
            return "Courier-Bold"
        if "italic" in font_lower or "oblique" in font_lower:
            return "Courier-Oblique"
        return "Courier"

    return "Helvetica"


def _int_to_rgb(color_int: int) -> tuple[float, float, float]:
    """Convert integer color (0xRRGGBB) to (r, g, b) floats 0..1."""
    r = ((color_int >> 16) & 0xFF) / 255.0
    g = ((color_int >> 8) & 0xFF) / 255.0
    b = (color_int & 0xFF) / 255.0
    return (r, g, b)


def _try_extract_font(doc: fitz.Document, page: fitz.Page, font_name: str) -> str | None:
    """Try to extract an embedded font and register it for reuse.

    Returns the registered fontname if successful, None otherwise.
    """
    try:
        fonts = page.get_fonts(full=True)
        for font_info in fonts:
            xref = font_info[0]
            fname = font_info[3]  # font basename
            refname = font_info[4]  # reference name used in page

            if fname == font_name or refname == font_name:
                font_data = doc.extract_font(xref)
                if font_data and font_data[3]:  # has binary content
                    registered = page.insert_font(
                        fontname=refname,
                        fontbuffer=font_data[3],
                    )
                    if registered:
                        return registered
    except Exception as e:
        logger.debug("Could not extract font '%s': %s", font_name, e)
    return None


def _replace_on_page(
    doc: fitz.Document,
    page: fitz.Page,
    page_num: int,
    search_text: str,
    replace_text: str,
    case_sensitive: bool,
) -> int:
    """Replace all occurrences of search_text with replace_text on a page.

    Uses span-level metadata for precise font, size, color, and baseline
    position matching.

    Returns the number of replacements made.
    """
    # Find all instances using PyMuPDF search
    text_instances = page.search_for(search_text)
    if not text_instances:
        return 0

    # Extract all spans with full metadata (including origin/baseline)
    spans = _extract_spans(page, page_num)

    # For each found instance, collect its specific font properties
    instance_data: list[dict] = []
    for inst_rect in text_instances:
        # Find the span that best overlaps this instance
        best_span: SpanInfo | None = None
        best_overlap = 0.0

        for span in spans:
            intersection = span.bbox & inst_rect  # intersection rect
            if intersection.is_empty:
                continue
            overlap = intersection.width * intersection.height
            if overlap > best_overlap:
                best_overlap = overlap
                best_span = span

        if best_span:
            # Use the span's exact origin for baseline positioning
            # Adjust x to match the search hit's x position
            insert_x = inst_rect.x0
            insert_y = best_span.origin[1]

            instance_data.append({
                "rect": inst_rect,
                "font": best_span.font,
                "size": best_span.size,
                "color": best_span.color,
                "flags": best_span.flags,
                "origin": (insert_x, insert_y),
            })
        else:
            # Fallback: estimate baseline from bbox
            # Baseline is typically ~80% down from top of bbox
            baseline_y = inst_rect.y0 + (inst_rect.height * 0.82)
            instance_data.append({
                "rect": inst_rect,
                "font": "Helvetica",
                "size": 11.0,
                "color": 0,
                "flags": 0,
                "origin": (inst_rect.x0, baseline_y),
            })

    if not instance_data:
        return 0

    # Phase 1: Add redaction annotations for all instances
    for data in instance_data:
        page.add_redact_annot(data["rect"])

    # Apply all redactions at once (removes old text)
    page.apply_redactions()

    # Phase 2: Insert new text at each position with original formatting
    count = 0
    for data in instance_data:
        font_name_original = data["font"]
        font_size = data["size"]
        font_color = _int_to_rgb(data["color"])
        origin = data["origin"]

        # Try to reuse the embedded font first
        registered_font = _try_extract_font(doc, page, font_name_original)

        if registered_font:
            fontname_to_use = registered_font
        else:
            fontname_to_use = _resolve_font(font_name_original)

        insert_point = fitz.Point(origin[0], origin[1])

        try:
            page.insert_text(
                insert_point,
                replace_text,
                fontname=fontname_to_use,
                fontsize=font_size,
                color=font_color,
            )
            count += 1
        except Exception as e:
            logger.warning(
                "Failed with font '%s' (from '%s'), trying Helvetica: %s",
                fontname_to_use, font_name_original, e,
            )
            try:
                page.insert_text(
                    insert_point,
                    replace_text,
                    fontname="helv",
                    fontsize=font_size,
                    color=font_color,
                )
                count += 1
            except Exception as e2:
                logger.error("Text insertion failed completely: %s", e2)

    return count


def replace_text(
    pdf_path: str | Path,
    replacements: dict[str, str],
    case_sensitive: bool = True,
) -> tuple[bytes, list[ReplacementResult]]:
    """Replace text in a PDF file while preserving formatting.

    Args:
        pdf_path: Path to the source PDF file.
        replacements: Mapping of old_text -> new_text.
        case_sensitive: Whether search is case-sensitive.

    Returns:
        Tuple of (modified PDF bytes, list of replacement results).

    Raises:
        FileNotFoundError: If pdf_path does not exist.
        RuntimeError: If PDF processing fails critically.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    doc = fitz.open(str(pdf_path))
    results: list[ReplacementResult] = []

    try:
        for search_text, new_text in replacements.items():
            if not search_text:
                continue

            total_count = 0
            for page_num in range(len(doc)):
                page = doc[page_num]
                count = _replace_on_page(
                    doc, page, page_num, search_text, new_text, case_sensitive
                )
                if count > 0:
                    total_count += count
                    logger.info(
                        "Page %d: replaced '%s' → '%s' (%d times)",
                        page_num + 1,
                        search_text,
                        new_text,
                        count,
                    )

            results.append(
                ReplacementResult(
                    original=search_text,
                    replacement=new_text,
                    page=-1,
                    count=total_count,
                )
            )

        output_bytes = doc.tobytes(deflate=True, garbage=4)
    finally:
        doc.close()

    return output_bytes, results
