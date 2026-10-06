# InvoiceLens

Extract structured, schema-validated, confidence-scored **invoice** data from
messy PDF, DOCX, plain-text, image, and scanned (text-layer-free) PDF
documents. FastAPI backend + React (shadcn/ui) upload UI, deployable to Vercel
as a single project.

## Features

- **Multi-format parsing** — PDF (`pdfplumber`, layout-preserving), DOCX
  (`python-docx`, paragraphs + tables), TXT, images
  (PNG/JPEG/WebP/TIFF/BMP).
- **Images & scans** — PDFs with no text layer are detected, rendered, and
  OCR'd locally (RapidOCR, optional install — free, offline, ~7s/page),
  escalating to the AI vision model when OCR is missing or unreliable.
- **Hybrid speed-first extraction** — default `auto` mode runs the instant
  heuristic extractor first (~50–200ms over HTTP) and escalates to the AI
  backend only when confidence is low or critical fields are missing.
- **AI extraction** — works with any Anthropic-compatible endpoint (native
  Anthropic API, or proxies like Atria) via standard env vars. Zero code
  change to switch providers.
- **Offline fallback** — deterministic regex/rule-based heuristic extractor
  runs with no API key, no network, no cost.
- **Schema validation with retry/repair** — Pydantic `Invoice` model; dirty
  values (e.g. `"$1,234.56"`) are auto-repaired and re-validated up to 3
  attempts.
- **Confidence scoring** — per-field (0–1) plus overall mean; rendered as
  color-coded bars in the UI.
- **Stateless one-shot endpoint** — `POST /extract` does upload → parse →
  extract → result in one request; safe on serverless.
- **Web UI** — single-page drag-and-drop upload, invoice card, confidence
  bars with stamp-in animation, line items, warnings, raw JSON view.
- **Vercel-ready** — Mangum ASGI adapter + `vercel.json`: static SPA and
  Python API in one deployment.
- **46 tests** — parsing (incl. images and scanned PDFs), validation/retry,
  all extractor backends incl. auto-mode gate/escalation/fallback and the
  image → vision escalation chain (AI path mocked), OCR confidence discount,
  full API cycle, one-shot endpoint.

## Quick start

```bash
# 1. Backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt    # bash: .venv/Scripts/pip

# 2. (optional) Local OCR — only needed to read images/scanned PDFs offline.
#    Heavy wheels, deliberately kept out of requirements.txt (see Deploy).
.venv\Scripts\pip install -r requirements-ocr.txt

# 3. (optional) AI backend — create .env (gitignored), see Environment variables
#    Without it the offline heuristic extractor is used.

# 4. UI (optional but recommended) — needs Node >= 22.12
cd frontend && npm install && npm run build && cd ..

# 5. Run
python scripts/run_local.py
# -> prints active backend, OCR/vision status + URL. Default port 8000;
#    PORT=8001 recommended (8000 is commonly occupied, and the Vite dev
#    proxy targets 8001).
```

Open the printed URL — UI at `/`, interactive API docs at `/docs`.
Sample invoices to test with: `sample_docs/sample_invoice.{pdf,docx,txt}` and
`sample_docs/scanned_invoice.{png,pdf}` — an image and a text-layer-free PDF
of the same invoice (regenerate: `python scripts/generate_sample_docs.py`,
`python scripts/generate_scanned_samples.py`).

## Project structure

```
app/                  FastAPI backend
  main.py             routes + static UI mount (frontend/dist if present)
  parsing.py          PDF/DOCX/TXT/images -> text (+ page images for vision)
  ocr.py              local OCR (RapidOCR / Tesseract), rows rebuilt from
                      per-line boxes
  extraction.py       backends (AnthropicExtractor incl. vision /
                      HeuristicExtractor), validate_with_retry, confidence
  models.py           Pydantic schemas (Invoice, LineItem, ExtractionResult)
  storage.py          thread-safe in-memory document store
api/index.py          Vercel/Lambda entrypoint (Mangum ASGI adapter)
frontend/             Vite + React + TS + Tailwind v4 + shadcn/ui (dist gitignored)
scripts/
  run_local.py        loads .env, prints backend + OCR/vision status, serves app
  generate_sample_docs.py
  generate_scanned_samples.py   image-only scan fixtures (png + pdf)
sample_docs/          test invoices (pdf/docx/txt + scanned_invoice.png/.pdf)
tests/                46 pytest tests
vercel.json           build + rewrites for single-deployment SPA+API
requirements.txt      runtime deps (+ dev/test deps, marked)
requirements-ocr.txt  optional local-OCR engines (kept out of the bundle)
```

## API

```
POST /extract                 -> parse + extract in ONE request (stateless, serverless-safe)
POST /documents               -> upload + parse, returns document_id (stateful)
POST /documents/{id}/extract  -> run extraction on stored document
GET  /documents/{id}/extraction
GET  /documents/{id}
GET  /health
```

All upload endpoints take multipart field `file` and accept `.pdf`, `.docx`,
`.txt`/`.text`, and images `.png`, `.jpg`, `.jpeg`, `.webp`, `.bmp`, `.tif`,
`.tiff` (scanned PDFs without a text layer are auto-detected and routed
through the image path). Image input that neither local OCR nor the vision
backend can read returns 422 with a detail naming the missing piece. On
serverless (Vercel) the in-memory store does not persist across invocations
— use `POST /extract` there (the deployed UI already does).

`GET /health` reports what the process can actually do right now, so setup
problems are visible without a probe upload:

```json
{"status": "ok", "extraction_mode": "auto", "image_mode": "auto",
 "ai_backend": true, "vision_for_images": true, "ocr_engine": "rapidocr"}
```

Extraction pipeline: parse → backend `extract()` → `validate_with_retry()`
(repairs exactly the fields Pydantic rejected, up to 3 attempts) →
`ExtractionResult` (invoice + per-field confidence + overall confidence +
backend + attempts + warnings + provenance: `text_source` — `pdf-text` /
`docx` / `txt` / `ocr` / `image`, or `vision` when the model read the pixels
— and `ocr_engine` — `rapidocr` / `tesseract`, else null).

Backends, selected by `get_extractor()` from `EXTRACTION_MODE`:

- `AutoExtractor` (default `auto`, needs AI env set) — speed-first hybrid.
  Runs the heuristic extractor (milliseconds); returns immediately when the
  result clears the quality gate (overall confidence ≥ 0.75 AND
  `invoice_number` + `total_amount` found), otherwise escalates to the AI
  backend. If the AI call fails, falls back to the heuristic result — an
  outage degrades, never errors. `backend` field in the response tells you
  which path ran (`heuristic-v1` = fast path, model name = escalated). For
  image/scanned input the escalation prefers the model's vision input over
  OCR'd text: the chain is heuristic (on OCR text) → vision → text AI →
  heuristic fallback.
- `AnthropicExtractor` (`EXTRACTION_MODE=ai`, or `auto` escalation) — active
  when `ANTHROPIC_API_KEY` **or** `ANTHROPIC_AUTH_TOKEN` is set. The
  `anthropic` SDK reads `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` /
  `ANTHROPIC_MODEL` from the environment itself, so any
  Anthropic-compatible proxy works with no code change. Prompts the model
  for strict JSON incl. self-reported per-field confidence (clamped to 0–1,
  defaulted 0.5 when omitted). `extract_image()` reads invoices straight off
  the pixels: up to `VISION_MAX_PAGES` pages as base64 image blocks (each
  downscaled to stay under 4 MB), same JSON contract; any OCR text rides
  along as a hint only. Lazy-initialized: costs nothing until an escalation
  actually happens.
- `HeuristicExtractor` (`EXTRACTION_MODE=fast`, or default when no AI env) —
  offline, instant, deterministic. Labeled patterns (e.g. `Invoice Number:`)
  score 0.9, loose fallback patterns 0.6, missing fields 0.0. Returns raw
  captures deliberately; the validation layer cleans them.

### Usage examples

Port below assumes 8000 (default); if you run with `PORT=8001`, swap it.
Use `127.0.0.1`, not `localhost`, in CLI clients: on Windows, fresh
`localhost` connections try IPv6 `::1` first and wait for the fallback,
adding ~2s per request. (Browsers race both stacks and are unaffected.)

```bash
# One-shot (recommended): parse + extract, single request
curl -s -X POST http://127.0.0.1:8000/extract \
  -F "file=@sample_docs/sample_invoice.pdf;type=application/pdf"
# -> {
#      "document_id": "inline",
#      "filename": "sample_invoice.pdf",
#      "invoice": {
#        "vendor_name": "Acme Robotics Inc.",
#        "invoice_number": "INV-10234",
#        "invoice_date": "03/14/2026",
#        "due_date": "04/13/2026",
#        "total_amount": 1005.5,
#        "currency": "USD",
#        "line_items": [ {"description": "Widget assembly (Model X)", "quantity": 10.0, "unit_price": 45.0, "amount": 450.0}, ... ]
#      },
#      "field_confidence": {"invoice_number": 0.99, "total_amount": 0.98, ...},
#      "overall_confidence": 0.857,
#      "backend": "heuristic-v1",   # fast path; AI model name (e.g.
#                                   # "Atria-Dawn-Preview") when escalated
#      "attempts": 2,
#      "warnings": ["attempt 1: validation failed (1 error(s)), repairing and retrying"]
#    }

# Image / scan upload — same endpoint, same one-shot shape. Measured offline
# run (RapidOCR installed, EXTRACTION_MODE=fast): all six header fields and
# three line items correct.
curl -s -X POST http://127.0.0.1:8000/extract \
  -F "file=@sample_docs/scanned_invoice.png;type=image/png"
# -> { ..., "backend": "heuristic-v1", "text_source": "ocr",
#      "ocr_engine": "rapidocr", "overall_confidence": 0.771,
#      "warnings": ["ocr engine rapidocr on 1 page(s), mean score 0.986",
#                   "text came from OCR (rapidocr); per-field confidence
#                    scaled by 0.9", ...] }

# Stateful flow (local / long-running servers only)
curl -s -X POST http://127.0.0.1:8000/documents \
  -F "file=@sample_docs/sample_invoice.pdf;type=application/pdf"
# -> {"document_id": "bd585b2953af", ...}
curl -s -X POST http://127.0.0.1:8000/documents/bd585b2953af/extract
curl -s http://127.0.0.1:8000/documents/bd585b2953af/extraction
```

Interactive OpenAPI docs: `http://127.0.0.1:8000/docs`.

## Images and scanned PDFs

Images (phone photos, screenshots) and PDFs with no real text layer (under
40 characters via `pdfplumber` — photocopier output) take a two-route path,
cheap first:

1. **Local OCR** (default) — scanned PDF pages are rasterized with
   pypdfium2 at ~200 dpi (up to 8 pages, `OCR_RENDER_SCALE` /
   `OCR_MAX_PAGES`) and OCR'd by RapidOCR (Tesseract as fallback): free,
   offline, no API key, ~7s/page locally (first request also pays a ~0.5–2s
   model load). Rows are rebuilt from per-line box geometry with tab-joined
   cells, so the heuristic line-item parser reads OCR'd tables like any
   other text. Needs `pip install -r requirements-ocr.txt`.
2. **Vision model** (escalation) — pages go to the AI backend as base64
   images (≤ 4 pages, downscaled under 4 MB each) when no OCR engine is
   installed or when the heuristic fails its quality gate on the OCR text
   (empty or garbled output will; a mean per-line score below 0.60 is also
   flagged as unreliable in the warnings). `IMAGE_MODE=vision` always sends
   pixels for image input; `IMAGE_MODE=ocr` never calls the model on
   images. The vision path treats OCR text as a hint, not input.

Provenance comes back on the result: `text_source` (`ocr` on the OCR route,
`vision` when the model read pixels) plus `ocr_engine`. OCR'd text gets its
per-field confidence discounted by a flat 0.9 — a misread `8`→`3` changes
numbers silently — so the same invoice content measured: via RapidOCR
`overall_confidence` 0.771 vs 0.857 through a real text layer; the vision
route (no discount) scored 0.984 with correct word spacing. When neither
route can run, the upload returns 422 explaining what to install or set.

Limits, as measured: no handwriting (print only); RapidOCR sometimes merges
inter-word spaces inside cells on scanned PDFs (`"Widgetassembly(Model X)"`)
while quantities and amounts stay correct; page caps (8 rendered/OCR'd, 4
sent to vision); ~7s/page OCR locally. Fixtures:
`sample_docs/scanned_invoice.{png,pdf}`.

## Environment variables

All optional. Without them: offline heuristic backend, still complete real
extractions. Create `.env` (gitignored, never committed — placeholders only
here):

```ini
# Option A — native Anthropic
ANTHROPIC_API_KEY=sk-ant-...

# Option B — any Anthropic-compatible proxy (e.g. Atria)
ANTHROPIC_BASE_URL=https://api.atria-asi.ai
ANTHROPIC_AUTH_TOKEN=atr_REPLACE_WITH_YOUR_TOKEN
ANTHROPIC_MODEL=Atria-Dawn-Preview

# Option C — ExperientialLabs (verified: Anthropic Messages API compatible,
# Bearer auth with an xpl_ token). BASE_URL carries no /v1 — the SDK appends
# /v1/messages itself. qwen3.8-27b accepts image blocks (vision path works);
# claude-opus-5 returns 429 model_requires_purchase until credits are bought.
ANTHROPIC_BASE_URL=https://api.experientiallabs.ai
ANTHROPIC_AUTH_TOKEN=xpl_REPLACE_WITH_YOUR_TOKEN
ANTHROPIC_MODEL=qwen3.8-27b
```

| Variable | Purpose | Default |
|---|---|---|
| `ANTHROPIC_API_KEY` | enables AI backend (native Anthropic) | unset → heuristic |
| `ANTHROPIC_AUTH_TOKEN` | enables AI backend (proxy auth, e.g. Atria) | unset |
| `ANTHROPIC_BASE_URL` | Anthropic-compatible endpoint URL | SDK default |
| `ANTHROPIC_MODEL` | model name for extraction | `claude-3-5-sonnet-latest` |
| `EXTRACTION_MODE` | `auto` (heuristic first, AI on low confidence) / `ai` (always LLM) / `fast` (always heuristic) | `auto` |
| `IMAGE_MODE` | image/scan handling: `auto` (OCR text first, vision to escalate) / `vision` (always pixels, needs AI creds) / `ocr` (never call the model on images) | `auto` |
| `OCR_ENGINE` | `auto` (RapidOCR, then Tesseract) / `rapid` / `tesseract` / `off` | `auto` |
| `OCR_RENDER_SCALE` | scanned-PDF page render scale (×72 dpi; 2.8 ≈ 200 dpi) | `2.8` |
| `OCR_MAX_PAGES` | max pages rendered + OCR'd per scanned PDF | `8` |
| `VISION_MAX_PAGES` | max page images sent per vision request | `4` |
| `PORT` | local server port (`run_local.py`) | `8000` |
| `INVOICELENS_UPLOAD_DIR` | where uploads are written | fresh temp dir; on Vercel must be under `/tmp` |
| `INVOICELENS_PAGE_DIR` | where rendered scan pages (`*_pageN.png`) are written | system temp dir (`/tmp` on Vercel) |

`run_local.py` loads `.env` automatically (python-dotenv). Plain
`uvicorn app.main:app` does **not** — export vars manually or you get the
heuristic backend.

## Web UI

`frontend/` — Vite + React + TypeScript, Tailwind v4 + shadcn/ui. Pure client
of the API above; the API stays usable standalone. One page: drop or pick an
invoice — PDF/DOCX/TXT, images and scans included (`.png .jpg .jpeg .webp
.bmp .tif .tiff`) → `POST /extract` → invoice card with per-field confidence bars
(stamp-in animation), line items, warnings, collapsible raw JSON. Theme
"Ledger & Stamp": light ledger-paper background, ink navy, stamp-green
confidence; Space Grotesk + IBM Plex Sans/Mono.

Build (Node >= 22.12; Vite 8 warns on 22.11 but builds fine):

```bash
cd frontend && npm install && npm run build    # -> frontend/dist (gitignored)
```

`app/main.py` mounts `frontend/dist` at `/` **only if it exists**, registered
after every API route — one port serves UI + API. Delete `dist` → API-only
server again.

Hot-reload dev mode: backend on 8001 in one terminal
(`PORT=8001 python scripts/run_local.py`), then:

```bash
cd frontend && npm run dev
```

Vite proxies `/extract`, `/documents`, `/health`, `/docs`, `/openapi.json` →
`http://localhost:8001` (edit targets in `frontend/vite.config.ts` if
needed). Dev server auto-picks a free port and prints it (5173 is often
taken).

Windows: if npm prunes the `rolldown` optional binding and build fails:
`npm install @rolldown/binding-win32-x64-msvc` inside `frontend/`.

## Tests

```bash
.venv\Scripts\python -m pytest -q      # bash: .venv/Scripts/python -m pytest -q
```

46 tests: stateless `POST /extract` one-shot, backend selection
(`EXTRACTION_MODE` auto/ai/fast, `ANTHROPIC_API_KEY` vs
`ANTHROPIC_AUTH_TOKEN`), auto-mode quality gate (clean invoice → no LLM
call), escalation on messy text, heuristic fallback on AI failure, parsing
all three formats, Pydantic validation (incl. out-of-range confidence
rejection), retry/repair loop (success + exhausted paths), heuristic
confidence scoring, `AnthropicExtractor` with mocked client (JSON parsing,
markdown-fence stripping, confidence clamping/defaulting), image and scanned-PDF
paths (PNG → OCR, text-layer-free PDF → render + OCR, text-layer PDF never
OCR'd, OCR confidence discount, `IMAGE_MODE=ocr` blocking vision, auto-mode
escalation to `extract_image` and its fallbacks, 422 when neither OCR nor
vision can read the file), full API cycle
(upload → extract → fetch, 404s, rejected uploads). Tests run offline and
deterministic — no AI env and no optional OCR install needed (engine
detection is lazy, and image/scan tests fake the OCR engine and the AI
credentials rather than installing either).

## Deploy to Vercel

Single deployment serves SPA + API. Already configured:

- `api/index.py` — Mangum ASGI adapter around `app.main:app`
  (`lifespan="off"`).
- `vercel.json` — `buildCommand` builds the frontend
  (`cd frontend && npm install && npm run build`);
  `outputDirectory: frontend/dist` ships the SPA as static files; rewrites
  send `/health`, `/extract`, `/documents`, `/docs`, `/redoc`,
  `/openapi.json` to the Python function, everything else falls back to
  `/index.html`; `memory: 1024`, `maxDuration: 60`.

The `frontend/dist` mount in `app/main.py` is inactive on Vercel (platform
serves statics itself) — no local frontend build needed before deploy.

```bash
npm i -g vercel      # or use npx vercel
vercel login
vercel --prod        # from repo root
```

Then Dashboard → Project Settings → Environment Variables: add
`ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_MODEL` (never
commit them), redeploy.

Caveats (honest):

1. In-memory store is per-invocation on serverless → use stateless
   `POST /extract` (deployed UI already does).
2. Stateful `/documents` flow works locally, can 404 across cold invocations
   on Vercel.
3. Only `/tmp` writable on Vercel; uploads use a temp dir by default, or set
   `INVOICELENS_UPLOAD_DIR=/tmp/uploads`. Rendered scan pages
   (`*_pageN.png`) go to the system temp dir (`INVOICELENS_PAGE_DIR` to
   override) — `/tmp` on Vercel, so no extra config needed.
4. `pdfplumber` + `Pillow` are heavy and Vercel installs all of
   `requirements.txt` (dev deps included). If bundle size bites, split dev
   deps into a separate file.
5. The local-OCR engines stay out of the serverless bundle on purpose
   (`requirements-ocr.txt`: opencv/onnxruntime/numpy, ~200 MB unpacked — a
   bad fit for a Vercel/Lambda layer). Image and scanned-PDF input on Vercel
   therefore goes through the vision backend, which needs the `ANTHROPIC_*`
   env vars set; without them such uploads return a 422 that says so.

## Design notes / Phase 2

Schema rationale (why invoice, why dates-as-strings), tradeoffs, known
limitations (no handwriting support, no date normalization, no
persistence/auth), and Phase-2 ideas (table-aware PDF extraction,
table-structure OCR, DB-backed store, multi-schema routing): see `NOTES.md`.
