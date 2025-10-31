from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse
from starlette.responses import RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape


templates_env = Environment(
    loader=FileSystemLoader("backend/templates"),
    autoescape=select_autoescape(["html", "xml"]),
)

router = APIRouter(prefix="/banking", tags=["Banking"])

DATA_DIR = Path("backend/data")
BANK_ACCOUNTS_FILE = DATA_DIR / "bank_accounts.json"
BANK_TX_FILE = DATA_DIR / "bank_transactions.json"
DATA_DIR.mkdir(parents=True, exist_ok=True)
for f in [BANK_ACCOUNTS_FILE, BANK_TX_FILE]:
    if not f.exists():
        f.write_text("[]", encoding="utf-8")


def _load_json(path: Path) -> list[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8") or "[]")
    except Exception:
        return []


def _save_json(path: Path, data: list[dict]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


@router.get("/accounts", response_class=HTMLResponse)
async def accounts_list(request: Request):
    accounts = _load_json(BANK_ACCOUNTS_FILE)
    tpl = templates_env.get_template("bank_accounts.html")
    return HTMLResponse(tpl.render(request=request, accounts=accounts))


@router.get("/accounts/new", response_class=HTMLResponse)
async def accounts_new(request: Request):
    tpl = templates_env.get_template("bank_account_new.html")
    return HTMLResponse(tpl.render(request=request))


@router.post("/accounts")
async def accounts_create(name: str = Form(...), number: str = Form("")):
    accounts = _load_json(BANK_ACCOUNTS_FILE)
    acct_id = f"BA-{len(accounts)+1:03d}"
    accounts.append({"id": acct_id, "name": name.strip(), "number": (number or "").strip()})
    _save_json(BANK_ACCOUNTS_FILE, accounts)
    return RedirectResponse(url="/banking/accounts", status_code=303)


@router.get("/transactions", response_class=HTMLResponse)
async def transactions_list(request: Request, account_id: str = ""):
    accounts = _load_json(BANK_ACCOUNTS_FILE)
    txs = _load_json(BANK_TX_FILE)
    if account_id:
        txs = [t for t in txs if t.get("account_id") == account_id]
    tpl = templates_env.get_template("bank_transactions.html")
    return HTMLResponse(tpl.render(request=request, accounts=accounts, transactions=txs, selected_account=account_id))


@router.get("/transactions/new", response_class=HTMLResponse)
async def transactions_new(request: Request):
    accounts = _load_json(BANK_ACCOUNTS_FILE)
    tpl = templates_env.get_template("bank_transaction_new.html")
    return HTMLResponse(tpl.render(request=request, accounts=accounts))


@router.post("/transactions")
async def transactions_create(
    account_id: str = Form(...),
    ttype: str = Form("in"),  # in=deposit, out=withdrawal
    amount: float = Form(...),
    memo: str = Form(""),
    date: str = Form(""),
):
    txs = _load_json(BANK_TX_FILE)
    tx_id = f"BT-{len(txs)+1:05d}"
    txs.append({
        "id": tx_id,
        "account_id": account_id,
        "type": ttype,
        "amount": float(amount),
        "memo": memo,
        "date": date or datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "reconciled_ref": None,
    })
    _save_json(BANK_TX_FILE, txs)
    return RedirectResponse(url="/banking/transactions", status_code=303)


# --- Simple reconciliation between bank transactions and recorded payments ---
@router.get("/reconcile", response_class=HTMLResponse)
async def reconcile_list(request: Request, account_id: str = ""):
    accounts = _load_json(BANK_ACCOUNTS_FILE)
    accounts_map = {a.get("id"): a.get("name") for a in accounts}
    txs = _load_json(BANK_TX_FILE)
    # Filter transactions by selected bank account if provided
    if account_id:
        txs = [t for t in txs if t.get("account_id") == account_id]
    sales_payments = _load_json(DATA_DIR / "payments.json")
    purchase_payments = _load_json(DATA_DIR / "purchase_payments.json")
    # Only consider bank method payments
    sales_bank = [p for p in sales_payments if (p.get("method") or "").lower() == "bank"]
    purch_bank = [p for p in purchase_payments if (p.get("method") or "").lower() == "bank"]
    tpl = templates_env.get_template("bank_reconcile.html")
    return HTMLResponse(tpl.render(
        request=request,
        accounts=accounts,
        accounts_map=accounts_map,
        transactions=txs,
        sales_payments=sales_bank,
        purchase_payments=purch_bank,
        selected_account=account_id,
    ))


@router.post("/reconcile")
async def reconcile_apply(tx_id: str = Form(...), ref_type: str = Form(...), ref_id: str = Form(...)):
    txs = _load_json(BANK_TX_FILE)
    for i, t in enumerate(txs):
        if t.get("id") == tx_id:
            txs[i]["reconciled_ref"] = {"type": ref_type, "id": ref_id, "date": datetime.utcnow().isoformat(timespec="seconds") + "Z"}
            break
    _save_json(BANK_TX_FILE, txs)
    return RedirectResponse(url="/banking/reconcile", status_code=303)