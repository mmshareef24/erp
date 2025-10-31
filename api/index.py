# Vercel Serverless Function entrypoint for FastAPI (ASGI)
# This exposes the FastAPI app from backend.main so Vercel can run it as a single function.

from backend.main import app as fastapi_app

# Vercel's Python runtime detects ASGI apps via a module-level `app` symbol.
app = fastapi_app