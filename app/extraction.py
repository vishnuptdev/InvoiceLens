"""Extraction engine: turns raw document text into a schema-validated
`Invoice`, with a retry-on-invalid-schema loop and per-field confidence
scoring.

Backend selection mirrors the agent-observability project: an
`AnthropicExtractor` is used when `ANTHROPIC_API_KEY` is set, and a
deterministic `HeuristicExtractor` (regex/rule-based, no network) is the
default otherwise. See NOTES.md for the full tradeoff.
"""
import json
import os
import re
from typing import Dict, List, Tuple

from pydantic import ValidationError

from .models import Invoice

LABELED_CONFIDENCE = 0.9
FALLBACK_CONFIDENCE = 0.6
MISSING_CONFIDENCE = 0.0


class ExtractionError(Exception):
    def __init__(self, message: str, warnings: List[str]):
        super().__init__(message)
        self.warnings = warnings


# ---------------------------------------------------------------------------
# Schema validation with a retry-on-invalid-schema loop
# ---------------------------------------------------------------------------

def _clean_numeric(value):
    """Best-effort coercion of a dirty numeric string (e.g. "$1,234.56") to a
    float. Returns None (rather than raising) when it truly can't be parsed,
    letting Pydantic accept the field as absent."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[^\d.\-]", "", str(value))
    if cleaned in ("", "-", "."):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _repair_candidate(candidate: dict, exc: ValidationError) -> dict:
    """Apply targeted fixes for the specific fields Pydantic rejected, based
    on its error locations, rather than blindly retrying the same input."""
    repaired = dict(candidate)
    repaired["line_items"] = [dict(item) for item in repaired.get("line_items") or []]

    for err in exc.errors():
        loc = err["loc"]
        if not loc:
            continue
        field = loc[0]
        if field == "total_amount":
            repaired["total_amount"] = _clean_numeric(repaired.get("total_amount"))
        elif field == "currency" and not repaired.get("currency"):
            repaired["currency"] = "USD"
        elif field == "line_items" and len(loc) >= 3:
            idx, subfield = loc[1], loc[2]
            items = repaired["line_items"]
            if isinstance(idx, int) and 0 <= idx < len(items) and subfield in ("quantity", "unit_price", "amount"):
                items[idx][subfield] = _clean_numeric(items[idx].get(subfield))
    return repaired


def validate_with_retry(candidate: dict, max_attempts: int = 3) -> Tuple[Invoice, int, List[str]]:
    """Try to build a valid `Invoice` from `candidate`, repairing and retrying
    on ValidationError up to `max_attempts` times."""
    warnings: List[str] = []
    current = candidate
    last_exc = None
    for attempt in range(1, max_attempts + 1):
        try:
            invoice = Invoice.model_validate(current)
            return invoice, attempt, warnings
        except ValidationError as exc:
            last_exc = exc
            warnings.append(f"attempt {attempt}: validation failed ({len(exc.errors())} error(s)), repairing and retrying")
            current = _repair_candidate(current, exc)
    raise ExtractionError(f"schema validation failed after {max_attempts} attempts: {last_exc}", warnings)


def _overall_confidence(field_confidence: Dict[str, float]) -> float:
    if not field_confidence:
        return 0.0
    return round(sum(field_confidence.values()) / len(field_confidence), 3)


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------

class ExtractorBackend:
    name = "base"

    def extract(self, text: str) -> Tuple[dict, Dict[str, float]]:
        """Returns (raw_candidate_dict, field_confidence_dict)."""
        raise NotImplementedError


class HeuristicExtractor(ExtractorBackend):
    """Regex/rule-based extractor. Deliberately returns raw, sometimes-dirty
    values (e.g. "$1,234.56" for total_amount) exactly as captured from the
    text - the validate_with_retry loop is what cleans these up, which is a
    realistic division of labor between "extraction" and "validation"."""

    name = "heuristic-v1"

    _LABELED_PATTERNS = {
        "invoice_number": r"(?im)^\s*invoice\s*(?:#|no\.?|number)?\s*[:#]\s*([A-Za-z0-9\-]+)",
        "invoice_date": r"(?im)^\s*invoice\s*date\s*[:\-]\s*(.+)$",
        "due_date": r"(?im)^\s*due\s*date\s*[:\-]\s*(.+)$",
        "total_amount": r"(?im)^\s*(?:total|amount\s*due|balance\s*due)\s*[:\-]\s*\$?\s*([\d,]+\.?\d{0,2})",
    }
    _FALLBACK_PATTERNS = {
        "invoice_number": r"(?i)\b(?:invoice)\D{0,10}([A-Z]{0,4}-?\d{3,})",
        "invoice_date": r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
    }
    # Column separator can be 2+ spaces (txt/PDF layout extraction) or a tab
    # (docx tables, joined with "\t" by parsing.py).
    _LINE_ITEM_RE = re.compile(
        r"^(?P<description>.+?)(?:\t|\s{2,})(?P<quantity>\d+(?:\.\d+)?)(?:\t|\s{2,})\$?(?P<unit_price>[\d,]+\.\d{2})(?:\t|\s{2,})\$?(?P<amount>[\d,]+\.\d{2})\s*$"
    )

    def extract(self, text: str) -> Tuple[dict, Dict[str, float]]:
        candidate: dict = {}
        confidence: Dict[str, float] = {}

        for field, pattern in self._LABELED_PATTERNS.items():
            match = re.search(pattern, text)
            if match:
                candidate[field] = match.group(1).strip()
                confidence[field] = LABELED_CONFIDENCE
            else:
                fallback = self._FALLBACK_PATTERNS.get(field)
                fmatch = re.search(fallback, text) if fallback else None
                if fmatch:
                    candidate[field] = fmatch.group(1).strip()
                    confidence[field] = FALLBACK_CONFIDENCE
                else:
                    candidate[field] = None
                    confidence[field] = MISSING_CONFIDENCE

        candidate["vendor_name"], confidence["vendor_name"] = self._guess_vendor(text)
        candidate["currency"] = self._guess_currency(text)
        confidence["currency"] = LABELED_CONFIDENCE if candidate["currency"] != "USD" else FALLBACK_CONFIDENCE

        line_items = self._parse_line_items(text)
        candidate["line_items"] = line_items
        confidence["line_items"] = LABELED_CONFIDENCE if line_items else MISSING_CONFIDENCE

        return candidate, confidence

    @staticmethod
    def _guess_vendor(text: str) -> Tuple[str, float]:
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if re.search(r"(?i)\b(inc|llc|ltd|corp|co\.?|company|group)\b", line):
                return line, LABELED_CONFIDENCE
            return line, FALLBACK_CONFIDENCE
        return None, MISSING_CONFIDENCE

    @staticmethod
    def _guess_currency(text: str) -> str:
        if "€" in text:
            return "EUR"
        if "£" in text:
            return "GBP"
        return "USD"

    @classmethod
    def _parse_line_items(cls, text: str) -> List[dict]:
        items = []
        for line in text.splitlines():
            match = cls._LINE_ITEM_RE.match(line.rstrip())
            if match:
                items.append({
                    "description": match.group("description").strip(),
                    "quantity": match.group("quantity"),
                    "unit_price": match.group("unit_price"),
                    "amount": match.group("amount"),
                })
        return items


class AnthropicExtractor(ExtractorBackend):
    name = "claude-3-5-haiku-20241022"

    def __init__(self, model=None, api_key=None):
        import anthropic  # imported lazily so the package is optional offline

        self._client = anthropic.Anthropic(api_key=api_key or os.environ["ANTHROPIC_API_KEY"])
        if model:
            self.name = model

    def extract(self, text: str) -> Tuple[dict, Dict[str, float]]:
        prompt = (
            "Extract invoice fields from the document text below as strict JSON "
            "with this exact shape (no prose, no markdown fences):\n"
            '{"vendor_name": str|null, "invoice_number": str|null, '
            '"invoice_date": str|null, "due_date": str|null, '
            '"total_amount": number|null, "currency": str, '
            '"line_items": [{"description": str, "quantity": number|null, '
            '"unit_price": number|null, "amount": number|null}], '
            '"confidence": {"<field name>": number between 0 and 1, ...}}\n\n'
            f"Document text:\n{text}\n"
        )
        response = self._client.messages.create(
            model=self.name,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = "".join(block.text for block in response.content if hasattr(block, "text"))
        raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
        parsed = json.loads(raw)
        confidence = {k: max(0.0, min(1.0, float(v))) for k, v in (parsed.pop("confidence", {}) or {}).items()}
        for key in ("vendor_name", "invoice_number", "invoice_date", "due_date", "total_amount", "currency", "line_items"):
            confidence.setdefault(key, 0.5)
        return parsed, confidence


def get_extractor() -> ExtractorBackend:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        return AnthropicExtractor(api_key=api_key)
    return HeuristicExtractor()


def extract_invoice(document_id: str, filename: str, text: str, backend: ExtractorBackend = None):
    """Full pipeline: run the configured backend, then validate (with retry/
    repair) into a schema-checked Invoice. Returns an ExtractionResult."""
    from .models import ExtractionResult  # local import avoids a cycle at module load time

    backend = backend or get_extractor()
    candidate, field_confidence = backend.extract(text)
    invoice, attempts, warnings = validate_with_retry(candidate)
    overall_confidence = _overall_confidence(field_confidence)

    return ExtractionResult(
        document_id=document_id,
        filename=filename,
        invoice=invoice,
        field_confidence=field_confidence,
        overall_confidence=overall_confidence,
        backend=backend.name,
        attempts=attempts,
        warnings=warnings,
    )
