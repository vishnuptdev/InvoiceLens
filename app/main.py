"""FastAPI service: upload a document or image, extract structured invoice data
from it."""
import os
import tempfile

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from .extraction import ExtractionError, ai_available, can_read_images, extract_invoice
from .models import DocumentMetadata, ExtractionResult
from .ocr import available_engine
from .parsing import (
    IMAGE_EXTENSIONS,
    ParsedDocument,
    SUPPORTED_EXTENSIONS,
    UnsupportedDocumentType,
    parse_document,
)
from .storage import store


def _no_text_error(ext: str, parsed: ParsedDocument) -> HTTPException:
    """Explain *why* nothing could be read, and what would fix it - an image
    upload failing for a missing OCR engine is a setup problem, not a bad file."""
    if ext in IMAGE_EXTENSIONS or parsed.images:
        if ai_available() and os.environ.get("EXTRACTION_MODE", "auto").strip().lower() == "fast":
            # Credentials are present but the mode pins the OCR-blind heuristic
            # backend, so the pixels can never be read. Say so instead of
            # telling the user to install something they may already have.
            hint = (
                "EXTRACTION_MODE=fast disables the vision backend; use auto or ai "
                "(or install a local OCR engine, see requirements-ocr.txt)"
            )
        else:
            hint = (
                "install a local OCR engine (see requirements-ocr.txt) to read it offline, "
                "or set ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN so the pages can be read by "
                "a vision model"
            )
    else:
        hint = "the document has no text layer - a scan or a blank page"
    return HTTPException(status_code=422, detail=f"no extractable text found in document: {hint}")


async def _parse_upload(file: UploadFile) -> tuple[str, ParsedDocument]:
    """Validate + parse an upload into (dest_path, ParsedDocument). Shared by the
    stateful `/documents` endpoint and the stateless `/extract` endpoint.

    Text-less input is not automatically a rejection: an image or scanned PDF
    with no OCR installed still has page images, which the vision backend can
    read, so it is only rejected when neither route can possibly work."""
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
        parsed = parse_document(dest_path)
    except UnsupportedDocumentType as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:  # pdfplumber/docx/cv2 can raise a variety of parse errors on corrupt files
        raise HTTPException(status_code=422, detail=f"failed to parse document: {exc}")

    if not parsed.text and not (parsed.images and can_read_images()):
        raise _no_text_error(ext, parsed)
    return dest_path, parsed

app = FastAPI(
    title="InvoiceLens",
    description="Extracts structured, schema-validated, confidence-scored invoice data from messy PDFs, DOCX files, plain text, images and scanned PDFs.",
    version="0.1.0",
)

UPLOAD_DIR = os.environ.get("INVOICELENS_UPLOAD_DIR", tempfile.mkdtemp(prefix="invoicelens-"))
os.makedirs(UPLOAD_DIR, exist_ok=True)


@app.get("/health")
def health():
    """Also reports what the process can actually do right now, so image/OCR
    setup problems are visible without uploading a file to discover them."""
    return {
        "status": "ok",
        "extraction_mode": os.environ.get("EXTRACTION_MODE", "auto"),
        "image_mode": os.environ.get("IMAGE_MODE", "auto"),
        "ai_backend": ai_available(),
        "vision_for_images": can_read_images(),
        "ocr_engine": available_engine(),
    }


@app.post("/documents", response_model=DocumentMetadata, status_code=201)
async def upload_document(file: UploadFile):
    dest_path, parsed = await _parse_upload(file)

    doc = store.add(filename=file.filename, file_path=dest_path, text=parsed.text, parsed=parsed)
    return DocumentMetadata(
        document_id=doc.document_id,
        filename=doc.filename,
        char_count=len(doc.text),
        has_extraction=False,
    )


@app.post("/extract", response_model=ExtractionResult)
async def extract_upload(file: UploadFile):
    """Stateless one-shot: parse + extract in a single request. This is the
    endpoint to use on serverless hosts (Vercel/Lambda), where the in-memory
    `store` does not survive across the upload->extract two-call flow."""
    _, parsed = await _parse_upload(file)  # the written file is only needed for parsing; page images live in `parsed`

    try:
        result = extract_invoice(
            document_id="inline", filename=file.filename or "document", text=parsed.text, parsed=parsed
        )
    except ExtractionError as exc:
        return JSONResponse(
            status_code=422,
            content={"detail": str(exc), "warnings": exc.warnings},
        )
    return result


@app.post("/documents/{document_id}/extract", response_model=ExtractionResult)
def extract_document(document_id: str):
    doc = store.get(document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="document not found")

    try:
        result = extract_invoice(document_id=doc.document_id, filename=doc.filename, text=doc.text, parsed=doc.parsed)
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


# Serve the built frontend (frontend/dist) at / when it exists. Registered
# last so every API route above takes precedence over the static mount.
# On Vercel the static build is served by the platform itself (see
# vercel.json outputDirectory) and this mount is simply inactive.
_FRONTEND_DIST = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend", "dist"
)
if os.path.isdir(_FRONTEND_DIST):
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=_FRONTEND_DIST, html=True), name="ui")
