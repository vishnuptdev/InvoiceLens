import io
import os

from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app.ocr import OcrResult, OcrUnavailable

client = TestClient(app)

SAMPLE_DIR = os.path.join(os.path.dirname(__file__), "..", "sample_docs")

# A tidy fake OCR transcript so the heuristic extractor (used when no AI
# credentials are present) produces assertable values.
FAKE_OCR_TEXT = (
    "Acme Robotics Inc.\n"
    "Invoice #: INV-OCR-4242\n"
    "Invoice Date: 01/15/2026\n"
    "Total: $120.00\n"
)


def _clear_ai_env(monkeypatch):
    # Make the app believe no AI credentials exist, whatever the runner's
    # environment happens to carry. Values are never printed.
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
        monkeypatch.delenv(var, raising=False)


def _fake_ocr_engine(monkeypatch, text=FAKE_OCR_TEXT):
    class _Engine:
        name = "fake-ocr"

        def __init__(self):
            self.calls = []

        def run(self, paths):
            self.calls.append(list(paths))
            return OcrResult(text=text, engine="fakeocr", mean_score=0.92,
                             line_count=len(text.splitlines()))

    engine = _Engine()
    monkeypatch.setattr("app.parsing.get_engine", lambda: engine)
    return engine


def _no_ocr_engine(monkeypatch):
    def _raise():
        raise OcrUnavailable("no OCR engine available - install an engine or set ANTHROPIC_* vars")
    monkeypatch.setattr("app.parsing.get_engine", _raise)


def _png_bytes():
    img = Image.new("RGB", (600, 800), "white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _scanned_pdf_bytes():
    # Image-only PDF (no text layer), the same thing a flatbed scanner makes.
    img = Image.new("RGB", (600, 800), "white")
    buf = io.BytesIO()
    img.save(buf, format="PDF", resolution=100)
    return buf.getvalue()


def test_health():
    # /health reports what this process can actually do; the exact answers
    # depend on what is installed/configured in the runner, so assert shape
    # and sane types rather than fixed values.
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    for key in ("extraction_mode", "image_mode", "ocr_engine", "ai_backend", "vision_for_images"):
        assert key in body, f"health response is missing {key!r}"
    assert isinstance(body["extraction_mode"], str)
    assert isinstance(body["image_mode"], str)
    assert body["ocr_engine"] is None or isinstance(body["ocr_engine"], str)
    assert isinstance(body["ai_backend"], bool)
    assert isinstance(body["vision_for_images"], bool)


def test_upload_extract_and_fetch_flow():
    with open(os.path.join(SAMPLE_DIR, "sample_invoice.pdf"), "rb") as f:
        upload_resp = client.post(
            "/documents",
            files={"file": ("sample_invoice.pdf", f, "application/pdf")},
        )
    assert upload_resp.status_code == 201
    body = upload_resp.json()
    document_id = body["document_id"]
    assert body["has_extraction"] is False
    assert body["char_count"] > 0

    extract_resp = client.post(f"/documents/{document_id}/extract")
    assert extract_resp.status_code == 200
    result = extract_resp.json()
    assert result["invoice"]["invoice_number"] == "INV-10234"
    assert result["invoice"]["total_amount"] == 1005.50
    assert result["backend"] == "heuristic-v1"
    assert 0.0 <= result["overall_confidence"] <= 1.0

    fetch_resp = client.get(f"/documents/{document_id}/extraction")
    assert fetch_resp.status_code == 200
    assert fetch_resp.json() == result

    meta_resp = client.get(f"/documents/{document_id}")
    assert meta_resp.status_code == 200
    assert meta_resp.json()["has_extraction"] is True


def test_stateless_extract_one_shot_flow():
    # The serverless-safe endpoint: upload + extract in a single request, no
    # reliance on the in-memory store surviving between calls.
    with open(os.path.join(SAMPLE_DIR, "sample_invoice.pdf"), "rb") as f:
        resp = client.post(
            "/extract",
            files={"file": ("sample_invoice.pdf", f, "application/pdf")},
        )
    assert resp.status_code == 200
    result = resp.json()
    assert result["invoice"]["invoice_number"] == "INV-10234"
    assert result["invoice"]["total_amount"] == 1005.50
    assert result["backend"] == "heuristic-v1"
    assert 0.0 <= result["overall_confidence"] <= 1.0


def test_extract_one_shot_rejects_bad_file():
    resp = client.post("/extract", files={"file": ("x.xyz", b"data", "application/octet-stream")})
    assert resp.status_code == 422


def test_extraction_not_found_before_running():
    with open(os.path.join(SAMPLE_DIR, "sample_invoice.txt"), "rb") as f:
        upload_resp = client.post("/documents", files={"file": ("sample_invoice.txt", f, "text/plain")})
    document_id = upload_resp.json()["document_id"]

    resp = client.get(f"/documents/{document_id}/extraction")
    assert resp.status_code == 404


def test_unknown_document_id_returns_404():
    assert client.post("/documents/does-not-exist/extract").status_code == 404
    assert client.get("/documents/does-not-exist").status_code == 404
    assert client.get("/documents/does-not-exist/extraction").status_code == 404


def test_upload_rejects_unsupported_extension():
    resp = client.post(
        "/documents",
        files={"file": ("invoice.xyz", b"some bytes", "application/octet-stream")},
    )
    assert resp.status_code == 422


def test_upload_rejects_empty_file():
    resp = client.post(
        "/documents",
        files={"file": ("empty.txt", b"", "text/plain")},
    )
    assert resp.status_code == 422


def test_extract_rejects_image_without_ocr_or_vision(monkeypatch):
    # An image upload is only a hard failure when neither route can read it:
    # no local OCR engine and no AI credentials for the vision path. The 422
    # detail must name both fixes so the setup problem is obvious.
    _clear_ai_env(monkeypatch)
    _no_ocr_engine(monkeypatch)

    resp = client.post("/extract", files={"file": ("scan.png", _png_bytes(), "image/png")})

    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "OCR" in detail
    assert "vision" in detail.lower()


def test_extract_png_through_fake_ocr_engine(monkeypatch):
    # The stateless endpoint must handle a plain image upload via local OCR:
    # source "ocr", OCR-discounted confidence, no network, no real engine.
    _clear_ai_env(monkeypatch)
    engine = _fake_ocr_engine(monkeypatch)

    resp = client.post("/extract", files={"file": ("scan.png", _png_bytes(), "image/png")})

    assert resp.status_code == 200
    result = resp.json()
    assert len(engine.calls) == 1
    assert result["text_source"] == "ocr"
    assert result["ocr_engine"] == "fakeocr"
    assert result["invoice"]["invoice_number"] == "INV-OCR-4242"
    assert result["invoice"]["total_amount"] == 120.0
    assert result["backend"] == "heuristic-v1"
    assert any("OCR" in w for w in result["warnings"])
    # OCR discount applied: the heuristic's 0.9 labeled confidence becomes 0.81.
    assert result["field_confidence"]["invoice_number"] == 0.81


def test_extract_scanned_pdf_through_fake_ocr_engine(monkeypatch, tmp_path):
    # A PDF with no text layer should be rendered + OCR'd, not rejected, and
    # the response should carry the "no text layer" provenance note.
    _clear_ai_env(monkeypatch)
    monkeypatch.setenv("INVOICELENS_PAGE_DIR", str(tmp_path))
    engine = _fake_ocr_engine(monkeypatch)

    resp = client.post("/extract", files={"file": ("scan.pdf", _scanned_pdf_bytes(), "application/pdf")})

    assert resp.status_code == 200
    result = resp.json()
    assert result["text_source"] == "ocr"
    assert len(engine.calls) == 1 and len(engine.calls[0]) == 1  # one rendered page
    assert any("no text layer" in w for w in result["warnings"])
    assert result["invoice"]["invoice_number"] == "INV-OCR-4242"
