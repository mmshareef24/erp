from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse
from starlette.responses import RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape


templates_env = Environment(
    loader=FileSystemLoader("backend/templates"),
    autoescape=select_autoescape(["html", "xml"]),
)

router = APIRouter(prefix="/auth", tags=["Auth"])

DATA_DIR = Path("backend/data")
USERS_FILE = DATA_DIR / "users.json"
DATA_DIR.mkdir(parents=True, exist_ok=True)
if not USERS_FILE.exists():
    # Initialize with a default admin user: admin/admin (hashed)
    default = [{
        "username": "admin",
        "password_hash": hashlib.sha256("admin".encode("utf-8")).hexdigest(),
        "role": "admin"
    }]
    USERS_FILE.write_text(json.dumps(default, ensure_ascii=False, indent=2), encoding="utf-8")


def load_users() -> list[dict]:
    try:
        return json.loads(USERS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def get_user(username: str) -> Optional[dict]:
    return next((u for u in load_users() if u.get("username") == username), None)


def check_password(user: dict, password: str) -> bool:
    ph = user.get("password_hash", "")
    return ph == hashlib.sha256((password or "").encode("utf-8")).hexdigest()


def get_current_user(request: Request) -> Optional[dict]:
    uname = request.cookies.get("session_user") or ""
    if not uname:
        return None
    return get_user(uname)


def require_roles(request: Request, roles: list[str]) -> Optional[HTMLResponse]:
    user = get_current_user(request)
    if not user:
        # Not logged in; redirect to login
        return RedirectResponse(url="/auth/login", status_code=303)
    role = (user.get("role") or "").lower()
    allowed = [r.lower() for r in roles]
    if role not in allowed:
        return HTMLResponse("Forbidden: insufficient permissions", status_code=403)
    return None


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str | None = None):
    tpl = templates_env.get_template("auth_login.html")
    return HTMLResponse(tpl.render(request=request, error=error or ""))


@router.post("/login")
async def login(username: str = Form(""), password: str = Form("")):
    user = get_user((username or "").strip())
    if not user or not check_password(user, password or ""):
        return RedirectResponse(url="/auth/login?error=Invalid%20credentials", status_code=303)
    resp = RedirectResponse(url="/", status_code=303)
    resp.set_cookie("session_user", user.get("username") or "", httponly=True, samesite="lax")
    return resp


@router.get("/logout")
async def logout():
    resp = RedirectResponse(url="/auth/login", status_code=303)
    resp.delete_cookie("session_user")
    return resp