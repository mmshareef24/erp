from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from jinja2 import Environment, FileSystemLoader, select_autoescape
from datetime import datetime

from .apps_registry import App, APPS
from .modules.sales import router as sales_router
from .modules.sales import load_invoices as load_sales_invoices, load_orders as load_sales_orders
from .modules.zatca import router as zatca_router
from .modules.catalogs import router as catalogs_router
from .modules.accounting import router as accounting_router
from .modules.finance import router as finance_router
from .modules.purchases import router as purchases_router
from .modules.purchases import load_bills as load_purchase_bills, load_orders as load_purchase_orders
from .modules.inventory import router as inventory_router
from .modules.inventory import compute_on_hand
from .modules.settings import router as settings_router
from .modules.production import router as production_router
from .modules.mrp import router as mrp_router
from .modules.employees import router as employees_router
from .modules.time import router as time_router
from .modules.hr import router as hr_router
from .modules.auth import router as auth_router
from .modules.banking import router as banking_router
from .modules.chart import router as chart_router
from .modules.quality import router as quality_router
from .modules.slitting import router as slitting_router
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


@app.get("/api/dashboard/data")
async def dashboard_data(from_date: str | None = None, to_date: str | None = None):
    """Aggregate simple metrics for charts on the dashboard.

    Returns keys:
    - sales_invoices_status: {open, paid} counts
    - sales_invoices_monthly: [{month, total}] ordered by month
    - sales_orders_status: {confirmed, delivered, invoiced} counts
    - purchases_bills_status: {open, paid} counts
    - purchases_bills_monthly: [{month, total}] ordered by month
    - inventory_top_value: [{product, value}] top 5 by value
    """
    # Helper: parse and check date range
    def _parse_date(d: str | None):
        if not d:
            return None
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m", "%Y/%m"):
            try:
                return datetime.strptime(d, fmt).date()
            except Exception:
                continue
        return None

    start_dt = _parse_date(from_date) if from_date else None
    end_dt = _parse_date(to_date) if to_date else None

    def _in_range(dstr: str | None):
        # If no filters, everything is in range
        if start_dt is None and end_dt is None:
            return True
        d = _parse_date(dstr)
        if d is None:
            # If we have filters but date is missing/unparseable, exclude
            return False
        if start_dt and d < start_dt:
            return False
        if end_dt and d > end_dt:
            return False
        return True

    # --- Sales invoices status and monthly totals ---
    invoices = []
    try:
        invoices = load_sales_invoices()
    except Exception:
        invoices = []
    inv_status = {"open": 0, "paid": 0}
    inv_months: dict[str, float] = {}
    for i in invoices:
        if _in_range(getattr(i, "date", None)):
            inv_status[i.status] = inv_status.get(i.status, 0) + 1
            month = (i.date or "")[:7]  # YYYY-MM
            try:
                total = float(i.total or 0)
            except Exception:
                total = 0.0
            inv_months[month] = inv_months.get(month, 0.0) + total

    sales_orders = []
    try:
        sales_orders = load_sales_orders()
    except Exception:
        sales_orders = []
    orders_status = {"confirmed": 0, "delivered": 0, "invoiced": 0}
    for o in sales_orders:
        if _in_range(getattr(o, "date", None)):
            orders_status[o.status] = orders_status.get(o.status, 0) + 1

    # --- Purchases bills status and monthly totals ---
    bills = []
    try:
        bills = load_purchase_bills()
    except Exception:
        bills = []
    bills_status = {"open": 0, "paid": 0}
    bills_months: dict[str, float] = {}
    for b in bills:
        if _in_range(getattr(b, "date", None)):
            bills_status[b.status] = bills_status.get(b.status, 0) + 1
            month = (b.date or "")[:7]
            try:
                total = float(b.total or 0)
            except Exception:
                total = 0.0
            bills_months[month] = bills_months.get(month, 0.0) + total

    # Purchases orders status
    po_orders = []
    try:
        po_orders = load_purchase_orders()
    except Exception:
        po_orders = []
    po_status = {"confirmed": 0, "billed": 0}
    for o in po_orders:
        if _in_range(getattr(o, "date", None)):
            po_status[o.status] = po_status.get(o.status, 0) + 1

    # --- Inventory top value by product ---
    inv_top = []
    try:
        on_hand = compute_on_hand()
        pairs = [(p, float(meta.get("value", 0.0))) for p, meta in on_hand.items()]
        pairs.sort(key=lambda x: x[1], reverse=True)
        inv_top = [{"product": p, "value": v} for p, v in pairs[:5]]
    except Exception:
        inv_top = []

    def _sorted_months(d: dict[str, float]):
        # Sort YYYY-MM lexicographically which matches chronological if same format
        return [{"month": m, "total": round(t, 2)} for m, t in sorted(d.items())]

    return {
        "sales_invoices_status": inv_status,
        "sales_invoices_monthly": _sorted_months(inv_months),
        "sales_orders_status": orders_status,
        "purchases_bills_status": bills_status,
        "purchases_bills_monthly": _sorted_months(bills_months),
        "purchases_orders_status": po_status,
        "inventory_top_value": inv_top,
    }


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
app.include_router(hr_router)
app.include_router(auth_router)
app.include_router(banking_router)
app.include_router(chart_router)
app.include_router(slitting_router)
app.include_router(quality_router)
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