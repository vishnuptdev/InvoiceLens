"""Pydantic schema for the extracted document type.

Document type chosen: **invoice** (see NOTES.md for the tradeoff/rationale).
All numeric fields are Optional so a document missing a field still produces
a valid `Invoice` instead of a hard failure - the *values that ARE present*
are what get strictly type-checked by Pydantic.
"""
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class LineItem(BaseModel):
    description: str
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    amount: Optional[float] = None


class Invoice(BaseModel):
    vendor_name: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    due_date: Optional[str] = None
    total_amount: Optional[float] = None
    currency: str = "USD"
    line_items: List[LineItem] = Field(default_factory=list)


class ExtractionResult(BaseModel):
    document_id: str
    filename: str
    invoice: Invoice
    field_confidence: Dict[str, float]
    overall_confidence: float = Field(ge=0.0, le=1.0)
    backend: str
    attempts: int
    warnings: List[str] = Field(default_factory=list)


class DocumentMetadata(BaseModel):
    document_id: str
    filename: str
    char_count: int
    has_extraction: bool
