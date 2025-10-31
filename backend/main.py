from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .apps_registry import App, APPS
from .modules.sales import router as sales_router
from .modules.zatca import router as zatca_router
from .modules.catalogs import router as catalogs_router
from .modules.accounting import router as accounting_router
from .modules.finance import router as finance_router
from .modules.purchases import router as purchases_router
from .modules.inventory import router as inventory_router
from .modules.settings import router as settings_router
from .modules.production import router as production_router
from .modules.mrp import router as mrp_router
from .modules.employees import router as employees_router
from .modules.time import router as time_router
from .modules.auth import router as auth_router
from .modules.banking import router as banking_router
from .db import init_db


app = FastAPI(title="Matrix ERP")

# Basic CORS for future frontend integrations
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files (CSS, assets)
app.mount("/static", StaticFiles(directory="backend/static"), name="static")

# Jinja2 templates setup
templates_env = Environment(
    loader=FileSystemLoader("backend/templates"),
    autoescape=select_autoescape(["html", "xml"]),
)


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    template = templates_env.get_template("index.html")
    html = template.render(apps=APPS, request=request)
    return HTMLResponse(content=html, status_code=200)


@app.get("/app/{slug}", response_class=HTMLResponse)
async def app_page(request: Request, slug: str):
    app_item = next((a for a in APPS if a.slug == slug), None)
    if not app_item:
        raise HTTPException(status_code=404, detail="App not found")
    template = templates_env.get_template("app.html")
    html = template.render(app_item=app_item, request=request)
    return HTMLResponse(content=html, status_code=200)


@app.get("/api/apps")
async def api_apps():
    return [a.model_dump() for a in APPS]

# Include module routers
app.include_router(sales_router)
app.include_router(catalogs_router)
app.include_router(accounting_router)
app.include_router(finance_router)
app.include_router(purchases_router)
app.include_router(inventory_router)
app.include_router(settings_router)
app.include_router(production_router)
app.include_router(mrp_router)
app.include_router(zatca_router)
app.include_router(employees_router)
app.include_router(time_router)
app.include_router(auth_router)
app.include_router(banking_router)
# Initialize database (users, customers, products)
init_db()

# --- Simple scheduler for document expiry alerts ---
import threading
import time
import json
from pathlib import Path

_stop_event = threading.Event()

def _compute_expiry_alerts() -> list[dict]:
    DATA_DIR = Path("backend/data")
    EMPLOYEES_FILE = DATA_DIR / "employees.json"
    try:
        emps = json.loads(EMPLOYEES_FILE.read_text(encoding="utf-8"))
    except Exception:
        emps = []
    from datetime import datetime, date
    def _days_until(dstr: str) -> int | None:
        try:
            d = datetime.strptime((dstr or "").strip(), "%Y-%m-%d").date()
            return (d - date.today()).days
        except Exception:
            return None
    alerts: list[dict] = []
    for e in emps:
        for kind in ["iqama", "passport"]:
            dstr = e.get(f"{kind}_expiry", "")
            days = _days_until(dstr)
            status = "unknown"
            if days is None:
                status = "unknown"
            elif days < 0:
                status = "expired"
            elif days <= 60:
                status = "soon"
            else:
                status = "ok"
            alerts.append({
                "emp_id": e.get("emp_id"),
                "name": e.get("name"),
                "type": kind,
                "expiry": dstr,
                "status": status,
                "days": days,
            })
    return alerts

def _scheduler_loop():
    DATA_DIR = Path("backend/data")
    ALERTS_FILE = DATA_DIR / "alerts.json"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    while not _stop_event.is_set():
        alerts = _compute_expiry_alerts()
        try:
            ALERTS_FILE.write_text(json.dumps(alerts, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        # Sleep for a minute; adjust as needed
        _stop_event.wait(60)

@app.on_event("startup")
async def _start_scheduler():
    t = threading.Thread(target=_scheduler_loop, daemon=True)
    t.start()

@app.on_event("shutdown")
async def _stop_scheduler():
    _stop_event.set()