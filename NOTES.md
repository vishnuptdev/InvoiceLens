# NOTES.md — InvoiceLens

Autonomous build log and design-decision record (task requested no
clarifying questions; decisions below were made unilaterally and documented
here per the task's instructions).

## Environment check (performed before starting)

- **API keys**: `ANTHROPIC_API_KEY` not set. `OPENAI_API_KEY` not set.
- **Language toolchains**: Python 3.11.15, pip 24.0, Node v22.22.2, npm
  10.9.7, git 2.43.0 — all present.
- **Network access**: allowlisted, not open internet. `pypi.org` → HTTP 200
  (package installs confirmed working: `fastapi`, `pdfplumber`,
  `python-docx`, `reportlab`, `anthropic`, `pytest`, etc. all installed
  cleanly via pip). `api.anthropic.com` → HTTP 404 (reachable at the
  network level; 404 is just "no route at `/`", not an auth result).
  `api.openai.com` and a generic external host (`example.com`) both failed
  to connect — general internet is not reachable, only specific allowlisted
  hosts (package registries, the Anthropic API host).
- **Consequence**: no live LLM key is available here, so extraction defaults
  to a deterministic offline `HeuristicExtractor` (regex/rule-based, does
  real work — not a stub) and switches to `AnthropicExtractor` automatically
  once `ANTHROPIC_API_KEY` is set, with no code changes needed. **The
  heuristic backend is what ran for every test and every example in this
  NOTES.md and the README**, confirmed by `ExtractionResult.backend ==
  "heuristic-v1"` in every test assertion and API response shown below.
  *(Update from the image/scan pass: an Anthropic-compatible proxy later
  became reachable from this machine, so the vision-path numbers quoted in
  the images section below are measured runs, not projections.)*

## Document type: invoice

The brief said "structured data from messy documents/PDFs" without naming a
schema. Chose **invoice** extraction because it's the most common real-world
"extract structured data from a PDF" use case, has a schema with genuine
type diversity (strings, dates-as-strings, floats, a nested list) to exercise
Pydantic validation meaningfully, and is easy to generate realistic sample
documents for (see `scripts/generate_sample_docs.py`).

Dates are modeled as `Optional[str]` rather than `date`/`datetime`. Real
invoices use wildly inconsistent date formats ("03/14/2026", "March 14,
2026", "14 Mar 26"); parsing all of them into a single canonical `date` type
reliably is a real project on its own. Storing the raw matched string and
leaving normalization as a Phase-2 item was judged the right scope boundary
— it keeps the retry-on-invalid-schema loop honest (it's fixing genuine
validation failures like a dirty float, not papering over a decision to not
solve date parsing).

## Extraction backend: no ANTHROPIC_API_KEY available in this environment

Same situation and same solution as the agent-observability project: no API
key is configured here, so `get_extractor()` defaults to `HeuristicExtractor`
(regex/rule-based, no network, no cost) and only switches to
`AnthropicExtractor` when a key is present. Both implement the same
`ExtractorBackend.extract(text) -> (candidate_dict, field_confidence_dict)`
interface, so `extract_invoice()` and the retry-on-invalid-schema loop don't
care which one ran.

**Tradeoff**: the heuristic backend's field coverage is only as good as its
regexes — it will miss invoice layouts that don't match the labeled/fallback
patterns (e.g. a vendor name that isn't the first line, or line items not
laid out in clean columns). This was accepted because (a) it's fully
demonstrated working end-to-end on all three sample formats including a
genuine retry-and-repair cycle, and (b) the `AnthropicExtractor` code path
(prompting for the same JSON shape plus self-reported confidence) is
complete and would take over field coverage entirely once a key is
configured — nothing else in the pipeline would need to change. This path
is unit-tested too, not just written and left unexercised:
`tests/test_extraction.py` patches `anthropic.Anthropic` to confirm `get_extractor()`
selects it when a key is set, and that its JSON-parsing, markdown-fence
stripping, and confidence clamping/defaulting all behave correctly.

## Retry-on-invalid-schema loop: how it's genuinely exercised

The heuristic extractor intentionally returns raw regex captures for numeric
fields (e.g. `total_amount: "$1,234.56"` straight from the "Total:" line),
which Pydantic's `float` coercion correctly rejects (`$` and `,` aren't valid
float syntax). `validate_with_retry()` catches the `ValidationError`,
`_repair_candidate()` strips non-numeric characters from exactly the fields
Pydantic flagged, and the second attempt succeeds. This is demonstrated live
in `tests/test_extraction.py::test_validate_with_retry_repairs_dirty_total_amount`
and in the full end-to-end sample-document tests (`attempts == 2` in the
API response for the bundled sample invoice). A second test
(`test_validate_with_retry_raises_after_exhausting_attempts`) confirms it
raises cleanly, with accumulated warnings, when a candidate is truly
unrepairable (e.g. a required field is missing entirely) rather than looping
forever.

## Confidence scoring

Heuristic backend: 0.9 for a field matched by an explicit label (e.g.
`Invoice Number:`), 0.6 for a looser fallback pattern (e.g. a bare
`INV-1234`-shaped token found anywhere in the text), 0.0 for a field that
wasn't found at all. `overall_confidence` is the mean of the per-field
scores. Anthropic backend: the model is asked to self-report a 0–1
confidence per field in the same JSON response; values are clamped to
`[0,1]` and defaulted to 0.5 if the model omits one. Both paths feed the
same `ExtractionResult.field_confidence` / `overall_confidence` fields.

## Storage

`app/storage.py` is a simple thread-safe in-memory dict keyed by
`document_id`. This is a demo service (tests spin up a fresh `TestClient`
per run; there's no requirement in the brief for persistence across
restarts). A real deployment would swap this for a database-backed store;
because `main.py` only calls `store.add/get/set_extraction`, that swap
wouldn't touch any other module.

## PDF line-item parsing caveat

`pdfplumber`'s `extract_text(layout=True)` preserves column spacing well
enough that the bundled sample PDF's line items parse correctly (verified in
tests), but this is layout-dependent: a PDF with tighter or irregular column
spacing may not produce the "2+ spaces between columns" pattern the
heuristic line-item regex relies on. A more robust Phase-2 approach would
use `pdfplumber`'s native `page.extract_tables()` for documents with real
table structure, falling back to the text regex otherwise.

## Images and scanned PDFs: local OCR first, vision as escalation

The ask grew: read invoices from images (phone photos, screenshots) and from
PDFs with no text layer — photocopier output. Two routes exist because
neither alone is right. **Local OCR** is free, offline and key-less (~7s/page
measured locally, plus a one-time ~0.5–2s model load), but it misreads
glyphs and loses word spacing on harder scans. The **vision model** read the
same scanned fixture at 0.984 overall confidence with correct spacing, but
it's an API call: cost, latency, network — and it kills the
"works-with-no-key" demo story. So the pipeline tries cheap first: parse →
local OCR when an engine is installed → heuristic on the OCR text →
escalate to vision only when OCR is absent or the auto-mode quality gate
fails on the OCR text. A mean per-line score below `OCR_UNRELIABLE_SCORE`
(0.60) is flagged as unreliable in the parse notes/warnings, but the flag
itself has no separate escalation branch — its effect is indirect (garbled
OCR usually fails the gate) and informational.
`IMAGE_MODE` overrides the policy (`vision` always sends pixels for image
input, `ocr` never calls the model for images). Note `EXTRACTION_MODE=fast`
still uses OCR text — "fast" gates the extraction backend, not how the text
got there.

**Why RapidOCR over Tesseract**: pip-only. The ONNX models ship inside the
wheel — no system binary, no PATH to probe, and Windows dev boxes and Linux
containers behave identically. Tesseract via `pytesseract` is supported as
the second engine (`OCR_ENGINE=auto` tries RapidOCR first) but needs a
binary installer (UB-Mannheim on Windows, apt elsewhere) — the classic
"works on my machine" failure mode, and its plain-text API reports no
confidence, so `app/ocr.py` assumes a middling 0.7 for it. Engine detection
(`available_engine()`) is lazy imports + a version probe; nothing OCR-related
is imported at startup, which is why the engines live in a separate optional
`requirements-ocr.txt`: ~200MB unpacked of opencv/onnxruntime/numpy is
unusable in a Vercel bundle, where image input goes to the vision backend
instead.

**Row reconstruction from boxes**: RapidOCR returns per-text-run boxes and
scores, not lines. `group_boxes_into_rows` sorts boxes by vertical centre,
buckets centres within 28px into one line (tuned for the ~200dpi page
renders), and joins the cells of a row left-to-right with `\t` — the same
convention `parsing.py` uses for DOCX tables and the tab the heuristic
line-item regex accepts. That's what lets a scanned invoice's table parse
offline with zero AI. The limitation: rows whose cells differ a lot in
height or vertical extent split or merge wrongly, and RapidOCR itself
occasionally swallows inter-word spaces inside a cell — measured on the
scanned-PDF fixture, `"Widgetassembly(Model X)"` while quantities and
amounts stayed correct.

**Why the OCR confidence penalty is a flat 0.9**: a per-line OCR score
describes glyph recognition, not end-to-end correctness — a misread `8`→`3`
scores as high as a correct read, so the score can't be reused as extraction
confidence. `OCR_CONFIDENCE_FACTOR = 0.9` is applied to every field when the
text came through OCR, with a warning recorded; measured effect on the
fixture: `overall_confidence` 0.771 via OCR vs 0.857 for the same invoice
content through a real text layer. The vision path skips the penalty (it
consumes no OCR text), tracked via `ExtractorBackend.vision_used` →
`text_source: "vision"`. It's a constant, not an env var — a calibration
tied to the heuristic's 0.9/0.6/0.0 scale, not an operator knob.

**Why vision bypasses the OCR text**: `AnthropicExtractor.extract_image`
sends the pages as base64 image blocks under the same strict-JSON prompt and
appends any OCR text only as "for reference only (may be wrong)". Making
OCR text the source of truth would inherit its misreads into the model's
output; dropping it entirely loses a useful cross-check for the cases where
OCR was right. Pages are capped (`VISION_MAX_PAGES`, default 4) and
oversized renders downscaled below `MAX_VISION_IMAGE_BYTES` (4MB) because
the API bills by pixel area; formats outside the API's list (BMP, TIFF) go
through Pillow to PNG.

New env vars: `IMAGE_MODE`, `OCR_ENGINE`, `OCR_RENDER_SCALE`,
`OCR_MAX_PAGES`, `VISION_MAX_PAGES` — all with working defaults, tabled in
the README. `GET /health` reports the resolved capabilities
(`ocr_engine`, `vision_for_images`, …) and `scripts/run_local.py` prints
them at startup, so a setup problem is visible without a probe upload. When
an image upload can't be read by either route it 422s with a detail naming
the fix (install `requirements-ocr.txt` or set `ANTHROPIC_*`) — missing OCR
is a setup problem, not a bad file.

Remaining limits + Phase-2: **handwriting** — neither engine handles it and
the vision prompt asks for print; a handwriting-capable model is needed.
**Table structure** — row reconstruction keeps columns but ignores rules,
multi-line cells and merged headers; the robust step is table-structure OCR
(or `extract_tables()`-style detection on the rendered pages). **Page
caps** — `OCR_MAX_PAGES`/`VISION_MAX_PAGES` are truncation guards, not
sharding; long scans want per-page extraction with a merge. **PDF raster
caching** — `render_pdf_pages` re-rasterizes every request, writing
`*_pageN.png` next to the upload; the stateful `/documents` flow reuses the
parsed result only within one process, so every cold serverless invocation
re-renders. A cache keyed on (file hash, scale) is Phase-2.

## What's NOT included (by design, to keep this scoped)

- No date-format normalization (see above).
- No persistent storage / auth / multi-tenancy - this is a demo API, not a
  production service.
- No handwriting — both OCR engines and the vision prompt target printed
  text. Printed images/scans themselves are handled (see the images section
  above).
- No document-type detection/routing (e.g. distinguishing invoices from
  receipts from POs) — the service assumes every uploaded document is an
  invoice, per the single schema chosen above.

## Phase 2 ideas (not built, out of scope for this pass)

- Table-aware PDF line-item extraction via `page.extract_tables()`.
- Date normalization to a real `date` type with format inference.
- Handwriting support, table-structure-aware OCR, and PDF raster caching
  (each limit explained in the images section above).
- A second schema (e.g. receipts or purchase orders) sharing the same
  extraction/validation/confidence machinery, with a `document_type` field
  routing to the right Pydantic model.
- Persistent storage (SQLite/Postgres) behind the same `DocumentStore`
  interface.
