import json
import os
from datetime import datetime
from typing import List, Dict, Optional

from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates


DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
CHART_FILE = os.path.join(DATA_DIR, "chart_of_accounts.json")
JOURNAL_FILE = os.path.join(DATA_DIR, "journal_entries.json")
BUDGETS_FILE = os.path.join(DATA_DIR, "budgets.json")
TAX_SETTINGS_FILE = os.path.join(DATA_DIR, "tax_settings.json")
TAX_FILINGS_FILE = os.path.join(DATA_DIR, "tax_filings.json")
STOCK_MOVES_FILE = os.path.join(DATA_DIR, "stock_moves.json")

templates = Jinja2Templates(directory=os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates"))


def ensure_finance_storage():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(CHART_FILE):
        # Seed a minimal chart of accounts
        chart = [
            {"code": "1000", "name": "Cash", "type": "asset"},
            {"code": "1010", "name": "Bank", "type": "asset"},
            {"code": "1100", "name": "Accounts Receivable", "type": "asset"},
            {"code": "1200", "name": "Inventory", "type": "asset"},
            {"code": "2000", "name": "Accounts Payable", "type": "liability"},
            {"code": "2100", "name": "VAT Payable", "type": "liability"},
            {"code": "4000", "name": "Sales Revenue", "type": "income"},
            {"code": "5000", "name": "Cost of Goods Sold", "type": "expense"},
        ]
        with open(CHART_FILE, "w", encoding="utf-8") as f:
            json.dump(chart, f, indent=2)
    if not os.path.exists(JOURNAL_FILE):
        with open(JOURNAL_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, indent=2)
    if not os.path.exists(BUDGETS_FILE):
        with open(BUDGETS_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, indent=2)
    if not os.path.exists(TAX_SETTINGS_FILE):
        with open(TAX_SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump({"country_code": "", "gst_rate": None, "vat_rate": None, "tds_rates": {}, "gstin": "", "vat_number": "", "tds_tan": ""}, f, indent=2)
    if not os.path.exists(TAX_FILINGS_FILE):
        with open(TAX_FILINGS_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, indent=2)


def load_chart() -> List[Dict]:
    ensure_finance_storage()
    with open(CHART_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_chart(chart: List[Dict]):
    with open(CHART_FILE, "w", encoding="utf-8") as f:
        json.dump(chart, f, indent=2)


def load_journals() -> List[Dict]:
    ensure_finance_storage()
    with open(JOURNAL_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_journals(entries: List[Dict]):
    with open(JOURNAL_FILE, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)


def ensure_account(code: str, name: str, atype: str) -> str:
    """Ensure an account exists in the chart; create if missing."""
    chart = load_chart()
    if not any(a.get("code") == code for a in chart):
        chart.append({"code": code, "name": name, "type": atype})
        chart = sorted(chart, key=lambda a: a.get("code", ""))
        save_chart(chart)
    return code


def _load_stock_moves() -> List[Dict]:
    try:
        if not os.path.exists(STOCK_MOVES_FILE):
            return []
        with open(STOCK_MOVES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


# --- Budgeting storage helpers ---
def load_budgets() -> List[Dict]:
    ensure_finance_storage()
    with open(BUDGETS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_budgets(rows: List[Dict]):
    with open(BUDGETS_FILE, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)


# --- Tax storage helpers ---
def load_tax_settings() -> Dict:
    ensure_finance_storage()
    with open(TAX_SETTINGS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_tax_settings(s: Dict):
    with open(TAX_SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2)


def load_tax_filings() -> List[Dict]:
    ensure_finance_storage()
    with open(TAX_FILINGS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_tax_filings(rows: List[Dict]):
    with open(TAX_FILINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)


def append_journal_entry(date: str, ref: str, memo: str, lines: List[Dict]) -> Dict:
    """
    Append a balanced journal entry. Each line is: {account_code, debit, credit, memo?}
    """
    # Validate balance
    total_debit = sum(float(l.get("debit", 0) or 0) for l in lines)
    total_credit = sum(float(l.get("credit", 0) or 0) for l in lines)
    if round(total_debit, 2) != round(total_credit, 2):
        raise ValueError("Journal entry is not balanced: debit != credit")

    entries = load_journals()
    entry = {
        "date": date,
        "ref": ref,
        "memo": memo,
        "lines": lines,
        "posted_at": datetime.utcnow().isoformat(),
    }
    entries.append(entry)
    save_journals(entries)
    return entry


def account_by_code(chart: List[Dict], code: str) -> Optional[Dict]:
    for a in chart:
        if a.get("code") == code:
            return a
    return None


def ledger_for_account(code: str) -> List[Dict]:
    entries = load_journals()
    ledger = []
    running = 0.0
    for e in entries:
        for line in e.get("lines", []):
            if line.get("account_code") == code:
                debit = float(line.get("debit", 0) or 0)
                credit = float(line.get("credit", 0) or 0)
                running = running + debit - credit
                ledger.append({
                    "date": e.get("date"),
                    "ref": e.get("ref"),
                    "entry_memo": e.get("memo"),
                    "line_memo": line.get("memo", ""),
                    "debit": debit,
                    "credit": credit,
                    "balance": round(running, 2),
                })
    return ledger


def actual_for_period_account(period: str, account_code: Optional[str]) -> float:
    """Compute actual amount for given YYYY-MM period and optional account_code.
    Sum debit-credit for matching lines within the month. If account_code is None,
    include all expense/income accounts.
    """
    # Parse period
    try:
        year, month = [int(x) for x in period.split("-")[:2]]
    except Exception:
        return 0.0
    entries = load_journals()
    chart = load_chart()
    type_by_code = {a.get("code"): a.get("type") for a in chart}
    total = 0.0
    for e in entries:
        # e.get("date") can be YYYY-MM-DD; match year-month prefix
        date_str = str(e.get("date") or "")
        if not date_str.startswith(f"{year:04d}-{month:02d}"):
            continue
        for l in e.get("lines", []):
            code = l.get("account_code")
            if account_code and code != account_code:
                continue
            if not account_code:
                # only consider income/expense
                if type_by_code.get(code) not in ("income", "expense"):
                    continue
            debit = float(l.get("debit", 0) or 0)
            credit = float(l.get("credit", 0) or 0)
            # For expense, debit increases; for income, credit increases.
            sign = 1.0 if type_by_code.get(code) == "expense" else -1.0
            total += sign * (debit - credit)
    return round(total, 2)


def period_matches(date_iso: str, period: str) -> bool:
    try:
        year, month = [int(x) for x in period.split("-")[:2]]
    except Exception:
        return False
    return str(date_iso or "").startswith(f"{year:04d}-{month:02d}")


def trial_balance() -> List[Dict]:
    chart = load_chart()
    balances: Dict[str, Dict] = {a["code"]: {"code": a["code"], "name": a["name"], "type": a["type"], "debit": 0.0, "credit": 0.0} for a in chart}
    entries = load_journals()
    for e in entries:
        for l in e.get("lines", []):
            code = l.get("account_code")
            debit = float(l.get("debit", 0) or 0)
            credit = float(l.get("credit", 0) or 0)
            acct = balances.get(code)
            if acct:
                acct["debit"] += debit
                acct["credit"] += credit
    # Compute net for display convenience
    tb = []
    for b in balances.values():
        net = round(b["debit"] - b["credit"], 2)
        tb.append({**b, "net": net})
    return tb


def profit_and_loss() -> Dict[str, float]:
    tb = trial_balance()
    income = sum(-row["net"] for row in tb if row["type"] == "income" and row["net"] < 0)
    expense = sum(row["net"] for row in tb if row["type"] == "expense" and row["net"] > 0)
    return {
        "income": round(income, 2),
        "expense": round(expense, 2),
        "net_income": round(income - expense, 2),
    }


def balance_sheet() -> Dict[str, float]:
    tb = trial_balance()
    assets = sum(row["net"] for row in tb if row["type"] == "asset")
    liabilities = sum(-row["net"] for row in tb if row["type"] == "liability" and row["net"] < 0)
    equity = sum(-row["net"] for row in tb if row["type"] == "equity" and row["net"] < 0)
    return {
        "assets": round(assets, 2),
        "liabilities": round(liabilities, 2),
        "equity": round(equity, 2),
    }


def post_invoice_to_gl(invoice: Dict):
    """Post a sales invoice to GL: DR AR, CR Revenue, CR VAT Payable."""
    subtotal = float(invoice.get("subtotal", 0))
    tax_rate = float(invoice.get("tax_rate", 0))
    vat = round(subtotal * tax_rate, 2)
    total = round(subtotal + vat, 2)
    customer = invoice.get("customer")
    ref = f"INV-{invoice.get('id')}"
    date = invoice.get("date") or datetime.utcnow().date().isoformat()
    memo = f"Invoice {invoice.get('id')} for {customer}"
    lines = [
        {"account_code": "1100", "debit": total, "credit": 0.0, "memo": f"AR {customer}"},
        {"account_code": "4000", "debit": 0.0, "credit": subtotal, "memo": "Sales revenue"},
        {"account_code": "2100", "debit": 0.0, "credit": vat, "memo": "VAT payable"},
    ]
    append_journal_entry(date=date, ref=ref, memo=memo, lines=lines)


def post_payment_to_gl(payment: Dict):
    """Post a payment: DR Cash/Bank, CR AR."""
    amount = float(payment.get("amount", 0))
    method = (payment.get("method") or "cash").lower()
    account_code = "1000" if method == "cash" else "1010"
    bank_account_id = payment.get("bank_account_id")
    bank_accounts_file = os.path.join(DATA_DIR, "bank_accounts.json")
    bank_accounts = []
    try:
        if os.path.exists(bank_accounts_file):
            with open(bank_accounts_file, "r", encoding="utf-8") as f:
                bank_accounts = json.load(f)
    except Exception:
        bank_accounts = []
    acct_name_map = {a.get("id"): a.get("name") for a in bank_accounts}
    customer = payment.get("customer")
    invoice_id = payment.get("invoice_id")
    ref = f"PAY-{payment.get('id')}"
    date = payment.get("date") or datetime.utcnow().date().isoformat()
    memo = f"Payment {payment.get('id')} from {customer}"
    method_memo = f"Payment {method}"
    if method == "bank" and bank_account_id:
        method_memo = f"Payment bank • {acct_name_map.get(bank_account_id) or bank_account_id}"
    lines = [
        {"account_code": account_code, "debit": amount, "credit": 0.0, "memo": method_memo},
        {"account_code": "1100", "debit": 0.0, "credit": amount, "memo": f"AR settle INV-{invoice_id}"},
    ]
    append_journal_entry(date=date, ref=ref, memo=memo, lines=lines)


def post_purchase_bill_to_gl(bill: Dict):
    """Post a purchase bill to GL: DR Inventory, DR VAT Payable (decrease), CR AP."""
    subtotal = float(bill.get("subtotal", 0))
    tax_rate = float(bill.get("tax_rate", 0))
    vat = round(subtotal * tax_rate, 2)
    total = round(subtotal + vat, 2)
    vendor = bill.get("vendor")
    ref = f"BILL-{bill.get('id')}"
    date = bill.get("date") or datetime.utcnow().date().isoformat() # no change
    memo = f"Bill {bill.get('id')} from {vendor}"
    # Ensure Inventory account exists
    ensure_account("1200", "Inventory", "asset")
    lines = [
        {"account_code": "1200", "debit": subtotal, "credit": 0.0, "memo": "Inventory receipt"},
        {"account_code": "2100", "debit": vat, "credit": 0.0, "memo": "VAT receivable"},
        {"account_code": "2000", "debit": 0.0, "credit": total, "memo": f"AP {vendor}"},
    ]
    append_journal_entry(date=date, ref=ref, memo=memo, lines=lines)


def post_delivery_to_gl(delivery: Dict):
    """Post a delivery note to GL: DR COGS, CR Inventory using recorded stock moves."""
    # Ensure necessary accounts exist
    ensure_account("1200", "Inventory", "asset")
    ensure_account("5000", "Cost of Goods Sold", "expense")

    ref = f"DEL-{delivery.get('id')}"
    date = delivery.get("date") or datetime.utcnow().date().isoformat()
    customer = delivery.get("customer")
    memo = f"Delivery {delivery.get('id')} to {customer}"

    # Sum COGS from recorded 'out' moves for this delivery
    moves = _load_stock_moves()
    cogs_total = 0.0
    for m in moves:
        try:
            if m.get("ref") == ref and (m.get("type") or "") == "out":
                qty = float(m.get("quantity", 0) or 0)
                unit_cost = float(m.get("unit_cost", 0) or 0)
                cogs_total += qty * unit_cost
        except Exception:
            continue
    cogs_total = round(cogs_total, 2)
    if cogs_total <= 0:
        # Nothing to post
        return

    lines = [
        {"account_code": "5000", "debit": cogs_total, "credit": 0.0, "memo": "COGS from delivery"},
        {"account_code": "1200", "debit": 0.0, "credit": cogs_total, "memo": "Inventory reduction"},
    ]
    append_journal_entry(date=date, ref=ref, memo=memo, lines=lines)


def post_purchase_payment_to_gl(payment: Dict):
    """Post a payment to supplier: DR AP, CR Cash/Bank."""
    amount = float(payment.get("amount", 0))
    method = (payment.get("method") or "cash").lower()
    cash_code = "1000" if method == "cash" else "1010"
    bank_account_id = payment.get("bank_account_id")
    bank_accounts_file = os.path.join(DATA_DIR, "bank_accounts.json")
    bank_accounts = []
    try:
        if os.path.exists(bank_accounts_file):
            with open(bank_accounts_file, "r", encoding="utf-8") as f:
                bank_accounts = json.load(f)
    except Exception:
        bank_accounts = []
    acct_name_map = {a.get("id"): a.get("name") for a in bank_accounts}
    vendor = payment.get("vendor")
    bill_id = payment.get("bill_id")
    ref = f"PPAY-{payment.get('id')}"
    date = payment.get("date") or datetime.utcnow().date().isoformat()
    memo = f"Payment {payment.get('id')} to {vendor}"
    method_memo = f"Payment {method}"
    if method == "bank" and bank_account_id:
        method_memo = f"Payment bank • {acct_name_map.get(bank_account_id) or bank_account_id}"
    lines = [
        {"account_code": "2000", "debit": amount, "credit": 0.0, "memo": f"AP settle BILL-{bill_id}"},
        {"account_code": cash_code, "debit": 0.0, "credit": amount, "memo": method_memo},
    ]
    append_journal_entry(date=date, ref=ref, memo=memo, lines=lines)


router = APIRouter(prefix="/accounting", tags=["Finance"])


@router.get("/coa")
def chart_of_accounts_page(request: Request):
    chart = load_chart()
    return templates.TemplateResponse("finance_coa.html", {"request": request, "chart": chart})


@router.get("/coa/new")
def chart_of_accounts_new(request: Request):
    types = ["asset", "liability", "equity", "income", "expense"]
    return templates.TemplateResponse("finance_coa_new.html", {"request": request, "types": types, "error": None})


@router.post("/coa")
def chart_of_accounts_create(
    request: Request,
    code: str = Form(...),
    name: str = Form(...),
    type: str = Form(...),
    opening_balance_amount: str = Form(""),
    opening_balance_side: Optional[str] = Form(None),
    opening_balance_date: Optional[str] = Form(None),
):
    code = (code or "").strip()
    name = (name or "").strip()
    type = (type or "").strip().lower()
    types = ["asset", "liability", "equity", "income", "expense"]
    if type not in types:
        return templates.TemplateResponse("finance_coa_new.html", {"request": request, "types": types, "error": f"Invalid type '{type}'"})
    chart = load_chart()
    if any(a.get("code") == code for a in chart):
        return templates.TemplateResponse("finance_coa_new.html", {"request": request, "types": types, "error": f"Account code '{code}' already exists"})
    chart.append({"code": code, "name": name, "type": type})
    chart = sorted(chart, key=lambda a: a.get("code", ""))
    save_chart(chart)

    # Optional opening balance journal posting
    try:
        amount_val = float(opening_balance_amount) if (opening_balance_amount or "").strip() != "" else 0.0
    except ValueError:
        amount_val = 0.0
    if amount_val and amount_val > 0:
        side = (opening_balance_side or "").lower()
        if side not in ("debit", "credit"):
            return templates.TemplateResponse(
                "finance_coa_new.html",
                {"request": request, "types": types, "error": "Select debit/credit for opening balance"}
            )
        # Determine offset equity account code
        equity_code = None
        preferred = next((a for a in chart if a.get("code") == "3000"), None)
        if preferred:
            equity_code = preferred["code"]
        else:
            eq = next((a for a in chart if a.get("type") == "equity"), None)
            if eq:
                equity_code = eq["code"]
            else:
                # Create an Opening Balances equity account if none exist
                opening_equity = {"code": "3999", "name": "Opening Balances", "type": "equity"}
                chart.append(opening_equity)
                chart = sorted(chart, key=lambda a: a.get("code", ""))
                save_chart(chart)
                equity_code = opening_equity["code"]

        # Compose balanced opening entry
        as_of = opening_balance_date or datetime.utcnow().date().isoformat()
        ref = f"OPEN-{code}"
        memo = f"Opening balance for {code} {name}"
        if side == "debit":
            lines = [
                {"account_code": code, "debit": amount_val, "credit": 0.0, "memo": "Opening balance"},
                {"account_code": equity_code, "debit": 0.0, "credit": amount_val, "memo": "Offset opening balance"},
            ]
        else:
            lines = [
                {"account_code": code, "debit": 0.0, "credit": amount_val, "memo": "Opening balance"},
                {"account_code": equity_code, "debit": amount_val, "credit": 0.0, "memo": "Offset opening balance"},
            ]
        append_journal_entry(date=as_of, ref=ref, memo=memo, lines=lines)
    return RedirectResponse(url="/accounting/coa", status_code=303)


@router.get("/journals")
def journals_page(request: Request):
    entries = load_journals()
    return templates.TemplateResponse("finance_journal_list.html", {"request": request, "entries": entries})


@router.get("/journals/new")
def journals_new_page(request: Request):
    chart = load_chart()
    return templates.TemplateResponse("finance_journal_new.html", {"request": request, "chart": chart})


@router.post("/journals")
def journals_create(
    request: Request,
    date: str = Form(...),
    ref: str = Form("MANUAL"),
    memo: str = Form("") ,
    account1: str = Form(...), debit1: str = Form("0"), credit1: str = Form("0"), memo1: str = Form("") ,
    account2: str = Form(...), debit2: str = Form("0"), credit2: str = Form("0"), memo2: str = Form("") ,
):
    lines = [
        {"account_code": account1, "debit": float(debit1 or 0), "credit": float(credit1 or 0), "memo": memo1},
        {"account_code": account2, "debit": float(debit2 or 0), "credit": float(credit2 or 0), "memo": memo2},
    ]
    append_journal_entry(date=date, ref=ref, memo=memo, lines=lines)
    return RedirectResponse(url="/accounting/journals", status_code=303)


# --- Budgeting routes ---
@router.get("/budget")
def budget_list(request: Request):
    budgets = load_budgets()
    # Enrich with actuals and variance
    for b in budgets:
        period = b.get("period")
        acct = b.get("account_code") or None
        actual = actual_for_period_account(period, acct)
        b["actual"] = actual
        limit = float(b.get("limit", 0) or 0)
        forecast = float(b.get("forecast", 0) or 0)
        b["variance_vs_limit"] = round(limit - actual, 2)
        b["variance_vs_forecast"] = round(forecast - actual, 2)
    chart = load_chart()
    return templates.TemplateResponse("finance_budget.html", {"request": request, "budgets": budgets, "chart": chart})


@router.get("/budget/new")
def budget_new(request: Request):
    chart = load_chart()
    # limit to income/expense accounts for targeting (optional)
    accounts = [a for a in chart if a.get("type") in ("income", "expense")]
    return templates.TemplateResponse("finance_budget_new.html", {"request": request, "accounts": accounts})


@router.post("/budget")
def budget_create(
    request: Request,
    name: str = Form(...),
    period: str = Form(...),  # YYYY-MM
    account_code: str = Form(""),
    limit: str = Form("0"),
    forecast: str = Form("0"),
):
    budgets = load_budgets()
    row = {
        "id": f"BUD-{len(budgets)+1:05d}",
        "name": (name or "").strip(),
        "period": (period or "").strip(),
        "account_code": (account_code or "").strip() or None,
        "limit": float(limit or 0),
        "forecast": float(forecast or 0),
        "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    budgets.append(row)
    save_budgets(budgets)
    return RedirectResponse(url="/accounting/budget", status_code=303)


# --- Tax Compliance routes ---
@router.get("/tax")
def tax_overview(request: Request, period: str = ""):
    settings = load_tax_settings()
    # default period: current YYYY-MM
    if not period:
        now = datetime.utcnow()
        period = f"{now.year:04d}-{now.month:02d}"
    # Sales tax collected
    sales_tax = 0.0
    try:
        from .sales import load_invoices as sales_load_invoices  # type: ignore
        invoices = sales_load_invoices()
        for inv in invoices:
            if period_matches(inv.date, period):
                sales_tax += float(inv.subtotal or 0.0) * float(inv.tax_rate or 0.0)
    except Exception:
        pass
    # Purchase tax credit
    purchase_tax = 0.0
    try:
        from .purchases import load_bills as purchases_load_bills  # type: ignore
        bills = purchases_load_bills()
        for b in bills:
            if period_matches(b.date, period):
                purchase_tax += float(b.subtotal or 0.0) * float(b.tax_rate or 0.0)
    except Exception:
        pass
    net_vat = round(sales_tax - purchase_tax, 2)
    filings = load_tax_filings()
    period_filings = [f for f in filings if f.get("period") == period]
    return templates.TemplateResponse(
        "finance_tax_overview.html",
        {
            "request": request,
            "settings": settings,
            "period": period,
            "sales_tax": round(sales_tax, 2),
            "purchase_tax": round(purchase_tax, 2),
            "net_vat": net_vat,
            "filings": period_filings,
        },
    )


@router.get("/tax/settings")
def tax_settings_page(request: Request):
    settings = load_tax_settings()
    countries = [
        {"code": "IN", "name": "India", "taxes": ["GST", "TDS"]},
        {"code": "GB", "name": "United Kingdom", "taxes": ["VAT"]},
        {"code": "US", "name": "United States", "taxes": ["Sales Tax"]},
        {"code": "SA", "name": "Saudi Arabia", "taxes": ["VAT"]},
        {"code": "AE", "name": "United Arab Emirates", "taxes": ["VAT"]},
    ]
    return templates.TemplateResponse("finance_tax_settings.html", {"request": request, "settings": settings, "countries": countries})


@router.post("/tax/settings")
def tax_settings_update(
    request: Request,
    country_code: str = Form(""),
    gst_rate: str = Form(""),
    vat_rate: str = Form(""),
    gstin: str = Form(""),
    vat_number: str = Form(""),
    tds_tan: str = Form(""),
):
    s = load_tax_settings()
    s["country_code"] = (country_code or "").upper()
    s["gst_rate"] = float(gstin and (gst_rate or 0) or (gst_rate or 0) or 0) if (gst_rate or "").strip() != "" else None
    s["vat_rate"] = float((vat_rate or 0) or 0) if (vat_rate or "").strip() != "" else None
    s["gstin"] = (gstin or "").strip()
    s["vat_number"] = (vat_number or "").strip()
    s["tds_tan"] = (tds_tan or "").strip()
    # TDS rates can be edited separately (not in this simple form)
    save_tax_settings(s)
    return RedirectResponse(url="/accounting/tax/settings", status_code=303)


@router.get("/tax/filings")
def tax_filings_list(request: Request):
    filings = load_tax_filings()
    return templates.TemplateResponse("finance_tax_filings.html", {"request": request, "filings": filings})


@router.get("/tax/filings/new")
def tax_filings_new(request: Request):
    return templates.TemplateResponse("finance_tax_filing_new.html", {"request": request})


@router.post("/tax/filings")
def tax_filings_create(
    request: Request,
    ftype: str = Form(...),  # gst, vat, tds
    period: str = Form(...),
    ref: str = Form(""),
    status: str = Form("draft"),  # draft, submitted, accepted, rejected
):
    rows = load_tax_filings()
    rows.append({
        "id": f"TAX-{len(rows)+1:05d}",
        "type": (ftype or "").lower(),
        "period": (period or "").strip(),
        "ref": (ref or "").strip(),
        "status": (status or "").strip(),
        "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    })
    save_tax_filings(rows)
    return RedirectResponse(url="/accounting/tax/filings", status_code=303)


# --- GL Account edit/delete helpers and routes ---
def account_in_use(code: str) -> bool:
    entries = load_journals()
    for e in entries:
        for l in e.get("lines", []):
            if l.get("account_code") == code:
                return True
    return False


@router.get("/coa/{code}/edit")
def chart_of_accounts_edit(request: Request, code: str):
    chart = load_chart()
    acct = account_by_code(chart, code)
    if not acct:
        return RedirectResponse(url="/accounting/coa", status_code=303)
    types = ["asset", "liability", "equity", "income", "expense"]
    return templates.TemplateResponse("finance_coa_edit.html", {"request": request, "account": acct, "types": types, "error": None})


@router.post("/coa/{code}/edit")
def chart_of_accounts_update(
    request: Request,
    code: str,
    name: str = Form(...),
    type: str = Form(...),
):
    chart = load_chart()
    acct = account_by_code(chart, code)
    if not acct:
        return RedirectResponse(url="/accounting/coa", status_code=303)
    types = ["asset", "liability", "equity", "income", "expense"]
    type_val = (type or "").strip().lower()
    if type_val not in types:
        return templates.TemplateResponse("finance_coa_edit.html", {"request": request, "account": acct, "types": types, "error": f"Invalid type '{type_val}'"})
    acct["name"] = (name or "").strip()
    acct["type"] = type_val
    save_chart(sorted(chart, key=lambda a: a.get("code", "")))
    return RedirectResponse(url="/accounting/coa", status_code=303)


@router.get("/coa/{code}/delete")
def chart_of_accounts_delete_confirm(request: Request, code: str):
    chart = load_chart()
    acct = account_by_code(chart, code)
    if not acct:
        return RedirectResponse(url="/accounting/coa", status_code=303)
    in_use = account_in_use(code)
    return templates.TemplateResponse("finance_coa_delete.html", {"request": request, "account": acct, "in_use": in_use, "error": None})


@router.post("/coa/{code}/delete")
def chart_of_accounts_delete(request: Request, code: str):
    chart = load_chart()
    acct = account_by_code(chart, code)
    if not acct:
        return RedirectResponse(url="/accounting/coa", status_code=303)
    if account_in_use(code):
        return templates.TemplateResponse("finance_coa_delete.html", {"request": request, "account": acct, "in_use": True, "error": "Account is referenced in journals and cannot be deleted"})
    new_chart = [a for a in chart if a.get("code") != code]
    save_chart(sorted(new_chart, key=lambda a: a.get("code", "")))
    return RedirectResponse(url="/accounting/coa", status_code=303)


@router.get("/ledger/{account_code}")
def ledger_page(request: Request, account_code: str):
    chart = load_chart()
    account = account_by_code(chart, account_code)
    ledger = ledger_for_account(account_code)
    return templates.TemplateResponse("finance_ledger.html", {"request": request, "account": account, "ledger": ledger})


@router.get("/trial-balance")
def trial_balance_page(request: Request):
    tb = trial_balance()
    return templates.TemplateResponse("finance_trial_balance.html", {"request": request, "tb": tb})


@router.get("/pl")
def pl_page(request: Request):
    pl = profit_and_loss()
    return templates.TemplateResponse("finance_pl.html", {"request": request, "pl": pl})


@router.get("/balance-sheet")
def bs_page(request: Request):
    bs = balance_sheet()
    return templates.TemplateResponse("finance_balance_sheet.html", {"request": request, "bs": bs})