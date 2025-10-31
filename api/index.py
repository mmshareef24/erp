# Vercel Serverless Function entrypoint for FastAPI (ASGI)
# Ensures repository root is on sys.path so `backend.main` is importable in Vercel.

import os
import sys

# Add repo root to import path (one level above /api)
ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.append(ROOT)

from backend.main import app as fastapi_app

# Vercel's Python runtime detects ASGI apps via a module-level `app` symbol.
app = fastapi_app