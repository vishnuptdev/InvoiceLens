import json
import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from app.extraction import (
    AnthropicExtractor,
    ExtractionError,
    HeuristicExtractor,
    _clean_numeric,
    _repair_candidate,
    extract_invoice,
    get_extractor,
    validate_with_retry,
)
from app.models import Invoice
from app.parsing import extract_text

SAMPLE_DIR = os.path.join(os.path.dirname(__file__), "..", "sample_docs")


def test_get_extractor_defaults_to_heuristic(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    backend = get_extractor()
    assert isinstance(backend, HeuristicExtractor)


def _fake_anthropic_response(payload: dict):
    return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(payload))])


def test_get_extractor_returns_anthropic_when_key_set(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    with patch("anthropic.Anthropic") as mock_ctor:
        mock_ctor.return_value = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: None))
        backend = get_extractor()
    assert isinstance(backend, AnthropicExtractor)


def test_anthropic_extractor_parses_json_and_clamps_confidence(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    payload = {
        "vendor_name": "Acme Inc.",
        "invoice_number": "INV-1",
        "invoice_date": "01/01/2026",
        "due_date": None,
        "total_amount": 100.0,
        "currency": "USD",
        "line_items": [],
        "confidence": {"vendor_name": 1.4, "invoice_number": -0.2, "total_amount": 0.75},
    }
    with patch("anthropic.Anthropic") as mock_ctor:
        mock_ctor.return_value = SimpleNamespace(
            messages=SimpleNamespace(create=lambda **kw: _fake_anthropic_response(payload))
        )
        extractor = AnthropicExtractor(api_key="test-key-not-real")
        candidate, confidence = extractor.extract("some invoice text")

    assert candidate["vendor_name"] == "Acme Inc."
    assert "confidence" not in candidate  # popped out of the candidate dict
    assert confidence["vendor_name"] == 1.0  # clamped from 1.4
    assert confidence["invoice_number"] == 0.0  # clamped from -0.2
    assert confidence["total_amount"] == 0.75
    assert confidence["currency"] == 0.5  # defaulted, model didn't report one


def test_anthropic_extractor_strips_markdown_fences(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    payload = {
        "vendor_name": "Acme Inc.", "invoice_number": None, "invoice_date": None,
        "due_date": None, "total_amount": None, "currency": "USD", "line_items": [],
    }
    fenced_text = "```json\n" + json.dumps(payload) + "\n```"
    with patch("anthropic.Anthropic") as mock_ctor:
        mock_ctor.return_value = SimpleNamespace(
            messages=SimpleNamespace(create=lambda **kw: SimpleNamespace(content=[SimpleNamespace(text=fenced_text)]))
        )
        extractor = AnthropicExtractor(api_key="test-key-not-real")
        candidate, _ = extractor.extract("some invoice text")

    assert candidate["vendor_name"] == "Acme Inc."


def test_clean_numeric_strips_currency_and_commas():
    assert _clean_numeric("$1,234.56") == 1234.56
    assert _clean_numeric(42) == 42.0
    assert _clean_numeric(None) is None
    assert _clean_numeric("not a number") is None


def test_validate_with_retry_repairs_dirty_total_amount():
    candidate = {
        "vendor_name": "Acme Inc.",
        "invoice_number": "INV-1",
        "invoice_date": "01/01/2026",
        "due_date": None,
        "total_amount": "$1,234.56",  # dirty - Pydantic will reject this string as float
        "currency": "USD",
        "line_items": [],
    }
    invoice, attempts, warnings = validate_with_retry(candidate)
    assert isinstance(invoice, Invoice)
    assert invoice.total_amount == 1234.56
    assert attempts == 2  # first attempt fails, second (repaired) succeeds
    assert len(warnings) == 1


def test_validate_with_retry_raises_after_exhausting_attempts():
    # unrepairable: description is missing entirely, which _repair_candidate
    # has no fix for, so every attempt fails identically.
    candidate = {"line_items": [{"quantity": "abc"}]}
    with pytest.raises(ExtractionError) as exc_info:
        validate_with_retry(candidate, max_attempts=2)
    assert len(exc_info.value.warnings) == 2


def test_heuristic_extractor_on_sample_invoice_text():
    text = extract_text(os.path.join(SAMPLE_DIR, "sample_invoice.txt"))
    candidate, confidence = HeuristicExtractor().extract(text)

    assert candidate["invoice_number"] == "INV-10234"
    assert candidate["vendor_name"].startswith("Acme")
    assert len(candidate["line_items"]) == 3
    assert confidence["invoice_number"] == pytest.approx(0.9)
    assert all(0.0 <= v <= 1.0 for v in confidence.values())


def test_extract_invoice_end_to_end_on_sample_docx():
    text = extract_text(os.path.join(SAMPLE_DIR, "sample_invoice.docx"))
    result = extract_invoice(document_id="doc1", filename="sample_invoice.docx", text=text)

    assert result.invoice.invoice_number == "INV-10234"
    assert result.invoice.total_amount == 1005.50
    assert len(result.invoice.line_items) == 3
    assert result.backend == "heuristic-v1"
    assert 0.0 <= result.overall_confidence <= 1.0
    assert result.attempts >= 1


def test_extract_invoice_missing_fields_still_produces_valid_result():
    result = extract_invoice(document_id="doc2", filename="blank.txt", text="Not an invoice at all, just some prose.")
    assert result.invoice.invoice_number is None
    assert result.invoice.total_amount is None
    assert result.field_confidence["invoice_number"] == 0.0
