from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timedelta
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse
from starlette.responses import RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from ..db import SessionLocal, Customer

DATA_DIR = Path("backend/data")
AR_LEDGER_FILE = DATA_DIR / "ar_ledger.json"
INVOICES_FILE = DATA_DIR / "invoices.json"
DATA_DIR.mkdir(parents=True, exist_ok=True)
if not AR_LEDGER_FILE.exists():
    AR_LEDGER_FILE.write_text("[]", encoding="utf-8")


def _load_json(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def load_ar() -> list[dict]:
    return _load_json(AR_LEDGER_FILE)


def save_ar(entries: list[dict]):
    AR_LEDGER_FILE.write_text(json.dumps(entries, indent=2), encoding="utf-8")


def append_ar_entry(entry: dict):
    entries = load_ar()
    entries.append(entry)
    save_ar(entries)


def load_invoices() -> list[dict]:
    return _load_json(INVOICES_FILE)


def invoice_open_amount(entries: list[dict], invoice_id: str) -> float:
    amt = 0.0
    for e in entries:
        if e.get("invoice_id") == invoice_id:
            if e.get("type") == "invoice":
                amt += float(e.get("amount", 0.0))
            elif e.get("type") in ("payment", "credit"):
                amt -= float(e.get("amount", 0.0))
            elif e.get("type") == "adjustment":
                amt += float(e.get("amount", 0.0))
    return round(amt, 2)


def customer_balance(entries: list[dict], customer: str) -> float:
    amt = 0.0
    for e in entries:
        if e.get("customer") == customer:
            if e.get("type") == "invoice":
                amt += float(e.get("amount", 0.0))
            elif e.get("type") in ("payment", "credit"):
                amt -= float(e.get("amount", 0.0))
            elif e.get("type") == "adjustment":
                amt += float(e.get("amount", 0.0))
    return round(amt, 2)


def due_date_from_invoice(inv: dict, days: int = 30) -> datetime:
    try:
        # invoice['date'] is ISO with trailing Z
        dt = datetime.fromisoformat(inv.get("date", "").replace("Z", ""))
    except Exception:
        dt = datetime.utcnow()
    return dt + timedelta(days=days)


def aging_buckets(open_invoices: list[dict]) -> dict:
    # Returns {customer: {"current": x, "30": y, "60": z, "90": w, "120": u}}
    buckets: dict[str, dict[str, float]] = {}
    now = datetime.utcnow()
    for inv in open_invoices:
        cust = inv.get("customer")
        amt = float(inv.get("open", 0.0))
        if amt <= 0:
            continue
        dd = due_date_from_invoice(inv)
        days_over = (now - dd).days
        key = "current"
        if days_over > 0 and days_over <= 30:
            key = "30"
        elif days_over <= 60:
            key = "60"
        elif days_over <= 90:
            key = "90"
        elif days_over > 90:
            key = "120"
        buckets.setdefault(cust, {"current": 0.0, "30": 0.0, "60": 0.0, "90": 0.0, "120": 0.0})
        buckets[cust][key] += amt
    # round
    for cust, b in buckets.items():
        for k in list(b.keys()):
            b[k] = round(b[k], 2)
    return buckets


templates_env = Environment(
    loader=FileSystemLoader("backend/templates"),
    autoescape=select_autoescape(["html", "xml"]),
)

router = APIRouter(prefix="/accounting", tags=["Accounting"])


@router.get("/ar", response_class=HTMLResponse)
async def ar_list(request: Request):
    entries = load_ar()
    tpl = templates_env.get_template("accounting_ar.html")
    return HTMLResponse(tpl.render(request=request, entries=entries))


@router.get("/ar/customers", response_class=HTMLResponse)
async def ar_customers(request: Request):
    entries = load_ar()
    # Base list from DB for visibility
    with SessionLocal() as db:
        db_customers = db.query(Customer).order_by(Customer.name.asc()).all()
    rows = []
    for c in db_customers:
        rows.append({
            "name": c.name,
            "country": c.country_code or "-",
            "ar_account": c.ar_account or "AR",
            "balance": customer_balance(entries, c.name),
        })
    tpl = templates_env.get_template("accounting_ar_customers.html")
    return HTMLResponse(tpl.render(request=request, customers=rows))


@router.get("/ar/invoices", response_class=HTMLResponse)
async def ar_invoices(request: Request):
    entries = load_ar()
    invoices = load_invoices()
    rows = []
    for inv in invoices:
        open_amt = invoice_open_amount(entries, inv.get("id"))
        dd = due_date_from_invoice(inv)
        status = "paid" if open_amt <= 0 else ("overdue" if datetime.utcnow() > dd else "open")
        rows.append({
            "id": inv.get("id"),
            "customer": inv.get("customer"),
            "date": inv.get("date"),
            "due_date": dd.isoformat(timespec="seconds") + "Z",
            "total": float(inv.get("total", 0.0)),
            "open": open_amt,
            "status": status,
        })
    tpl = templates_env.get_template("accounting_ar_invoices.html")
    return HTMLResponse(tpl.render(request=request, invoices=rows))


@router.get("/ar/aging", response_class=HTMLResponse)
async def ar_aging(request: Request):
    entries = load_ar()
    invoices = load_invoices()
    # Build list of open invoices with open amount
    open_invs = []
    for inv in invoices:
        open_amt = invoice_open_amount(entries, inv.get("id"))
        if open_amt > 0:
            inv_copy = dict(inv)
            inv_copy["open"] = open_amt
            open_invs.append(inv_copy)
    buckets = aging_buckets(open_invs)
    tpl = templates_env.get_template("accounting_ar_aging.html")
    return HTMLResponse(tpl.render(request=request, aging=buckets))


@router.get("/ar/adjustments/new", response_class=HTMLResponse)
async def ar_adjustment_new(request: Request):
    with SessionLocal() as db:
        customers = db.query(Customer).order_by(Customer.name.asc()).all()
    tpl = templates_env.get_template("accounting_ar_adjustment_new.html")
    return HTMLResponse(tpl.render(request=request, customers=customers))


@router.post("/ar/adjustments")
async def ar_adjustment_create(
    customer: str = Form(...),
    amount: float = Form(...),
    direction: str = Form("debit"),  # debit increases AR, credit decreases
    invoice_id: str = Form("")
):
    signed = amount if direction == "debit" else -abs(amount)
    append_ar_entry({
        "id": f"adj-{datetime.utcnow().timestamp()}",
        "date": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "type": "adjustment",
        "customer": customer,
        "invoice_id": invoice_id or None,
        "amount": signed,
        "ar_account": "AR",
    })
    return RedirectResponse(url="/accounting/ar", status_code=303)