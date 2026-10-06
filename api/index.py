"""Vercel / AWS Lambda entrypoint.

Vercel's Python runtime builds this file as a serverless function. Mangum
adapts the ASGI FastAPI app to the Lambda/API-Gateway event format. All
routing is delegated to the app, so every FastAPI endpoint
(/health, /documents, /documents/{id}/extract, /extract, /docs, ...) is
served through this single handler.
"""
import os
import sys

# Make the sibling `app/` package importable regardless of the function's cwd
# inside the Lambda/Vercel runtime.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app  # noqa: E402
from mangum import Mangum  # noqa: E402

# lifespan="off": Lambda has no startup/shutdown cycle to run, and skipping it
# avoids re-initialising the app on warm invocations.
handler = Mangum(app, lifespan="off")
