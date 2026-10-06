"""In-memory document store keyed by document_id.

Scoped deliberately: this is a demo service, not a production data layer.
Swapping this module for a real database-backed store (documented as a
Phase-2 idea in NOTES.md) would not require changing any other module,
since `main.py` only calls the functions below.
"""
import threading
import uuid
from dataclasses import dataclass
from typing import Dict, Optional

from .models import ExtractionResult


@dataclass
class StoredDocument:
    document_id: str
    filename: str
    file_path: str
    text: str
    extraction: Optional[ExtractionResult] = None
    # parsing.ParsedDocument for this upload: where the text came from, the OCR
    # engine/score, and any page images (needed to re-extract an image or a
    # scanned PDF through the vision backend).
    parsed: Optional[object] = None


class DocumentStore:
    def __init__(self):
        self._docs: Dict[str, StoredDocument] = {}
        self._lock = threading.Lock()

    def add(self, filename: str, file_path: str, text: str, parsed=None) -> StoredDocument:
        document_id = uuid.uuid4().hex[:12]
        doc = StoredDocument(document_id=document_id, filename=filename, file_path=file_path,
                             text=text, parsed=parsed)
        with self._lock:
            self._docs[document_id] = doc
        return doc

    def get(self, document_id: str) -> Optional[StoredDocument]:
        with self._lock:
            return self._docs.get(document_id)

    def set_extraction(self, document_id: str, result: ExtractionResult) -> None:
        with self._lock:
            if document_id in self._docs:
                self._docs[document_id].extraction = result


# Process-wide singleton store used by the FastAPI app.
store = DocumentStore()
