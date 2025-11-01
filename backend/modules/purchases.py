from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Request, HTTPException, Form
from fastapi.responses import HTMLResponse
from starlette.responses import RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel

from .finance import post_purchase_bill_to_gl, post_purchase_payment_to_gl
from .inventory import record_purchase_receipt


# Jinja environment for Purchases templates
templates_env = Environment(
    loader=FileSystemLoader("backend/templates"),
    autoescape=select_autoescape(["html", "xml"]),
)


# Simple JSON storage for Purchases
DATA_DIR = Path("backend/data")
ORDERS_FILE = DATA_DIR / "purchase_orders.json"
BILLS_FILE = DATA_DIR / "purchase_bills.json"
PAYMENTS_FILE = DATA_DIR / "purchase_payments.json"
DATA_DIR.mkdir(parents=True, exist_ok=True)
for f in [ORDERS_FILE, BILLS_FILE, PAYMENTS_FILE]:
    if not f.exists():
        f.write_text("[]", encoding="utf-8")


class PurchaseItem(BaseModel):
    product: str
    quantity: float
    unit_cost: float

    def line_total(self) -> float:
        return float(self.quantity) * float(self.unit_cost)


class PurchaseOrder(BaseModel):
    id: str
    vendor: str
    date: str
    items: list[PurchaseItem]
    status: str  # confirmed, billed
    total: float


def load_orders() -> list[PurchaseOrder]:
    return [PurchaseOrder(**o) for o in json.loads(ORDERS_FILE.read_text(encoding="utf-8"))]


def save_orders(orders: list[PurchaseOrder]) -> None:
    ORDERS_FILE.write_text(
        json.dumps([o.model_dump() for o in orders], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


class PurchaseBill(BaseModel):
    id: str
    order_id: str
    vendor: str
    date: str
    items: list[PurchaseItem]
    status: str  # open, paid
    subtotal: float
    tax_rate: float
    total: float


def load_bills() -> list[PurchaseBill]:
    return [PurchaseBill(**b) for b in json.loads(BILLS_FILE.read_text(encoding="utf-8"))]


def save_bills(bills: list[PurchaseBill]) -> None:
    BILLS_FILE.write_text(
        json.dumps([b.model_dump() for b in bills], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


class PurchasePayment(BaseModel):
    id: str
    bill_id: str
    vendor: str
    date: str
    amount: float
    method: str
    bank_account_id: str | None = None


def load_payments() -> list[PurchasePayment]:
    return [PurchasePayment(**p) for p in json.loads(PAYMENTS_FILE.read_text(encoding="utf-8"))]


def save_payments(payments: list[PurchasePayment]) -> None:
    PAYMENTS_FILE.write_text(
        json.dumps([p.model_dump() for p in payments], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


router = APIRouter(prefix="/purchases", tags=["Purchases"])


@router.get("/", response_class=HTMLResponse)
async def purchases_home():
    return RedirectResponse(url="/purchases/orders", status_code=303)


@router.get("/orders", response_class=HTMLResponse)
async def orders_list(request: Request):
    orders = load_orders()
    bills = load_bills()
    # Counts for sidebar badges
    open_po_count = sum(1 for o in orders if o.status == "confirmed")
    unpaid_bills_count = sum(1 for b in bills if b.status == "open")
    tpl = templates_env.get_template("purchases_orders.html")
    return HTMLResponse(
        tpl.render(
            request=request,
            orders=orders,
            open_po_count=open_po_count,
            unpaid_bills_count=unpaid_bills_count,
        )
    )


@router.get("/orders/new", response_class=HTMLResponse)
async def orders_new_form(request: Request):
    tpl = templates_env.get_template("purchases_order_new.html")
    return HTMLResponse(tpl.render(request=request))


@router.post("/orders")
async def orders_create(
    vendor: str = Form(...),
    product: list[str] = Form(...),
    quantity: list[float] = Form(...),
    unit_cost: list[float] = Form(...),
    status: str = Form("confirmed"),
):
    items: list[PurchaseItem] = []
    for i in range(len(product)):
        items.append(PurchaseItem(product=product[i], quantity=quantity[i], unit_cost=unit_cost[i]))
    total = sum(it.line_total() for it in items)
    order = PurchaseOrder(
        id=str(uuid4()),
        vendor=vendor,
        date=datetime.utcnow().isoformat(timespec="seconds") + "Z",
        items=items,
        status=status,
        total=total,
    )
    orders = load_orders()
    orders.append(order)
    save_orders(orders)
    return RedirectResponse(url=f"/purchases/orders/{order.id}", status_code=303)


@router.get("/orders/{order_id}", response_class=HTMLResponse)
async def order_detail(request: Request, order_id: str):
    order = next((x for x in load_orders() if x.id == order_id), None)
    if not order:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    tpl = templates_env.get_template("purchases_order_detail.html")
    return HTMLResponse(tpl.render(request=request, order=order))


@router.post("/orders/{order_id}/bill")
async def order_to_bill(order_id: str, tax_rate: float = Form(0.0)):
    orders = load_orders()
    order = next((x for x in orders if x.id == order_id), None)
    if not order:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    subtotal = sum(it.line_total() for it in order.items)
    vat = round(subtotal * float(tax_rate), 2)
    total = round(subtotal + vat, 2)
    bill = PurchaseBill(
        id=str(uuid4()),
        order_id=order.id,
        vendor=order.vendor,
        date=datetime.utcnow().isoformat(timespec="seconds") + "Z",
        items=order.items,
        status="open",
        subtotal=subtotal,
        tax_rate=float(tax_rate),
        total=total,
    )
    bills = load_bills()
    bills.append(bill)
    save_bills(bills)
    # Post to GL
    post_purchase_bill_to_gl(bill.model_dump())
    # Record stock-in in Inventory
    try:
        record_purchase_receipt(bill.model_dump())
    except Exception:
        pass
    # Update order status
    for i, x in enumerate(orders):
        if x.id == order_id:
            orders[i].status = "billed"
            break
    save_orders(orders)
    return RedirectResponse(url=f"/purchases/bills/{bill.id}", status_code=303)


@router.get("/bills", response_class=HTMLResponse)
async def bills_list(request: Request):
    bills = load_bills()
    orders = load_orders()
    # Counts for sidebar badges
    open_po_count = sum(1 for o in orders if o.status == "confirmed")
    unpaid_bills_count = sum(1 for b in bills if b.status == "open")
    tpl = templates_env.get_template("purchases_bills.html")
    return HTMLResponse(
        tpl.render(
            request=request,
            bills=bills,
            open_po_count=open_po_count,
            unpaid_bills_count=unpaid_bills_count,
        )
    )


@router.get("/payments", response_class=HTMLResponse)
async def payments_list(request: Request):
    payments = load_payments()
    # Counts for sidebar badges (reuse Purchases counts for consistency)
    orders = load_orders()
    bills = load_bills()
    open_po_count = sum(1 for o in orders if o.status == "confirmed")
    unpaid_bills_count = sum(1 for b in bills if b.status == "open")
    tpl = templates_env.get_template("purchases_payments.html")
    return HTMLResponse(
        tpl.render(
            request=request,
            payments=payments,
            open_po_count=open_po_count,
            unpaid_bills_count=unpaid_bills_count,
        )
    )


@router.get("/bills/{bill_id}", response_class=HTMLResponse)
async def bill_detail(request: Request, bill_id: str):
    bill = next((x for x in load_bills() if x.id == bill_id), None)
    if not bill:
        raise HTTPException(status_code=404, detail="Bill not found")
    # Load bank accounts for payment selection
    accounts_file = Path("backend/data/bank_accounts.json")
    bank_accounts = []
    try:
        if accounts_file.exists():
            bank_accounts = json.loads(accounts_file.read_text(encoding="utf-8"))
    except Exception:
        bank_accounts = []
    tpl = templates_env.get_template("purchases_bill_detail.html")
    return HTMLResponse(tpl.render(request=request, bill=bill, accounts=bank_accounts))


@router.post("/bills/{bill_id}/pay")
async def bill_pay(bill_id: str, method: str = Form("cash"), bank_account_id: str = Form("")):
    bills = load_bills()
    bill = next((x for x in bills if x.id == bill_id), None)
    if not bill:
        raise HTTPException(status_code=404, detail="Bill not found")
    payment = PurchasePayment(
        id=str(uuid4()),
        bill_id=bill.id,
        vendor=bill.vendor,
        date=datetime.utcnow().isoformat(timespec="seconds") + "Z",
        amount=bill.total,
        method=method,
        bank_account_id=(bank_account_id or None),
    )
    pays = load_payments()
    pays.append(payment)
    save_payments(pays)
    # mark bill as paid
    for i, x in enumerate(bills):
        if x.id == bill_id:
            bills[i].status = "paid"
            break
    save_bills(bills)
    # Post to GL
    post_purchase_payment_to_gl(payment.model_dump())
    return RedirectResponse(url=f"/purchases/payments/{payment.id}", status_code=303)


@router.get("/payments/{payment_id}", response_class=HTMLResponse)
async def payment_detail(request: Request, payment_id: str):
    p = next((x for x in load_payments() if x.id == payment_id), None)
    if not p:
        raise HTTPException(status_code=404, detail="Payment not found")
    # Load bank accounts for display
    accounts_file = Path("backend/data/bank_accounts.json")
    bank_accounts = []
    try:
        if accounts_file.exists():
            bank_accounts = json.loads(accounts_file.read_text(encoding="utf-8"))
    except Exception:
        bank_accounts = []
    accounts_map = {a.get("id"): a.get("name") for a in bank_accounts}
    tpl = templates_env.get_template("purchases_payment_detail.html")
    return HTMLResponse(tpl.render(request=request, payment=p, accounts_map=accounts_map))