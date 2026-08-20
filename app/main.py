"""FastAPI service: upload a document, extract structured invoice data from it."""
import os
import tempfile

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from .extraction import ExtractionError, extract_invoice
from .models import DocumentMetadata, ExtractionResult
from .parsing import SUPPORTED_EXTENSIONS, UnsupportedDocumentType, extract_text
from .storage import store

app = FastAPI(
    title="doc-extractor",
    description="Extracts structured, schema-validated, confidence-scored invoice data from messy documents.",
    version="0.1.0",
)

UPLOAD_DIR = os.environ.get("DOC_EXTRACTOR_UPLOAD_DIR", tempfile.mkdtemp(prefix="doc-extractor-"))
os.makedirs(UPLOAD_DIR, exist_ok=True)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/documents", response_model=DocumentMetadata, status_code=201)
async def upload_document(file: UploadFile):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=422, detail=f"unsupported file type '{ext}'. Supported: {SUPPORTED_EXTENSIONS}")

    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=422, detail="uploaded file is empty")

    dest_path = os.path.join(UPLOAD_DIR, f"{os.urandom(8).hex()}{ext}")
    with open(dest_path, "wb") as f:
        f.write(contents)

    try:
        text = extract_text(dest_path)
    except UnsupportedDocumentType as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:  # pdfplumber/docx can raise a variety of parse errors on corrupt files
        raise HTTPException(status_code=422, detail=f"failed to parse document: {exc}")

    if not text:
        raise HTTPException(status_code=422, detail="no extractable text found in document")

    doc = store.add(filename=file.filename, file_path=dest_path, text=text)
    return DocumentMetadata(
        document_id=doc.document_id,
        filename=doc.filename,
        char_count=len(doc.text),
        has_extraction=False,
    )


@app.post("/documents/{document_id}/extract", response_model=ExtractionResult)
def extract_document(document_id: str):
    doc = store.get(document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="document not found")

    try:
        result = extract_invoice(document_id=doc.document_id, filename=doc.filename, text=doc.text)
    except ExtractionError as exc:
        return JSONResponse(
            status_code=422,
            content={"detail": str(exc), "warnings": exc.warnings},
        )

    store.set_extraction(document_id, result)
    return result


@app.get("/documents/{document_id}/extraction", response_model=ExtractionResult)
def get_extraction(document_id: str):
    doc = store.get(document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="document not found")
    if doc.extraction is None:
        raise HTTPException(status_code=404, detail="document has not been extracted yet; POST /documents/{id}/extract first")
    return doc.extraction


@app.get("/documents/{document_id}", response_model=DocumentMetadata)
def get_document(document_id: str):
    doc = store.get(document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="document not found")
    return DocumentMetadata(
        document_id=doc.document_id,
        filename=doc.filename,
        char_count=len(doc.text),
        has_extraction=doc.extraction is not None,
    )
