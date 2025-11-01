from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Request, HTTPException, Form
from fastapi.responses import HTMLResponse
from starlette.responses import RedirectResponse
from .inventory import record_sales_delivery
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel
from ..db import SessionLocal, Customer, default_vat_rate
from .accounting import append_ar_entry
from .finance import post_invoice_to_gl, post_payment_to_gl, post_delivery_to_gl


# Jinja environment for Sales templates (reuses global templates folder)
templates_env = Environment(
    loader=FileSystemLoader("backend/templates"),
    autoescape=select_autoescape(["html", "xml"]),
)


# Simple JSON storage
DATA_DIR = Path("backend/data")
QUOTES_FILE = DATA_DIR / "quotes.json"
ORDERS_FILE = DATA_DIR / "orders.json"
DELIVERIES_FILE = DATA_DIR / "deliveries.json"
INVOICES_FILE = DATA_DIR / "invoices.json"
PAYMENTS_FILE = DATA_DIR / "payments.json"
DATA_DIR.mkdir(parents=True, exist_ok=True)
for f in [QUOTES_FILE, ORDERS_FILE, DELIVERIES_FILE, INVOICES_FILE, PAYMENTS_FILE]:
    if not f.exists():
        f.write_text("[]", encoding="utf-8")


class QuoteItem(BaseModel):
    product: str
    quantity: float
    unit_price: float

    def line_total(self) -> float:
        return float(self.quantity) * float(self.unit_price)


class Quote(BaseModel):
    id: str
    customer: str
    date: str  # ISO string
    items: list[QuoteItem]
    status: str
    total: float


def load_quotes() -> list[Quote]:
    raw = json.loads(QUOTES_FILE.read_text(encoding="utf-8"))
    return [Quote(**q) for q in raw]


def save_quotes(quotes: list[Quote]) -> None:
    QUOTES_FILE.write_text(
        json.dumps([q.model_dump() for q in quotes], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


class SalesOrder(BaseModel):
    id: str
    quote_id: str
    customer: str
    date: str
    items: list[QuoteItem]
    status: str  # confirmed, delivered, invoiced
    total: float


def load_orders() -> list[SalesOrder]:
    return [SalesOrder(**o) for o in json.loads(ORDERS_FILE.read_text(encoding="utf-8"))]


def save_orders(orders: list[SalesOrder]) -> None:
    ORDERS_FILE.write_text(
        json.dumps([o.model_dump() for o in orders], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


class DeliveryNote(BaseModel):
    id: str
    order_id: str
    customer: str
    date: str
    items: list[QuoteItem]
    status: str  # done


def load_deliveries() -> list[DeliveryNote]:
    return [DeliveryNote(**d) for d in json.loads(DELIVERIES_FILE.read_text(encoding="utf-8"))]


def save_deliveries(deliveries: list[DeliveryNote]) -> None:
    DELIVERIES_FILE.write_text(
        json.dumps([d.model_dump() for d in deliveries], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


class Invoice(BaseModel):
    id: str
    order_id: str
    customer: str
    date: str
    items: list[QuoteItem]
    status: str  # open, paid
    subtotal: float
    tax_rate: float
    total: float


def load_invoices() -> list[Invoice]:
    return [Invoice(**i) for i in json.loads(INVOICES_FILE.read_text(encoding="utf-8"))]


def save_invoices(invoices: list[Invoice]) -> None:
    INVOICES_FILE.write_text(
        json.dumps([i.model_dump() for i in invoices], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


class Payment(BaseModel):
    id: str
    invoice_id: str
    customer: str
    date: str
    amount: float
    method: str
    bank_account_id: str | None = None


def load_payments() -> list[Payment]:
    return [Payment(**p) for p in json.loads(PAYMENTS_FILE.read_text(encoding="utf-8"))]


def save_payments(payments: list[Payment]) -> None:
    PAYMENTS_FILE.write_text(
        json.dumps([p.model_dump() for p in payments], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


router = APIRouter(prefix="/mail", tags=["Mail"])


@router.get("/", response_class=HTMLResponse)
async def sales_home():
    return RedirectResponse(url="/mail/quotes", status_code=303)


@router.get("/quotes", response_class=HTMLResponse)
async def quotes_list(request: Request):
    quotes = load_quotes()
    lead_count = sum(1 for q in quotes if q.status in {"draft", "sent"})
    orders_open_count = sum(1 for o in load_orders() if o.status == "confirmed")
    invoices_open_count = sum(1 for i in load_invoices() if i.status == "open")
    template = templates_env.get_template("sales_quotes.html")
    return HTMLResponse(template.render(
        request=request,
        quotes=quotes,
        lead_count=lead_count,
        orders_open_count=orders_open_count,
        invoices_open_count=invoices_open_count,
    ))

# Leads as pre-opportunities derived from quotes not yet accepted
@router.get("/leads", response_class=HTMLResponse)
async def leads_list(request: Request):
    quotes = load_quotes()
    lead_statuses = {"draft", "sent"}
    # Optional filter via query parameter: status=draft|sent|all
    status = request.query_params.get("status")
    # base leads set (only draft/sent)
    base_leads = [q for q in quotes if q.status in lead_statuses]
    if status in lead_statuses:
        leads = [q for q in base_leads if q.status == status]
    else:
        # default or status=all: show all lead statuses
        leads = base_leads
    template = templates_env.get_template("sales_leads.html")
    lead_count = len(base_leads)
    orders_open_count = sum(1 for o in load_orders() if o.status == "confirmed")
    invoices_open_count = sum(1 for i in load_invoices() if i.status == "open")
    return HTMLResponse(template.render(
        request=request,
        leads=leads,
        lead_count=lead_count,
        orders_open_count=orders_open_count,
        invoices_open_count=invoices_open_count,
    ))


@router.get("/quotes/new", response_class=HTMLResponse)
async def quotes_new_form(request: Request):
    # Load customers for selection
    with SessionLocal() as db:
        customers = db.query(Customer).order_by(Customer.name.asc()).all()
    template = templates_env.get_template("sales_quote_new.html")
    lead_count = sum(1 for q in load_quotes() if q.status in {"draft", "sent"})
    orders_open_count = sum(1 for o in load_orders() if o.status == "confirmed")
    invoices_open_count = sum(1 for i in load_invoices() if i.status == "open")
    return HTMLResponse(template.render(
        request=request,
        customers=customers,
        lead_count=lead_count,
        orders_open_count=orders_open_count,
        invoices_open_count=invoices_open_count,
    ))


@router.post("/quotes")
async def quotes_create(
    customer: str = Form(...),
    product: list[str] = Form(...),
    quantity: list[float] = Form(...),
    unit_price: list[float] = Form(...),
    status: str = Form("draft"),
):
    items: list[QuoteItem] = []
    for i in range(len(product)):
        items.append(QuoteItem(product=product[i], quantity=quantity[i], unit_price=unit_price[i]))
    total = sum(it.line_total() for it in items)
    q = Quote(
        id=str(uuid4()),
        customer=customer,
        date=datetime.utcnow().isoformat(timespec="seconds") + "Z",
        items=items,
        status=status,
        total=total,
    )
    quotes = load_quotes()
    quotes.append(q)
    save_quotes(quotes)
    return RedirectResponse(url=f"/mail/quotes/{q.id}", status_code=303)


@router.get("/quotes/{quote_id}", response_class=HTMLResponse)
async def quote_detail(request: Request, quote_id: str):
    quotes = load_quotes()
    q = next((x for x in quotes if x.id == quote_id), None)
    if not q:
        raise HTTPException(status_code=404, detail="Quote not found")
    template = templates_env.get_template("sales_quote_detail.html")
    lead_count = sum(1 for x in quotes if x.status in {"draft", "sent"})
    orders_open_count = sum(1 for o in load_orders() if o.status == "confirmed")
    invoices_open_count = sum(1 for i in load_invoices() if i.status == "open")
    return HTMLResponse(template.render(
        request=request,
        quote=q,
        lead_count=lead_count,
        orders_open_count=orders_open_count,
        invoices_open_count=invoices_open_count,
    ))


# APIs
@router.get("/api/quotes")
async def api_quotes():
    return [q.model_dump() for q in load_quotes()]


@router.get("/api/quotes/{quote_id}")
async def api_quote(quote_id: str):
    q = next((x for x in load_quotes() if x.id == quote_id), None)
    if not q:
        raise HTTPException(status_code=404, detail="Quote not found")
    return q.model_dump()


# --- Conversions ---
@router.post("/quotes/{quote_id}/confirm")
async def quote_confirm(quote_id: str):
    quotes = load_quotes()
    q = next((x for x in quotes if x.id == quote_id), None)
    if not q:
        raise HTTPException(status_code=404, detail="Quote not found")
    # update quote status
    q.status = "confirmed"
    for i, x in enumerate(quotes):
        if x.id == quote_id:
            quotes[i] = q
            break
    save_quotes(quotes)

    # create order from quote
    order = SalesOrder(
        id=str(uuid4()),
        quote_id=q.id,
        customer=q.customer,
        date=datetime.utcnow().isoformat(timespec="seconds") + "Z",
        items=q.items,
        status="confirmed",
        total=q.total,
    )
    orders = load_orders()
    orders.append(order)
    save_orders(orders)
    return RedirectResponse(url=f"/mail/orders/{order.id}", status_code=303)


@router.get("/orders", response_class=HTMLResponse)
async def orders_list(request: Request):
    orders = load_orders()
    template = templates_env.get_template("sales_orders.html")
    lead_count = sum(1 for q in load_quotes() if q.status in {"draft", "sent"})
    orders_open_count = sum(1 for o in orders if o.status == "confirmed")
    invoices_open_count = sum(1 for i in load_invoices() if i.status == "open")
    return HTMLResponse(template.render(
        request=request,
        orders=orders,
        lead_count=lead_count,
        orders_open_count=orders_open_count,
        invoices_open_count=invoices_open_count,
    ))


@router.get("/orders/{order_id}", response_class=HTMLResponse)
async def order_detail(request: Request, order_id: str):
    order = next((o for o in load_orders() if o.id == order_id), None)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    template = templates_env.get_template("sales_order_detail.html")
    return HTMLResponse(template.render(request=request, order=order))


@router.post("/orders/{order_id}/deliver")
async def order_deliver(order_id: str):
    orders = load_orders()
    order = next((o for o in orders if o.id == order_id), None)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    order.status = "delivered"
    save_orders(orders)

    note = DeliveryNote(
        id=str(uuid4()),
        order_id=order.id,
        customer=order.customer,
        date=datetime.utcnow().isoformat(timespec="seconds") + "Z",
        items=order.items,
        status="done",
    )
    deliveries = load_deliveries()
    deliveries.append(note)
    save_deliveries(deliveries)
    # Record stock-out in Inventory
    try:
        record_sales_delivery(note.model_dump())
    except Exception:
        pass
    # Post COGS to GL (Finance)
    try:
        post_delivery_to_gl(note.model_dump())
    except Exception:
        pass
    return RedirectResponse(url=f"/mail/deliveries/{note.id}", status_code=303)


@router.get("/deliveries/{delivery_id}", response_class=HTMLResponse)
async def delivery_detail(request: Request, delivery_id: str):
    note = next((d for d in load_deliveries() if d.id == delivery_id), None)
    if not note:
        raise HTTPException(status_code=404, detail="Delivery note not found")
    template = templates_env.get_template("sales_delivery_detail.html")
    return HTMLResponse(template.render(request=request, delivery=note))


@router.post("/orders/{order_id}/invoice")
async def order_invoice(order_id: str):
    orders = load_orders()
    order = next((o for o in orders if o.id == order_id), None)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    order.status = "invoiced"
    save_orders(orders)

    subtotal = sum(i.quantity * i.unit_price for i in order.items)
    # VAT based on customer VAT rate or default by country (SA=15%)
    tax_rate = 0.0
    try:
        with SessionLocal() as db:
            cust = db.query(Customer).filter(Customer.name == order.customer).first()
            if cust:
                if cust.vat_rate is not None:
                    tax_rate = float(cust.vat_rate)
                else:
                    tax_rate = default_vat_rate(getattr(cust, "country_code", None))
            else:
                # Fallback to company country defaults (e.g., SA 15%)
                try:
                    from .settings import load_company as load_company_settings
                    company = load_company_settings()
                    tax_rate = default_vat_rate(company.get("country_code"))
                except Exception:
                    tax_rate = 0.0
    except Exception:
        pass
    total = subtotal * (1 + tax_rate)
    invoice = Invoice(
        id=str(uuid4()),
        order_id=order.id,
        customer=order.customer,
        date=datetime.utcnow().isoformat(timespec="seconds") + "Z",
        items=order.items,
        status="open",
        subtotal=subtotal,
        tax_rate=tax_rate,
        total=total,
    )
    invoices = load_invoices()
    invoices.append(invoice)
    save_invoices(invoices)
    # Record AR for invoice
    try:
        with SessionLocal() as db:
            cust = db.query(Customer).filter(Customer.name == invoice.customer).first()
        append_ar_entry({
            "id": str(uuid4()),
            "date": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "type": "invoice",
            "customer": invoice.customer,
            "invoice_id": invoice.id,
            "amount": invoice.total,
            "ar_account": (cust.ar_account if cust and cust.ar_account else "AR"),
        })
    except Exception:
        pass
    # Post to GL (Finance)
    try:
        post_invoice_to_gl(invoice.model_dump())
    except Exception:
        pass
    return RedirectResponse(url=f"/mail/invoices/{invoice.id}", status_code=303)


@router.get("/invoices", response_class=HTMLResponse)
async def invoices_list(request: Request):
    invoices = load_invoices()
    template = templates_env.get_template("sales_invoices.html")
    lead_count = sum(1 for q in load_quotes() if q.status in {"draft", "sent"})
    orders_open_count = sum(1 for o in load_orders() if o.status == "confirmed")
    invoices_open_count = sum(1 for i in invoices if i.status == "open")
    return HTMLResponse(template.render(
        request=request,
        invoices=invoices,
        lead_count=lead_count,
        orders_open_count=orders_open_count,
        invoices_open_count=invoices_open_count,
    ))


@router.get("/invoices/{invoice_id}", response_class=HTMLResponse)
async def invoice_detail(request: Request, invoice_id: str):
    invoice = next((i for i in load_invoices() if i.id == invoice_id), None)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    customer = None
    try:
        with SessionLocal() as db:
            customer = db.query(Customer).filter(Customer.name == invoice.customer).first()
    except Exception:
        customer = None
    # Load bank accounts for payment selection
    accounts_file = Path("backend/data/bank_accounts.json")
    bank_accounts = []
    try:
        if accounts_file.exists():
            bank_accounts = json.loads(accounts_file.read_text(encoding="utf-8"))
    except Exception:
        bank_accounts = []
    template = templates_env.get_template("sales_invoice_detail.html")
    return HTMLResponse(template.render(request=request, invoice=invoice, customer=customer, accounts=bank_accounts))


@router.post("/invoices/{invoice_id}/pay")
async def invoice_pay(invoice_id: str, method: str = Form("cash"), bank_account_id: str = Form("")):
    invoices = load_invoices()
    invoice = next((i for i in invoices if i.id == invoice_id), None)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    invoice.status = "paid"
    save_invoices(invoices)

    payment = Payment(
        id=str(uuid4()),
        invoice_id=invoice.id,
        customer=invoice.customer,
        date=datetime.utcnow().isoformat(timespec="seconds") + "Z",
        amount=invoice.total,
        method=method,
        bank_account_id=(bank_account_id or None),
    )
    payments = load_payments()
    payments.append(payment)
    save_payments(payments)
    # Record AR for payment
    try:
        with SessionLocal() as db:
            cust = db.query(Customer).filter(Customer.name == invoice.customer).first()
        append_ar_entry({
            "id": str(uuid4()),
            "date": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "type": "payment",
            "customer": invoice.customer,
            "invoice_id": invoice.id,
            "amount": payment.amount,
            "method": method,
            "ar_account": (cust.ar_account if cust and cust.ar_account else "AR"),
        })
    except Exception:
        pass
    # Post to GL (Finance)
    try:
        post_payment_to_gl(payment.model_dump())
    except Exception:
        pass
    return RedirectResponse(url=f"/mail/payments/{payment.id}", status_code=303)


@router.get("/payments/{payment_id}", response_class=HTMLResponse)
async def payment_detail(request: Request, payment_id: str):
    payment = next((p for p in load_payments() if p.id == payment_id), None)
    if not payment:
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
    template = templates_env.get_template("sales_payment_detail.html")
    return HTMLResponse(template.render(request=request, payment=payment, accounts_map=accounts_map))