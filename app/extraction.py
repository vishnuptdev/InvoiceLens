"""Extraction engine: turns raw document text (or the pixels of an image /
scanned PDF) into a schema-validated `Invoice`, with a retry-on-invalid-schema
loop and per-field confidence scoring.

Backend selection mirrors the agent-observability project: an
`AnthropicExtractor` is used when `ANTHROPIC_API_KEY` is set, and a
deterministic `HeuristicExtractor` (regex/rule-based, no network) is the
default otherwise. See NOTES.md for the full tradeoff.

Images take one of two routes, in this order of preference:
1. local OCR (`parsing.py` -> `ocr.py`) produced text -> normal text pipeline,
   with `OCR_CONFIDENCE_FACTOR` applied because OCR misreads digits;
2. the image bytes go to the model's vision input (`AnthropicExtractor.
   extract_image`) — used when OCR is unavailable, when OCR produced nothing
   usable, or when `IMAGE_MODE=vision` forces it.
"""
import base64
import io
import json
import os
import re
from typing import Dict, List, Optional, Sequence, Tuple

from pydantic import ValidationError

from .models import Invoice
from .ocr import OCR_UNRELIABLE_SCORE

LABELED_CONFIDENCE = 0.9
FALLBACK_CONFIDENCE = 0.6
MISSING_CONFIDENCE = 0.0

# Text that reached the extractor through OCR is trusted less than a native
# text layer: a misread '8'->'3' or dropped decimal changes the number silently.
OCR_CONFIDENCE_FACTOR = 0.9

# Vision input limits. Anthropic accepts ~5MB per image and bills by pixel
# area, so oversized page renders are downscaled and the page count capped.
MAX_VISION_IMAGE_BYTES = 4_000_000
DEFAULT_VISION_MAX_PAGES = 4

# Extensions the vision route accepts; anything else is converted to PNG via
# Pillow before base64-encoding.
_NATIVE_VISION_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


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
    # Set True by backends that read pixels (vision) instead of the text the
    # caller passed in. `extract_invoice` uses it to skip the OCR penalty.
    vision_used = False

    def extract(self, text: str) -> Tuple[dict, Dict[str, float]]:
        """Returns (raw_candidate_dict, field_confidence_dict)."""
        raise NotImplementedError

    def extract_image(self, image_paths: Sequence[str], text: str = "") -> Tuple[dict, Dict[str, float]]:
        """Pixel-based extraction. Only the AI backend implements this; the
        heuristic extractor cannot see images, so `NotImplementedError` here
        means "use the text path instead"."""
        raise NotImplementedError(f"{self.name} cannot read images")


_JSON_SPEC = (
    "Extract invoice fields as strict JSON with this exact shape (no prose, "
    "no markdown fences):\n"
    '{"vendor_name": str|null, "invoice_number": str|null, '
    '"invoice_date": str|null, "due_date": str|null, '
    '"total_amount": number|null, "currency": str, '
    '"line_items": [{"description": str, "quantity": number|null, '
    '"unit_price": number|null, "amount": number|null}], '
    '"confidence": {"<field name>": number between 0 and 1, ...}}'
)

_INVOICE_FIELDS = (
    "vendor_name", "invoice_number", "invoice_date", "due_date",
    "total_amount", "currency", "line_items",
)


def _postprocess_model_json(raw: str) -> Tuple[dict, Dict[str, float]]:
    """Shared by the text and vision paths: strip markdown fences, parse JSON,
    clamp self-reported confidence to 0-1 and default anything the model left
    out to 0.5."""
    raw = re.sub(r"^```(?:json)?|```$", "", (raw or "").strip(), flags=re.MULTILINE).strip()
    parsed = json.loads(raw)
    confidence = {k: max(0.0, min(1.0, float(v))) for k, v in (parsed.pop("confidence", {}) or {}).items()}
    for key in _INVOICE_FIELDS:
        confidence.setdefault(key, 0.5)
    return parsed, confidence


def _encode_image_for_vision(path: str) -> Tuple[str, str]:
    """Read an image file and return (base64 data, media_type). Non-Anthropic
    formats (BMP/TIFF, and the PNGs the page renderer writes) go through
    Pillow to PNG; anything over the size budget is downscaled."""
    from PIL import Image

    ext = os.path.splitext(path)[1].lower()
    with Image.open(path) as img:
        if ext in _NATIVE_VISION_TYPES and os.path.getsize(path) <= MAX_VISION_IMAGE_BYTES:
            with open(path, "rb") as fh:
                return base64.b64encode(fh.read()).decode(), _NATIVE_VISION_TYPES[ext]

        rgb = img.convert("RGB") if img.mode not in ("RGB", "L") else img
        scale = 1.0
        while True:
            buf = io.BytesIO()
            frame = rgb if scale == 1.0 else rgb.resize(
                (max(1, int(rgb.width * scale)), max(1, int(rgb.height * scale)))
            )
            frame.save(buf, format="PNG")
            data = buf.getvalue()
            if len(data) <= MAX_VISION_IMAGE_BYTES or scale <= 0.25:
                return base64.b64encode(data).decode(), "image/png"
            scale *= 0.75


def vision_pages(image_paths: Sequence[str]) -> List[str]:
    """Cap the number of pages sent to the model (each page costs tokens)."""
    limit = int(os.environ.get("VISION_MAX_PAGES", DEFAULT_VISION_MAX_PAGES))
    return list(image_paths[:limit])


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
    # Placeholder name; overwritten in __init__ from the ANTHROPIC_MODEL env
    # var (or the `model` arg) so custom Anthropic-compatible providers such
    # as Atria report the model they actually call.
    name = "anthropic"

    def __init__(self, model=None, api_key=None):
        import anthropic  # imported lazily so the package is optional offline

        # No explicit credentials => the SDK reads ANTHROPIC_API_KEY /
        # ANTHROPIC_AUTH_TOKEN / ANTHROPIC_BASE_URL from the environment.
        # That lets any Anthropic-compatible endpoint (api.anthropic.com or a
        # proxy like Atria) work with zero code changes — just set the env.
        # `api_key` is kept for tests/explicit overrides.
        kwargs = {}
        if api_key:
            kwargs["api_key"] = api_key
        self._client = anthropic.Anthropic(**kwargs)
        self.name = model or os.environ.get("ANTHROPIC_MODEL") or "claude-3-5-sonnet-latest"

    def extract(self, text: str) -> Tuple[dict, Dict[str, float]]:
        self.vision_used = False
        prompt = f"{_JSON_SPEC}\n\nDocument text:\n{text}\n"
        return self._ask([{"role": "user", "content": prompt}])

    def extract_image(self, image_paths: Sequence[str], text: str = "") -> Tuple[dict, Dict[str, float]]:
        """Read the invoice straight off the pixels. Used for image uploads and
        scanned PDFs - no OCR installed locally, or OCR returned garbage. Any
        OCR text that does exist is passed along as a hint, not as the source of
        truth."""
        pages = vision_pages(image_paths)
        if not pages:
            raise ExtractionError("no images to read", [])

        blocks: List[dict] = [{
            "type": "text",
            "text": (
                _JSON_SPEC
                + "\n\nRead the invoice in the attached image(s) - one image per"
                " page, in order. Report exactly what is printed; double-check"
                " digits and decimals. If a field is not visible, return null."
                + (f"\n\nPartial OCR text, for reference only (may be wrong):\n{text}\n" if text.strip() else "")
            ),
        }]
        for path in pages:
            data, media_type = _encode_image_for_vision(path)
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}})

        self.vision_used = True
        try:
            return self._ask([{"role": "user", "content": blocks}])
        except Exception:
            self.vision_used = False
            raise

    def _ask(self, messages: List[dict]) -> Tuple[dict, Dict[str, float]]:
        response = self._client.messages.create(model=self.name, max_tokens=1024, messages=messages)
        raw = "".join(block.text for block in response.content if hasattr(block, "text"))
        return _postprocess_model_json(raw)


# Auto-mode quality gate: heuristic results at/above these marks are returned
# immediately (milliseconds) instead of paying for an LLM round-trip
# (seconds). A clean, labeled invoice typically scores ~0.85+ overall.
AUTO_MIN_OVERALL = 0.75
AUTO_REQUIRED_FIELDS = ("invoice_number", "total_amount")


def _heuristic_quality_ok(confidence: Dict[str, float]) -> bool:
    """True when the heuristic result is good enough to skip the AI call:
    overall confidence above the gate AND every critical field was actually
    found (confidence > 0)."""
    if not confidence:
        return False
    overall = sum(confidence.values()) / len(confidence)
    if overall < AUTO_MIN_OVERALL:
        return False
    return all(confidence.get(field, 0.0) > 0.0 for field in AUTO_REQUIRED_FIELDS)


def image_mode() -> str:
    """`IMAGE_MODE`: how image / scanned input is handled.
    auto (default) - OCR text first, vision model only to escalate
    vision       - always send pixels to the model when AI creds are set
    ocr          - never call the model on images, local OCR only
    """
    return os.environ.get("IMAGE_MODE", "auto").strip().lower() or "auto"


def vision_enabled() -> bool:
    """True when images may be sent to a model. Needs both permission from
    IMAGE_MODE and credentials, because the vision route is an API call.
    IMAGE_MODE values `ocr`, `off` and `text` all mean "never call a model on
    pixels" (`off`/`text` are accepted aliases for `ocr`)."""
    if image_mode() in ("ocr", "off", "text"):
        return False
    return ai_available()


def can_read_images() -> bool:
    """True when an image or scanned-PDF upload can actually be read right now.
    `EXTRACTION_MODE=fast` pins the backend to the heuristic extractor, which
    cannot see pixels, so vision is unavailable there even with credentials -
    and text-less input must then be rejected instead of returning an empty
    invoice."""
    if os.environ.get("EXTRACTION_MODE", "auto").strip().lower() == "fast":
        return False
    return vision_enabled()


def _supports_images(backend: ExtractorBackend) -> bool:
    """True when `backend` overrides `extract_image` (i.e. it can really read
    pixels) rather than inheriting the base class's NotImplementedError."""
    method = getattr(type(backend), "extract_image", None)
    return method is not None and method is not ExtractorBackend.extract_image


def _accepts_kw(backend: ExtractorBackend, name: str) -> bool:
    """True when the backend's `extract` takes a keyword argument called `name`,
    so single-method test fakes keep working."""
    import inspect

    try:
        return name in inspect.signature(backend.extract).parameters
    except (TypeError, ValueError):
        return False


def _accepts_images(backend: ExtractorBackend) -> bool:
    return _accepts_kw(backend, "images")


class AutoExtractor(ExtractorBackend):
    """Speed-first hybrid: run the instant heuristic extractor, return its
    result when it clears the quality gate, otherwise escalate to the AI
    backend (or, for image input, the AI backend's vision input). Falls back to
    the heuristic result if the AI call fails, so an outage degrades to
    slow-but-working, never to an error."""

    name = "auto"

    def __init__(self, heuristic: ExtractorBackend = None, ai: ExtractorBackend = None):
        self._heuristic = heuristic or HeuristicExtractor()
        # Lazy: constructing AnthropicExtractor costs ~1-2s the first time
        # (anthropic import + client init). Only pay it if we actually
        # escalate past the quality gate.
        self._ai_override = ai

    @property
    def _ai(self) -> ExtractorBackend:
        if self._ai_override is None:
            self._ai_override = AnthropicExtractor()
        return self._ai_override

    def extract(self, text: str, images: Optional[Sequence[str]] = None,
                ocr_score: Optional[float] = None) -> Tuple[dict, Dict[str, float]]:
        self.vision_used = False
        pages = list(images or [])
        # `IMAGE_MODE=vision` skips the heuristic entirely for image input.
        force_vision = bool(pages) and vision_enabled() and image_mode() == "vision"
        # OCR that scored below the reliability floor is not trustworthy even
        # when the regexes happen to match, so it never satisfies the gate.
        ocr_suspect = ocr_score is not None and ocr_score < OCR_UNRELIABLE_SCORE

        candidate: dict = {}
        confidence: Dict[str, float] = {}
        if not force_vision:
            candidate, confidence = self._heuristic.extract(text)
            # An empty OCR text is by definition not gate-worthy, so the
            # `_heuristic_quality_ok` check also covers the "OCR found nothing"
            # case - no separate branch needed.
            if _heuristic_quality_ok(confidence) and not ocr_suspect:
                self.name = self._heuristic.name  # fast path, no LLM call
                return candidate, confidence

        # Escalate: pixels first when we have them and the model can read them,
        # otherwise the OCR/text layer result goes to the model as text.
        if pages and vision_enabled() and _supports_images(self._ai):
            try:
                vision_candidate, vision_confidence = self._ai.extract_image(pages, text=text)
                self.name = self._ai.name
                self.vision_used = True
                return vision_candidate, vision_confidence
            except Exception:
                self.vision_used = False  # vision failed -> try the text route

        try:
            candidate, confidence = self._ai.extract(text)
            self.name = self._ai.name  # escalated to AI
            return candidate, confidence
        except Exception:
            if not candidate:
                candidate, confidence = self._heuristic.extract(text)
            self.name = self._heuristic.name  # AI failed -> heuristic fallback
            return candidate, confidence


def ai_available() -> bool:
    """True when an AI backend can be built from the environment. The
    `anthropic` SDK reads `ANTHROPIC_AUTH_TOKEN` (proxies such as Atria) or
    `ANTHROPIC_API_KEY` (native) itself, so either credential is enough."""
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def get_extractor() -> ExtractorBackend:
    # EXTRACTION_MODE:
    #   auto (default) - heuristic first, AI only when confidence is low
    #   ai             - always the AI backend (slowest, best coverage)
    #   fast           - always the heuristic backend (no network, instant)
    # AI availability: EITHER a native key or an auth-token. Atria and similar
    # Anthropic-compatible proxies use ANTHROPIC_AUTH_TOKEN + ANTHROPIC_BASE_URL
    # (no ANTHROPIC_API_KEY); the SDK picks base_url/token/model up from env.
    mode = os.environ.get("EXTRACTION_MODE", "auto").strip().lower()
    if mode == "fast" or not ai_available():
        return HeuristicExtractor()
    if mode == "ai":
        return AnthropicExtractor()
    return AutoExtractor()


def extract_invoice(document_id: str, filename: str, text: str, backend: ExtractorBackend = None, parsed=None):
    """Full pipeline: run the configured backend (text, or pixels when `parsed`
    carries page images and the backend can read them), then validate (with
    retry/repair) into a schema-checked Invoice. Returns an ExtractionResult.

    `parsed` is the optional `parsing.ParsedDocument` for the same file; it
    supplies provenance (`source`, `ocr_engine`, `notes`) and page images.
    Without it the call behaves exactly like the text-only pipeline.
    """
    from .models import ExtractionResult  # local import avoids a cycle at module load time

    backend = backend or get_extractor()
    source = getattr(parsed, "source", None) or "text"
    pages = list(getattr(parsed, "images", None) or [])
    ocr_score = getattr(parsed, "ocr_score", None)
    warnings_extra = list(getattr(parsed, "notes", None) or [])

    if pages and vision_enabled() and _supports_images(backend) and not isinstance(backend, AutoExtractor):
        # EXTRACTION_MODE=ai with image input: go straight to the model's eyes.
        candidate, field_confidence = backend.extract_image(pages, text=text)
    else:
        kwargs = {}
        if _accepts_images(backend):
            kwargs["images"] = pages
        if ocr_score is not None and _accepts_kw(backend, "ocr_score"):
            kwargs["ocr_score"] = ocr_score
        candidate, field_confidence = backend.extract(text, **kwargs)

    invoice, attempts, warnings = validate_with_retry(candidate)

    # OCR introduces a second way to be wrong (misread glyphs), so text that
    # travelled through OCR is trusted less than a native text layer - unless
    # the model read the pixels itself, which ignores the OCR text entirely.
    via_vision = bool(getattr(backend, "vision_used", False))
    if source == "ocr" and not via_vision:
        field_confidence = {k: round(v * OCR_CONFIDENCE_FACTOR, 3) for k, v in field_confidence.items()}
        warnings.append(f"text came from OCR ({getattr(parsed, 'ocr_engine', None) or 'unknown engine'}); "
                        f"per-field confidence scaled by {OCR_CONFIDENCE_FACTOR}")
        if ocr_score is not None and ocr_score < OCR_UNRELIABLE_SCORE:
            warnings.append(f"OCR mean score {ocr_score} is below the {OCR_UNRELIABLE_SCORE} "
                            "reliability floor; fields may be misread")
    if source in ("ocr", "image") and via_vision:
        warnings.append("the model read the page images (vision) instead of trusting the OCR text")
    warnings = warnings_extra + warnings

    return ExtractionResult(
        document_id=document_id,
        filename=filename,
        invoice=invoice,
        field_confidence=field_confidence,
        overall_confidence=_overall_confidence(field_confidence),
        backend=backend.name,
        attempts=attempts,
        warnings=warnings,
        text_source="vision" if via_vision else source,
        ocr_engine=getattr(parsed, "ocr_engine", None),
    )
