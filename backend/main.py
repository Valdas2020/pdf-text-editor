"""FastAPI application for PDF Text Editor."""

import asyncio
import base64
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
from typing import AsyncGenerator

import fitz  # PyMuPDF
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image

from config import (
    BASE_DIR,
    CLEANUP_INTERVAL_MINUTES,
    HOST,
    MAX_FILE_SIZE_BYTES,
    OUTPUT_DIR,
    PORT,
    UPLOAD_DIR,
)
from payment import PAYMENT_PRICE_USD, create_invoice, verify_payment
from pdf_editor import ReplacementResult, replace_text
from watermark import add_watermark

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# Increase cleanup to 2 hours (give time for payment)
CLEANUP_SECONDS = max(CLEANUP_INTERVAL_MINUTES, 120) * 60


async def _cleanup_old_files() -> None:
    """Periodically delete files older than CLEANUP_SECONDS."""
    while True:
        await asyncio.sleep(CLEANUP_SECONDS)
        now = time.time()
        cutoff = now - CLEANUP_SECONDS
        for directory in (UPLOAD_DIR, OUTPUT_DIR):
            for f in directory.iterdir():
                if f.name == ".gitkeep":
                    continue
                try:
                    if f.stat().st_mtime < cutoff:
                        f.unlink()
                        logger.info("Cleaned up old file: %s", f.name)
                except OSError as e:
                    logger.warning("Failed to clean up %s: %s", f.name, e)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application lifecycle: start cleanup task."""
    task = asyncio.create_task(_cleanup_old_files())
    yield
    task.cancel()


app = FastAPI(
    title="PDF Text Editor",
    description="AI-powered find & replace for PDF files",
    version="1.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve frontend static files
FRONTEND_DIR = BASE_DIR / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_results(results: list[ReplacementResult]) -> list[dict]:
    """Convert replacement results to JSON-serializable dicts."""
    return [
        {"original": r.original, "replacement": r.replacement, "count": r.count}
        for r in results
    ]


def _pdf_bytes_to_preview(pdf_bytes: bytes, dpi: int = 150) -> list[str]:
    """Render PDF pages as JPEG images with watermark, return as base64 strings."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    previews: list[str] = []
    try:
        for page in doc:
            pix = page.get_pixmap(dpi=dpi)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            img = add_watermark(img)
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=80)
            previews.append(base64.b64encode(buf.getvalue()).decode())
    finally:
        doc.close()
    return previews


def _save_result(pdf_bytes: bytes, file_id: str, output_format: str, original_filename: str) -> Path:
    """Convert result to requested format and save to OUTPUT_DIR.

    Returns path to the saved file.
    """
    if output_format == "pdf":
        out_path = OUTPUT_DIR / f"{file_id}.pdf"
        out_path.write_bytes(pdf_bytes)
        return out_path

    # Image formats
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        if len(doc) == 1:
            pix = doc[0].get_pixmap(dpi=200)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            ext = output_format
            out_path = OUTPUT_DIR / f"{file_id}.{ext}"
            if output_format == "jpg":
                img.save(str(out_path), format="JPEG", quality=95)
            else:
                img.save(str(out_path), format="PNG")
            return out_path
        else:
            # Multi-page: save as PDF regardless (user gets all pages)
            out_path = OUTPUT_DIR / f"{file_id}.pdf"
            out_path.write_bytes(pdf_bytes)
            return out_path
    finally:
        doc.close()


def _process_pdf(
    upload_path: Path,
    repl_dict: dict[str, str],
    case_sensitive: bool,
) -> tuple[bytes, list[ReplacementResult]]:
    """Run replacement with PyMuPDF, fallback to raster if needed."""
    try:
        return replace_text(str(upload_path), repl_dict, case_sensitive)
    except Exception as e:
        logger.error("PyMuPDF method failed: %s", e)
        try:
            from pdf_editor_raster import replace_text_raster
            return replace_text_raster(str(upload_path), repl_dict)
        except ImportError:
            raise HTTPException(status_code=500, detail=f"PDF processing failed: {e}")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health() -> dict:
    """Health check endpoint."""
    return {"status": "ok", "service": "pdf-text-editor"}


@app.post("/api/edit-simple")
async def edit_simple(
    file: UploadFile = File(...),
    replacements: str = Form(...),
    case_sensitive: bool = Form(True),
    output_format: str = Form("pdf"),
) -> JSONResponse:
    """Edit PDF with explicit replacements (no LLM).

    Returns JSON with watermarked preview images and a result_file_id
    for downloading the clean file after payment.
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    content = await file.read()
    if len(content) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(status_code=413, detail=f"File too large. Max {MAX_FILE_SIZE_BYTES // (1024*1024)}MB")

    try:
        repl_dict: dict[str, str] = json.loads(replacements)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON in replacements field")
    if not isinstance(repl_dict, dict):
        raise HTTPException(status_code=400, detail="Replacements must be a JSON object")

    output_format = output_format.lower()
    if output_format not in ("pdf", "jpg", "png"):
        raise HTTPException(status_code=400, detail="output_format must be pdf, jpg, or png")

    file_id = uuid.uuid4().hex
    upload_path = UPLOAD_DIR / f"{file_id}.pdf"
    upload_path.write_bytes(content)

    try:
        pdf_bytes, results = _process_pdf(upload_path, repl_dict, case_sensitive)
        total_replacements = sum(r.count for r in results)

        # Generate watermarked preview
        previews = _pdf_bytes_to_preview(pdf_bytes)

        # Save clean result for later download
        result_file_id = uuid.uuid4().hex
        _save_result(pdf_bytes, result_file_id, output_format, file.filename or "doc.pdf")

        return JSONResponse({
            "preview_images": previews,
            "result_file_id": result_file_id,
            "output_format": output_format,
            "original_filename": file.filename,
            "replacements_report": _format_results(results),
            "total_replacements": total_replacements,
            "total_pages": len(previews),
            "price_usd": PAYMENT_PRICE_USD,
        })

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected error during PDF editing")
        raise HTTPException(status_code=500, detail=f"Processing error: {e}")
    finally:
        upload_path.unlink(missing_ok=True)


@app.post("/api/edit")
async def edit_with_llm(
    file: UploadFile = File(...),
    prompt: str = Form(...),
    output_format: str = Form("pdf"),
) -> JSONResponse:
    """Edit PDF using natural language instructions parsed by LLM.

    Returns JSON with watermarked preview and result_file_id.
    """
    from llm_parser import parse_prompt

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    content = await file.read()
    if len(content) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(status_code=413, detail=f"File too large. Max {MAX_FILE_SIZE_BYTES // (1024*1024)}MB")

    if not prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")

    output_format = output_format.lower()
    if output_format not in ("pdf", "jpg", "png"):
        output_format = "pdf"

    # Parse prompt through LLM
    try:
        parsed = await parse_prompt(prompt)
    except Exception as e:
        logger.error("LLM parsing failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Failed to parse instructions: {e}")

    repl_dict = parsed.get("replacements", {})
    case_sensitive = parsed.get("case_sensitive", False)
    notes = parsed.get("notes", "")

    if not repl_dict:
        raise HTTPException(
            status_code=400,
            detail="Could not extract any replacements from your instructions. Please be more specific.",
        )

    file_id = uuid.uuid4().hex
    upload_path = UPLOAD_DIR / f"{file_id}.pdf"
    upload_path.write_bytes(content)

    try:
        pdf_bytes, results = _process_pdf(upload_path, repl_dict, case_sensitive)
        total_replacements = sum(r.count for r in results)

        previews = _pdf_bytes_to_preview(pdf_bytes)

        result_file_id = uuid.uuid4().hex
        _save_result(pdf_bytes, result_file_id, output_format, file.filename or "doc.pdf")

        return JSONResponse({
            "preview_images": previews,
            "result_file_id": result_file_id,
            "output_format": output_format,
            "original_filename": file.filename,
            "replacements_report": _format_results(results),
            "total_replacements": total_replacements,
            "total_pages": len(previews),
            "price_usd": PAYMENT_PRICE_USD,
            "parsed_instructions": {
                "replacements": repl_dict,
                "case_sensitive": case_sensitive,
                "notes": notes,
            },
        })

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected error during PDF editing")
        raise HTTPException(status_code=500, detail=f"Processing error: {e}")
    finally:
        upload_path.unlink(missing_ok=True)


@app.post("/api/create-invoice/{result_file_id}")
async def create_invoice_endpoint(result_file_id: str) -> JSONResponse:
    """Create a payment invoice for a result file."""
    # Check that result file exists
    found = list(OUTPUT_DIR.glob(f"{result_file_id}.*"))
    if not found:
        raise HTTPException(status_code=404, detail="Result file not found or expired")

    invoice = create_invoice(result_file_id)
    return JSONResponse(invoice)


@app.get("/api/download/{result_file_id}")
async def download_result(result_file_id: str, token: str = "temp") -> FileResponse:
    """Download the clean result file after payment verification."""
    if not verify_payment(result_file_id, token):
        raise HTTPException(status_code=403, detail="Payment not verified")

    # Find the result file
    found = list(OUTPUT_DIR.glob(f"{result_file_id}.*"))
    if not found:
        raise HTTPException(status_code=404, detail="Result file not found or expired")

    result_path = found[0]
    ext = result_path.suffix.lstrip(".")
    media_types = {"pdf": "application/pdf", "jpg": "image/jpeg", "png": "image/png"}
    media = media_types.get(ext, "application/octet-stream")

    return FileResponse(
        path=str(result_path),
        filename=f"edited.{ext}",
        media_type=media,
    )


# Serve frontend index at root
@app.get("/")
async def serve_index() -> FileResponse:
    """Serve the frontend index.html."""
    index_path = FRONTEND_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Frontend not found")
    return FileResponse(str(index_path), media_type="text/html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=HOST, port=PORT, reload=True)
