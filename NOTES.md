# NOTES.md — doc-extractor

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
configured — nothing else in the pipeline would need to change.

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

## What's NOT included (by design, to keep this scoped)

- No date-format normalization (see above).
- No persistent storage / auth / multi-tenancy - this is a demo API, not a
  production service.
- No OCR for scanned (image-only) PDFs — `pdfplumber` only extracts text
  that's already embedded as text in the PDF.
- No document-type detection/routing (e.g. distinguishing invoices from
  receipts from POs) — the service assumes every uploaded document is an
  invoice, per the single schema chosen above.

## Phase 2 ideas (not built, out of scope for this pass)

- Table-aware PDF line-item extraction via `page.extract_tables()`.
- Date normalization to a real `date` type with format inference.
- OCR fallback (e.g. `pytesseract`) for scanned/image PDFs.
- A second schema (e.g. receipts or purchase orders) sharing the same
  extraction/validation/confidence machinery, with a `document_type` field
  routing to the right Pydantic model.
- Persistent storage (SQLite/Postgres) behind the same `DocumentStore`
  interface.
