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
    page_num: int


@dataclass
class ReplacementResult:
    """Result of a single text replacement operation."""

    original: str
    replacement: str
    page: int
    count: int


def _extract_spans(page: fitz.Page, page_num: int) -> list[SpanInfo]:
    """Extract all text spans from a page with full metadata."""
    spans: list[SpanInfo] = []
    blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]

    for block in blocks:
        if block.get("type") != 0:  # text block
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                if not span["text"].strip():
                    continue
                spans.append(
                    SpanInfo(
                        text=span["text"],
                        font=span["font"],
                        size=span["size"],
                        color=span["color"],
                        flags=span["flags"],
                        bbox=fitz.Rect(span["bbox"]),
                        page_num=page_num,
                    )
                )
    return spans


def _find_and_collect_spans(
    spans: list[SpanInfo],
    search_text: str,
    case_sensitive: bool,
) -> list[list[SpanInfo]]:
    """Find occurrences of search_text that may span across multiple spans.

    Returns a list of span groups, where each group forms one match.
    """
    matches: list[list[SpanInfo]] = []

    def _normalize(t: str) -> str:
        return t if case_sensitive else t.lower()

    search_norm = _normalize(search_text)
    i = 0

    while i < len(spans):
        # Try to build the search text by concatenating consecutive spans
        combined = ""
        group: list[SpanInfo] = []

        for j in range(i, len(spans)):
            span = spans[j]
            combined += _normalize(span.text)
            group.append(span)

            if search_norm in combined:
                matches.append(list(group))
                break

            # Stop if combined text is already longer than search text
            # and doesn't contain it
            if len(combined) > len(search_norm) * 2:
                break

        i += 1

    return matches


def _resolve_font(
    doc: fitz.Document,
    page: fitz.Page,
    original_font: str,
    target_text: str,
) -> str:
    """Try to resolve a usable font name for insertion.

    PyMuPDF can only insert text with Base-14 fonts or fonts that are
    registered. If the original font is embedded and not usable for
    insertion, fall back to a reasonable Base-14 alternative.
    """
    base14 = [
        "Helvetica",
        "Helvetica-Bold",
        "Helvetica-Oblique",
        "Helvetica-BoldOblique",
        "Times-Roman",
        "Times-Bold",
        "Times-Italic",
        "Times-BoldItalic",
        "Courier",
        "Courier-Bold",
        "Courier-Oblique",
        "Courier-BoldOblique",
        "Symbol",
        "ZapfDingbats",
    ]

    # Check if font name matches a base14 font
    font_lower = original_font.lower()
    for b14 in base14:
        if b14.lower() in font_lower:
            return b14

    # Heuristic mapping
    if "arial" in font_lower or "helvetica" in font_lower or "sans" in font_lower:
        if "bold" in font_lower and "italic" in font_lower:
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

    # Default fallback
    return "Helvetica"


def _int_to_rgb(color_int: int) -> tuple[float, float, float]:
    """Convert integer color (0xRRGGBB) to (r, g, b) floats 0..1."""
    r = ((color_int >> 16) & 0xFF) / 255.0
    g = ((color_int >> 8) & 0xFF) / 255.0
    b = (color_int & 0xFF) / 255.0
    return (r, g, b)


def _replace_on_page(
    doc: fitz.Document,
    page: fitz.Page,
    page_num: int,
    search_text: str,
    replace_text: str,
    case_sensitive: bool,
) -> int:
    """Replace all occurrences of search_text with replace_text on a page.

    Returns the number of replacements made.
    """
    count = 0

    # Use PyMuPDF's built-in search to find text locations
    flags = 0 if case_sensitive else fitz.TEXT_PRESERVE_WHITESPACE
    text_instances = page.search_for(search_text)

    if not text_instances:
        return 0

    # Extract spans to get font metadata for the first occurrence
    spans = _extract_spans(page, page_num)
    ref_span: SpanInfo | None = None

    # Find the span that overlaps with the first found instance
    for inst_rect in text_instances:
        for span in spans:
            if span.bbox.intersects(inst_rect):
                ref_span = span
                break
        if ref_span:
            break

    if not ref_span:
        # Fallback: use default styling
        ref_span = SpanInfo(
            text="",
            font="Helvetica",
            size=11.0,
            color=0,
            flags=0,
            bbox=fitz.Rect(),
            page_num=page_num,
        )

    # Resolve a usable font
    font_name = _resolve_font(doc, page, ref_span.font, replace_text)
    font_size = ref_span.size
    font_color = _int_to_rgb(ref_span.color)

    # Apply redactions for each instance, then insert new text
    for inst_rect in text_instances:
        # Add redaction annotation (white rectangle over old text)
        annot = page.add_redact_annot(inst_rect)
        count += 1

    # Apply all redactions at once
    page.apply_redactions()

    # Now re-search won't work since text is gone,
    # so we insert at the saved positions
    for inst_rect in text_instances:
        # Calculate text insertion point (left-bottom of bbox)
        insert_point = fitz.Point(inst_rect.x0, inst_rect.y1 - 2)

        # Adjust font size if replacement text is longer
        adjusted_size = font_size
        available_width = inst_rect.width
        text_width = fitz.get_text_length(replace_text, fontname=font_name, fontsize=font_size)

        if text_width > available_width and available_width > 0:
            # Scale font size down to fit
            ratio = available_width / text_width
            adjusted_size = max(font_size * ratio, 4.0)  # minimum 4pt

        try:
            page.insert_text(
                insert_point,
                replace_text,
                fontname=font_name,
                fontsize=adjusted_size,
                color=font_color,
            )
        except Exception as e:
            logger.warning(
                "Failed to insert text with font %s, falling back to Helvetica: %s",
                font_name,
                e,
            )
            page.insert_text(
                insert_point,
                replace_text,
                fontname="helv",
                fontsize=adjusted_size,
                color=font_color,
            )

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
                    page=-1,  # -1 means all pages
                    count=total_count,
                )
            )

        output_bytes = doc.tobytes(deflate=True, garbage=4)
    finally:
        doc.close()

    return output_bytes, results
