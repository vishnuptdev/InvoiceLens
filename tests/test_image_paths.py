"""Tests for the image / scanned-PDF routes: OCR dispatch in `parsing.py` and
the vision-vs-OCR confidence policy in `extraction.py`.

Everything here is fully offline and deterministic: the OCR engine is always a
fake returning a canned `OcrResult` (no pixels are ever read by a real
engine), and the AI backend is always a fake injected via `AutoExtractor(ai=)`
or passed directly to `extract_invoice` (the Anthropic SDK is never called).
Images and image-only PDFs are generated with Pillow into pytest's tmp_path.
"""
import os

import pytest
from PIL import Image, ImageDraw

from app.extraction import (
    AutoExtractor,
    ExtractorBackend,
    HeuristicExtractor,
    OCR_CONFIDENCE_FACTOR,
    extract_invoice,
)
from app.ocr import OcrResult, OcrUnavailable
from app.parsing import ParsedDocument, parse_document

SAMPLE_DIR = os.path.join(os.path.dirname(__file__), "..", "sample_docs")

# A tidy fake OCR transcript: enough structure for the heuristic extractor to
# latch onto, so text-path tests can assert on concrete values.
OCR_TEXT = (
    "Acme Robotics Inc.\n"
    "Invoice #: INV-OCR-4242\n"
    "Invoice Date: 01/15/2026\n"
    "Due Date: 02/14/2026\n"
    "Total: $120.00\n"
)

# OCR text of a real invoice, but too incomplete to clear the auto-mode
# quality gate (missing dates and line items drag the average down), so the
# AutoExtractor is forced to escalate. The heuristic fallback can still read
# the number and total from it.
MESSY_OCR_TEXT = (
    "Acme Robotics Inc.\n"
    "Invoice #: INV-OCR-4242\n"
    "Total: $120.00\n"
)


def _clean_ai_env(monkeypatch):
    """Remove every ANTHROPIC_* variable that could make the code believe a
    real AI backend (and therefore the network) is available. Never print the
    values being removed."""
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL"):
        monkeypatch.delenv(var, raising=False)


def _set_ai_env(monkeypatch):
    """Pretend AI credentials exist. Only dummy values are ever set, and no
    test in this file constructs a real AnthropicExtractor."""
    _clean_ai_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.delenv("IMAGE_MODE", raising=False)


def _fake_ocr_result(text=OCR_TEXT, score=0.92):
    return OcrResult(text=text, engine="fakeocr", mean_score=score, line_count=len(text.splitlines()))


class _FakeOcrEngine:
    """Same surface as app.ocr's engines (`name`, `run(paths)`), but returns a
    canned result instead of touching an OCR library."""
    name = "fake-ocr"

    def __init__(self, result):
        self._result = result
        self.calls = []  # one entry per run(), each the list of paths given

    def run(self, paths):
        self.calls.append(list(paths))
        return self._result


def _patch_engine(monkeypatch, result=None):
    engine = _FakeOcrEngine(result or _fake_ocr_result())
    monkeypatch.setattr("app.parsing.get_engine", lambda: engine)
    return engine


def _patch_engine_unavailable(monkeypatch):
    def _raise():
        raise OcrUnavailable(
            "no OCR engine available - install 'rapidocr-onnxruntime' or set "
            "ANTHROPIC_* env vars so image pages can be read by a vision model instead"
        )
    monkeypatch.setattr("app.parsing.get_engine", _raise)


def _write_png(path):
    img = Image.new("RGB", (600, 800), "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle([20, 20, 580, 780], outline="black")
    img.save(str(path))
    return str(path)


def _write_scanned_pdf(path):
    """An image-only PDF (no text layer at all) built with Pillow, standing in
    for a photocopier scan."""
    img = Image.new("RGB", (600, 800), "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle([20, 20, 580, 780], outline="black")
    img.save(str(path), "PDF", resolution=100)
    return str(path)


class _VisionAI(ExtractorBackend):
    """Fake AI backend that can 'see' images. Records every call so tests can
    assert which route ran; the error flags simulate outages."""
    name = "vision-ai"

    def __init__(self, image_error=False, text_error=False):
        self.image_error = image_error
        self.text_error = text_error
        self.image_calls = []
        self.text_calls = 0

    def _payload(self):
        return {
            "vendor_name": "Acme Robotics Inc.", "invoice_number": "INV-OCR-4242",
            "invoice_date": "01/15/2026", "due_date": "02/14/2026",
            "total_amount": 120.0, "currency": "USD", "line_items": [],
        }

    def _confidence(self):
        return {"vendor_name": 0.95, "invoice_number": 0.95, "total_amount": 0.95}

    def extract(self, text):
        self.text_calls += 1
        if self.text_error:
            raise RuntimeError("ai text outage")
        return self._payload(), self._confidence()

    def extract_image(self, image_paths, text=""):
        self.image_calls.append((list(image_paths), text))
        if self.image_error:
            raise RuntimeError("ai vision outage")
        self.vision_used = True
        return self._payload(), self._confidence()


class _TextOnlyBackend(ExtractorBackend):
    """Fake heuristic-style backend for `extract_invoice`: returns a fixed
    candidate + confidence dict through the text route only."""
    name = "fake-heuristic"

    def __init__(self):
        self.text_calls = 0

    def extract(self, text):
        self.text_calls += 1
        return (
            {
                "vendor_name": "Acme Robotics Inc.", "invoice_number": "INV-OCR-4242",
                "invoice_date": None, "due_date": None, "total_amount": "$120.00",
                "currency": "USD", "line_items": [],
            },
            {"vendor_name": 0.9, "invoice_number": 0.9, "total_amount": 0.8, "currency": 1.0},
        )


# ---------------------------------------------------------------------------
# parsing.py dispatch
# ---------------------------------------------------------------------------

def test_png_is_parsed_through_ocr(tmp_path, monkeypatch):
    # An image upload should end up as source "ocr" with the engine's text and
    # the image itself kept for the vision fallback.
    png = _write_png(tmp_path / "invoice.png")
    engine = _patch_engine(monkeypatch)

    parsed = parse_document(png)

    assert parsed.source == "ocr"
    assert parsed.text == OCR_TEXT
    assert parsed.ocr_engine == "fakeocr"
    assert parsed.ocr_score == pytest.approx(0.92)
    assert parsed.images == [png]
    assert engine.calls == [[png]]


def test_image_without_ocr_engine_reports_image_source(tmp_path, monkeypatch):
    # No engine installed is not an error at parse time: the source becomes
    # "image", there is no text, and the notes explain the OCR situation so a
    # caller (or the API's 422 message) can say what would fix it.
    png = _write_png(tmp_path / "invoice.png")
    _patch_engine_unavailable(monkeypatch)

    parsed = parse_document(png)

    assert parsed.source == "image"
    assert parsed.text == ""
    assert parsed.images == [png]
    assert any("OCR" in note for note in parsed.notes)


def test_scanned_pdf_goes_through_ocr(tmp_path, monkeypatch):
    # A PDF with no text layer is rendered to page images and OCR'd, with a
    # note saying it had no text layer.
    monkeypatch.setenv("INVOICELENS_PAGE_DIR", str(tmp_path))
    pdf = _write_scanned_pdf(tmp_path / "scan.pdf")
    engine = _patch_engine(monkeypatch)

    parsed = parse_document(pdf)

    assert parsed.source == "ocr"
    assert parsed.text == OCR_TEXT
    assert len(parsed.images) == 1  # one page rendered by pypdfium2
    assert all(path.endswith(".png") and os.path.isfile(path) for path in parsed.images)
    assert any("no text layer" in note for note in parsed.notes)
    assert engine.calls == [parsed.images]


def test_text_layer_pdf_is_not_ocrd(tmp_path, monkeypatch):
    # A normal PDF keeps the "pdf-text" source and the OCR engine must never
    # be consulted at all.
    engine = _patch_engine(monkeypatch)

    parsed = parse_document(os.path.join(SAMPLE_DIR, "sample_invoice.pdf"))

    assert parsed.source == "pdf-text"
    assert "INV-10234" in parsed.text
    assert parsed.images == []
    assert engine.calls == []


# ---------------------------------------------------------------------------
# extraction.py: OCR confidence policy, vision route, IMAGE_MODE gating
# ---------------------------------------------------------------------------

def _ocr_parsed(png_path, text=OCR_TEXT):
    return ParsedDocument(
        text=text, source="ocr", ocr_engine="fakeocr", ocr_score=0.92,
        images=[png_path], notes=["ocr engine fakeocr on 1 page(s), mean score 0.92"],
    )


def test_ocr_text_discounts_field_confidence(tmp_path, monkeypatch):
    # OCR can misread digits silently, so every per-field confidence from an
    # OCR-sourced document is scaled by OCR_CONFIDENCE_FACTOR and the result
    # carries a warning explaining why.
    _clean_ai_env(monkeypatch)
    backend = _TextOnlyBackend()
    parsed = _ocr_parsed(str(tmp_path / "invoice.png"))

    result = extract_invoice(document_id="d1", filename="invoice.png", text=OCR_TEXT,
                             backend=backend, parsed=parsed)

    originals = {"vendor_name": 0.9, "invoice_number": 0.9, "total_amount": 0.8, "currency": 1.0}
    assert OCR_CONFIDENCE_FACTOR == 0.9
    assert result.field_confidence == {
        k: round(v * OCR_CONFIDENCE_FACTOR, 3) for k, v in originals.items()
    }
    assert result.text_source == "ocr"
    assert result.ocr_engine == "fakeocr"
    assert result.backend == "fake-heuristic"
    assert result.invoice.total_amount == 120.0  # dirty "$120.00" repaired by the retry loop
    assert any("OCR" in w and str(OCR_CONFIDENCE_FACTOR) in w for w in result.warnings)
    assert any("fakeocr" in w for w in result.warnings)  # parse notes are carried through


def test_vision_backend_skips_ocr_discount(monkeypatch):
    # When the model read the pixels itself, the OCR text never influenced the
    # answer, so the discount must not apply and text_source becomes "vision".
    _set_ai_env(monkeypatch)

    class _DirectVision(_TextOnlyBackend):
        name = "direct-vision"

        def extract(self, text):
            raise AssertionError("image input with vision must not use the text route")

        def extract_image(self, image_paths, text=""):
            self.image_paths = list(image_paths)
            self.vision_used = True
            return (
                {"vendor_name": "Acme Robotics Inc.", "invoice_number": "INV-OCR-4242",
                 "invoice_date": None, "due_date": None, "total_amount": 120.0,
                 "currency": "USD", "line_items": []},
                {"vendor_name": 0.95, "invoice_number": 0.95, "total_amount": 0.95},
            )

    backend = _DirectVision()
    parsed = _ocr_parsed("page.png")

    result = extract_invoice(document_id="d2", filename="invoice.png", text=OCR_TEXT,
                             backend=backend, parsed=parsed)

    assert backend.image_paths == ["page.png"]
    assert result.text_source == "vision"
    assert result.field_confidence == {"vendor_name": 0.95, "invoice_number": 0.95, "total_amount": 0.95}
    assert any("read the page images" in w for w in result.warnings)
    assert not any("scaled" in w for w in result.warnings)


def test_image_mode_ocr_never_calls_vision(monkeypatch):
    # IMAGE_MODE=ocr is an explicit "no model on images" policy: even with
    # credentials and page images available, only the text route may run.
    _set_ai_env(monkeypatch)
    monkeypatch.setenv("IMAGE_MODE", "ocr")

    ai = _VisionAI(image_error=True)  # extract_image records then explodes if reached
    backend = AutoExtractor(ai=ai)

    candidate, confidence = backend.extract(MESSY_OCR_TEXT, images=["page.png"])

    assert ai.image_calls == []
    assert ai.text_calls == 1
    assert backend.name == ai.name  # escalated to the AI on text
    assert backend.vision_used is False
    assert candidate["invoice_number"] == "INV-OCR-4242"


def test_auto_extractor_escalates_image_to_vision(monkeypatch):
    # Empty OCR text fails the heuristic gate, so the image goes to the AI
    # backend's vision input and the backend reports the AI's name.
    _set_ai_env(monkeypatch)
    ai = _VisionAI()
    backend = AutoExtractor(ai=ai)

    candidate, _ = backend.extract("", images=["page.png"])

    assert ai.image_calls == [(["page.png"], "")]
    assert ai.text_calls == 0
    assert backend.name == "vision-ai"
    assert backend.vision_used is True
    assert candidate["invoice_number"] == "INV-OCR-4242"


def test_auto_extractor_vision_failure_falls_back_to_text(monkeypatch):
    # A vision outage must degrade to the text route, never propagate.
    _set_ai_env(monkeypatch)
    ai = _VisionAI(image_error=True)
    backend = AutoExtractor(ai=ai)

    candidate, _ = backend.extract(MESSY_OCR_TEXT, images=["page.png"])  # must not raise

    assert len(ai.image_calls) == 1  # vision really was attempted first
    assert ai.text_calls == 1
    assert backend.name == "vision-ai"
    assert backend.vision_used is False


def test_auto_extractor_full_ai_outage_falls_back_to_heuristic(monkeypatch):
    # Both AI routes down: the heuristic result on the OCR text is returned.
    _set_ai_env(monkeypatch)
    ai = _VisionAI(image_error=True, text_error=True)
    backend = AutoExtractor(ai=ai)

    candidate, _ = backend.extract(MESSY_OCR_TEXT, images=["page.png"])  # must not raise

    assert backend.name == "heuristic-v1"
    assert backend.vision_used is False
    assert candidate["invoice_number"] == "INV-OCR-4242"


def test_extract_invoice_with_auto_extractor_vision_skips_discount(tmp_path, monkeypatch):
    # Integration: AutoExtractor behind extract_invoice. When it escalates to
    # vision, the result says "vision" and the OCR discount is not applied
    # even though the parsed source was "ocr".
    _set_ai_env(monkeypatch)
    ai = _VisionAI()
    backend = AutoExtractor(heuristic=HeuristicExtractor(), ai=ai)
    parsed = _ocr_parsed(str(tmp_path / "page.png"), text="")

    result = extract_invoice(document_id="d3", filename="invoice.png", text="",
                             backend=backend, parsed=parsed)

    assert result.text_source == "vision"
    assert result.backend == "vision-ai"
    assert result.field_confidence == {"vendor_name": 0.95, "invoice_number": 0.95, "total_amount": 0.95}
    assert any("read the page images" in w for w in result.warnings)
