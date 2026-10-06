"""Local dev launcher.

Loads `.env` (gitignored) if present, then serves the FastAPI app. With AI
credentials in `.env` (ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN/BASE_URL/MODEL)
the app uses the Claude-compatible extractor; without them it falls back to the
offline heuristic extractor. Tests never import this file, so ambient `.env`
secrets can never leak into or change the deterministic test run.

Image and scanned-PDF support is reported at startup too, because both depend on
optional setup: a local OCR engine (`requirements-ocr.txt`) and/or AI
credentials for the vision route.

Usage (from the repo root):
    python scripts/run_local.py
"""
import os
import sys

from dotenv import load_dotenv

load_dotenv()  # no-op if there is no .env file

import uvicorn  # noqa: E402

# `python scripts/run_local.py` puts scripts/ on sys.path, not the repo root,
# so `import app.*` (here and inside uvicorn) would fail. Pin the root instead.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

if __name__ == "__main__":
    from app.extraction import ai_available, image_mode, vision_enabled
    from app.ocr import available_engine
    from app.parsing import IMAGE_EXTENSIONS, SUPPORTED_EXTENSIONS

    port = int(os.environ.get("PORT", "8000"))
    ocr = available_engine() or "off (pip install -r requirements-ocr.txt)"
    vision = "on" if vision_enabled() else "off (needs ANTHROPIC_* env, IMAGE_MODE!=ocr)"
    modes = " ".join(SUPPORTED_EXTENSIONS) + " (images: " + " ".join(IMAGE_EXTENSIONS) + ")"

    print(f"[InvoiceLens] extraction backend: {'AI (anthropic-compatible)' if ai_available() else 'heuristic (offline)'}")
    print(f"[InvoiceLens] image/scan input: OCR={ocr}, vision={vision}, IMAGE_MODE={image_mode()}")
    print(f"[InvoiceLens] accepted uploads: {modes}")
    print(f"[InvoiceLens] serving on http://localhost:{port}  (docs at /docs)")
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=False)
