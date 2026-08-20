# doc-extractor

A FastAPI service that extracts structured, schema-validated, confidence-
scored **invoice** data from messy PDF, DOCX, and plain-text documents.

## Why invoice extraction

"Messy documents/PDFs" needed a concrete target schema. Invoices were chosen
because they have a stable, demonstrable shape (vendor, invoice number,
dates, total, line items) that still varies enough in formatting to make
schema validation, retry, and confidence scoring genuinely necessary. See
`NOTES.md` for the full tradeoff.

## Architecture

```
POST /documents               -> parse (pdfplumber/python-docx/txt) -> store text
POST /documents/{id}/extract  -> extractor backend -> validate_with_retry -> ExtractionResult
GET  /documents/{id}/extraction
GET  /documents/{id}
GET  /health
```

* **Parsing** (`app/parsing.py`) - `extract_text()` handles `.pdf` (via
  `pdfplumber`, with `layout=True` so column spacing survives extraction),
  `.docx` (via `python-docx`, paragraphs + tables joined with tabs), and
  `.txt`.
* **Schema** (`app/models.py`) - `Invoice` / `LineItem` Pydantic models with
  strictly typed fields (`total_amount: Optional[float]`, etc.) wrapped in an
  `ExtractionResult` that also carries per-field confidence and metadata.
* **Extraction backends** (`app/extraction.py`) - an `ExtractorBackend`
  interface with two implementations, selected automatically by
  `get_extractor()`:
  * `AnthropicExtractor` - prompts Claude for strict JSON (including
    self-reported per-field confidence) when `ANTHROPIC_API_KEY` is set.
  * `HeuristicExtractor` - the default in this environment (no key
    configured). Regex/rule-based: labeled patterns (e.g. `Invoice Number:`)
    score 0.9 confidence, looser fallback patterns score 0.6, missing fields
    score 0.0. Deliberately returns raw, sometimes-dirty values (e.g.
    `"$1,234.56"`) exactly as captured — cleaning that up is the validation
    layer's job, not the extractor's.
* **Retry-on-invalid-schema loop** (`validate_with_retry` in
  `app/extraction.py`) - tries `Invoice.model_validate(candidate)`; on a
  `ValidationError`, `_repair_candidate()` targets exactly the fields
  Pydantic rejected (e.g. strips `$`/commas from a numeric string) and
  retries, up to `max_attempts` (default 3). Raises `ExtractionError` with
  the accumulated warnings if it still can't validate.
* **Confidence scoring** - per-field (`field_confidence: Dict[str, float]`,
  each in `[0,1]`) plus an `overall_confidence` (mean of field scores).
* **Storage** (`app/storage.py`) - a simple thread-safe in-memory store
  keyed by `document_id`. See NOTES.md for the production-DB tradeoff.

## Setup

```bash
cd doc-extractor
python3 -m pip install -r requirements.txt
python3 scripts/generate_sample_docs.py   # (re)generates sample_docs/*
```

Optional: set `ANTHROPIC_API_KEY` to use the real Claude backend for
extraction. Without it, the service automatically uses the offline
heuristic backend and still produces complete, real extractions.

Run the service:

```bash
python3 -m uvicorn app.main:app --reload --port 8000
```

## API usage examples

```bash
# 1. Upload a document
curl -s -X POST http://localhost:8000/documents \
  -F "file=@sample_docs/sample_invoice.pdf;type=application/pdf"
# -> {"document_id": "bd585b2953af", "filename": "sample_invoice.pdf", "char_count": 1643, "has_extraction": false}

# 2. Run extraction
curl -s -X POST http://localhost:8000/documents/bd585b2953af/extract
# -> {
#      "document_id": "bd585b2953af",
#      "invoice": {
#        "vendor_name": "Acme  Robotics Inc.",
#        "invoice_number": "INV-10234",
#        "invoice_date": "03/14/2026",
#        "due_date": "04/13/2026",
#        "total_amount": 1005.5,
#        "currency": "USD",
#        "line_items": [ {"description": "Widget assembly (Model X)", "quantity": 10.0, "unit_price": 45.0, "amount": 450.0}, ... ]
#      },
#      "field_confidence": {"invoice_number": 0.9, "total_amount": 0.9, "currency": 0.6, ...},
#      "overall_confidence": 0.857,
#      "backend": "heuristic-v1",
#      "attempts": 2,
#      "warnings": ["attempt 1: validation failed (1 error(s)), repairing and retrying"]
#    }

# 3. Re-fetch a previously computed extraction
curl -s http://localhost:8000/documents/bd585b2953af/extraction
```

Interactive OpenAPI docs are served at `http://localhost:8000/docs` while the
service is running.

## Tests

```bash
python3 -m pytest -q
```

21 tests cover: parsing all three formats (PDF/DOCX/TXT), Pydantic model
validation (including out-of-range confidence rejection), the retry-on-
invalid-schema loop (both the repair-succeeds and repair-exhausted paths),
the heuristic extractor's field confidence scoring, and the full FastAPI
request/response cycle (upload -> extract -> fetch, 404s, and rejected
uploads).

## Known limitations / Phase 2 ideas

See `NOTES.md`.
