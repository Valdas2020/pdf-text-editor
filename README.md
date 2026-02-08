# PDF Text Editor — AI-Powered Find & Replace

Web service that accepts a PDF file and text instructions, finds specified words/phrases, and replaces them while preserving the original formatting (font, size, color, position).

## Quick Start

### Local Development

```bash
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Copy and configure environment
cp .env.example .env
# Edit .env with your ROUTELLM_API_KEY

# Run the server
python main.py
```

Open http://localhost:10000 in your browser.

### Docker

```bash
cp backend/.env.example backend/.env
# Edit backend/.env with your API key

docker compose up --build
```

Open http://localhost:10000.

## Deploy to Render

1. Push repo to GitHub
2. Go to [Render Dashboard](https://dashboard.render.com)
3. New → Web Service → Connect your repo
4. Settings:
   - **Name**: pdf-text-editor
   - **Runtime**: Docker
   - **Plan**: Free (or Starter for better performance)
   - **Health Check Path**: /api/health
5. Environment Variables:
   - `ROUTELLM_API_KEY` = your key
   - `MAX_FILE_SIZE_MB` = 50
6. Click "Create Web Service"
7. Wait for build & deploy (~5-10 min first time)
8. Access at: `https://pdf-text-editor.onrender.com`

Alternatively, use the included `render.yaml` blueprint for one-click setup.

## System Dependencies (for local dev)

For the raster fallback method, you need:

```bash
# Ubuntu/Debian
sudo apt install poppler-utils tesseract-ocr tesseract-ocr-rus

# macOS
brew install poppler tesseract tesseract-lang
```

## API Endpoints

### `POST /api/edit-simple` — Direct replacements (no AI)

```bash
curl -X POST http://localhost:8080/api/edit-simple \
  -F "file=@input.pdf" \
  -F 'replacements={"2025": "2026", "Draft": "Final"}' \
  -F "case_sensitive=false" \
  -F "output_format=pdf" \
  --output edited.pdf
```

### `POST /api/edit` — AI-powered (requires API key)

```bash
curl -X POST http://localhost:8080/api/edit \
  -F "file=@input.pdf" \
  -F "prompt=Replace all occurrences of 2025 with 2026" \
  -F "output_format=pdf" \
  --output edited.pdf
```

### `GET /api/health` — Health check

## Architecture

- **pdf_editor.py** — PyMuPDF-based text replacement (primary method)
- **pdf_editor_raster.py** — OCR + Pillow fallback for problematic fonts
- **llm_parser.py** — RouteLLM API integration for natural language instruction parsing
- **main.py** — FastAPI application with all endpoints
- **frontend/** — Single-page app with drag & drop, AI/manual modes, preview
