"""Text extraction from messy documents: PDF, DOCX, and plain text."""
import os

import pdfplumber
from docx import Document as DocxDocument

SUPPORTED_EXTENSIONS = (".pdf", ".docx", ".txt", ".text")


class UnsupportedDocumentType(ValueError):
    pass


def extract_text(file_path: str) -> str:
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".pdf":
        return _extract_pdf(file_path)
    if ext == ".docx":
        return _extract_docx(file_path)
    if ext in (".txt", ".text"):
        return _extract_txt(file_path)
    raise UnsupportedDocumentType(f"unsupported document type: {ext or '(no extension)'}")


def _extract_pdf(file_path: str) -> str:
    pages = []
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            # layout=True preserves horizontal whitespace between columns,
            # which the heuristic line-item parser relies on.
            pages.append(page.extract_text(layout=True) or "")
    return "\n".join(pages).strip()


def _extract_docx(file_path: str) -> str:
    doc = DocxDocument(file_path)
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            parts.append("\t".join(cell.text for cell in row.cells))
    return "\n".join(parts).strip()


def _extract_txt(file_path: str) -> str:
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        return f.read().strip()
