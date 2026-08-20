import os

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

SAMPLE_DIR = os.path.join(os.path.dirname(__file__), "..", "sample_docs")


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


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
