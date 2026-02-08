"""FastAPI application for PDF Text Editor."""

import asyncio
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
from fastapi.responses import FileResponse, JSONResponse, Response
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
from pdf_editor import ReplacementResult, replace_text

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


async def _cleanup_old_files() -> None:
    """Periodically delete files older than CLEANUP_INTERVAL_MINUTES."""
    while True:
        await asyncio.sleep(CLEANUP_INTERVAL_MINUTES * 60)
        now = time.time()
        cutoff = now - CLEANUP_INTERVAL_MINUTES * 60
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
    version="1.0.0",
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


def _format_results(results: list[ReplacementResult]) -> list[dict]:
    """Convert replacement results to JSON-serializable dicts."""
    return [
        {
            "original": r.original,
            "replacement": r.replacement,
            "count": r.count,
        }
        for r in results
    ]


def _pdf_to_images(pdf_bytes: bytes, fmt: str) -> list[bytes]:
    """Convert PDF bytes to a list of image bytes (PNG or JPEG)."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    images: list[bytes] = []
    try:
        for page in doc:
            pix = page.get_pixmap(dpi=200)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            buf = BytesIO()
            if fmt == "jpg":
                img.save(buf, format="JPEG", quality=95)
            else:
                img.save(buf, format="PNG")
            images.append(buf.getvalue())
    finally:
        doc.close()
    return images


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
) -> Response:
    """Edit PDF with explicit replacements (no LLM).

    Args:
        file: PDF file to edit.
        replacements: JSON string of {"old": "new", ...} pairs.
        case_sensitive: Whether search is case-sensitive.
        output_format: Output format — pdf, jpg, or png.
    """
    # Validate file
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    content = await file.read()
    if len(content) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size is {MAX_FILE_SIZE_BYTES // (1024*1024)}MB",
        )

    # Parse replacements JSON
    try:
        repl_dict: dict[str, str] = json.loads(replacements)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=400,
            detail="Invalid JSON in replacements field",
        )

    if not isinstance(repl_dict, dict):
        raise HTTPException(status_code=400, detail="Replacements must be a JSON object")

    # Validate output format
    output_format = output_format.lower()
    if output_format not in ("pdf", "jpg", "png"):
        raise HTTPException(status_code=400, detail="output_format must be pdf, jpg, or png")

    # Save uploaded file
    file_id = uuid.uuid4().hex
    upload_path = UPLOAD_DIR / f"{file_id}.pdf"
    upload_path.write_bytes(content)

    try:
        # Try PyMuPDF method
        try:
            pdf_bytes, results = replace_text(
                str(upload_path), repl_dict, case_sensitive
            )
        except Exception as e:
            logger.error("PyMuPDF method failed: %s", e)
            # Try raster fallback
            try:
                from pdf_editor_raster import replace_text_raster

                pdf_bytes, results = replace_text_raster(str(upload_path), repl_dict)
            except ImportError:
                raise HTTPException(
                    status_code=500,
                    detail=f"PDF processing failed: {e}",
                )

        total_replacements = sum(r.count for r in results)

        # Convert to requested format
        if output_format == "pdf":
            output_path = OUTPUT_DIR / f"{file_id}_edited.pdf"
            output_path.write_bytes(pdf_bytes)
            return FileResponse(
                path=str(output_path),
                filename=f"edited_{file.filename}",
                media_type="application/pdf",
                headers={
                    "X-Replacements": json.dumps(_format_results(results)),
                    "X-Total-Replacements": str(total_replacements),
                },
            )
        else:
            images = _pdf_to_images(pdf_bytes, output_format)
            if len(images) == 1:
                media = "image/jpeg" if output_format == "jpg" else "image/png"
                ext = output_format
                return Response(
                    content=images[0],
                    media_type=media,
                    headers={
                        "Content-Disposition": f'attachment; filename="edited_page1.{ext}"',
                        "X-Replacements": json.dumps(_format_results(results)),
                        "X-Total-Replacements": str(total_replacements),
                    },
                )
            else:
                # Multiple pages: return first page as preview + metadata
                # (For multi-page images, frontend can request pages individually)
                media = "image/jpeg" if output_format == "jpg" else "image/png"
                ext = output_format
                # Save all pages
                page_paths = []
                for idx, img_bytes in enumerate(images):
                    p = OUTPUT_DIR / f"{file_id}_page{idx+1}.{ext}"
                    p.write_bytes(img_bytes)
                    page_paths.append(str(p))

                return Response(
                    content=images[0],
                    media_type=media,
                    headers={
                        "Content-Disposition": f'attachment; filename="edited_page1.{ext}"',
                        "X-Replacements": json.dumps(_format_results(results)),
                        "X-Total-Replacements": str(total_replacements),
                        "X-Total-Pages": str(len(images)),
                    },
                )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected error during PDF editing")
        raise HTTPException(status_code=500, detail=f"Processing error: {e}")
    finally:
        # Clean up upload
        try:
            upload_path.unlink(missing_ok=True)
        except OSError:
            pass


@app.post("/api/edit")
async def edit_with_llm(
    file: UploadFile = File(...),
    prompt: str = Form(...),
    output_format: str = Form("pdf"),
) -> Response:
    """Edit PDF using natural language instructions parsed by LLM.

    Args:
        file: PDF file to edit.
        prompt: Natural language editing instructions.
        output_format: Output format — pdf, jpg, or png.
    """
    from llm_parser import parse_prompt

    # Validate file
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    content = await file.read()
    if len(content) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size is {MAX_FILE_SIZE_BYTES // (1024*1024)}MB",
        )

    if not prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")

    # Parse prompt through LLM
    try:
        parsed = await parse_prompt(prompt)
    except Exception as e:
        logger.error("LLM parsing failed: %s", e)
        raise HTTPException(
            status_code=502,
            detail=f"Failed to parse instructions: {e}",
        )

    repl_dict = parsed.get("replacements", {})
    case_sensitive = parsed.get("case_sensitive", False)
    notes = parsed.get("notes", "")

    if not repl_dict:
        raise HTTPException(
            status_code=400,
            detail="Could not extract any replacements from your instructions. Please be more specific.",
        )

    # Save uploaded file
    file_id = uuid.uuid4().hex
    upload_path = UPLOAD_DIR / f"{file_id}.pdf"
    upload_path.write_bytes(content)

    try:
        # Try PyMuPDF method
        try:
            pdf_bytes, results = replace_text(
                str(upload_path), repl_dict, case_sensitive
            )
        except Exception as e:
            logger.error("PyMuPDF method failed: %s", e)
            try:
                from pdf_editor_raster import replace_text_raster

                pdf_bytes, results = replace_text_raster(str(upload_path), repl_dict)
            except ImportError:
                raise HTTPException(
                    status_code=500,
                    detail=f"PDF processing failed: {e}",
                )

        total_replacements = sum(r.count for r in results)

        # Build metadata
        metadata = {
            "replacements": _format_results(results),
            "total_replacements": total_replacements,
            "parsed_instructions": {
                "replacements": repl_dict,
                "case_sensitive": case_sensitive,
                "notes": notes,
            },
        }

        # Convert to requested format
        output_format = output_format.lower()
        if output_format not in ("pdf", "jpg", "png"):
            output_format = "pdf"

        if output_format == "pdf":
            output_path = OUTPUT_DIR / f"{file_id}_edited.pdf"
            output_path.write_bytes(pdf_bytes)
            return FileResponse(
                path=str(output_path),
                filename=f"edited_{file.filename}",
                media_type="application/pdf",
                headers={
                    "X-Metadata": json.dumps(metadata),
                    "X-Total-Replacements": str(total_replacements),
                },
            )
        else:
            images = _pdf_to_images(pdf_bytes, output_format)
            media = "image/jpeg" if output_format == "jpg" else "image/png"
            ext = output_format

            if images:
                return Response(
                    content=images[0],
                    media_type=media,
                    headers={
                        "Content-Disposition": f'attachment; filename="edited_page1.{ext}"',
                        "X-Metadata": json.dumps(metadata),
                        "X-Total-Replacements": str(total_replacements),
                        "X-Total-Pages": str(len(images)),
                    },
                )
            else:
                raise HTTPException(status_code=500, detail="Failed to convert PDF to images")

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected error during PDF editing")
        raise HTTPException(status_code=500, detail=f"Processing error: {e}")
    finally:
        try:
            upload_path.unlink(missing_ok=True)
        except OSError:
            pass


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
