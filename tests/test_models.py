import pytest
from pydantic import ValidationError

from app.models import ExtractionResult, Invoice, LineItem


def test_invoice_defaults():
    invoice = Invoice()
    assert invoice.currency == "USD"
    assert invoice.line_items == []
    assert invoice.total_amount is None


def test_invoice_rejects_non_numeric_total_amount():
    with pytest.raises(ValidationError):
        Invoice(total_amount="not a number")


def test_invoice_accepts_valid_line_items():
    invoice = Invoice(line_items=[{"description": "Widget", "quantity": 2, "unit_price": 10.0, "amount": 20.0}])
    assert isinstance(invoice.line_items[0], LineItem)
    assert invoice.line_items[0].amount == 20.0


def test_extraction_result_overall_confidence_bounds():
    with pytest.raises(ValidationError):
        ExtractionResult(
            document_id="abc",
            filename="f.pdf",
            invoice=Invoice(),
            field_confidence={},
            overall_confidence=1.5,  # out of [0,1] bound
            backend="heuristic-v1",
            attempts=1,
        )
