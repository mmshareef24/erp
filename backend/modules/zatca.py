from __future__ import annotations

import json
from base64 import b64encode
from pathlib import Path
from datetime import datetime
from fastapi import APIRouter
from fastapi.responses import JSONResponse, HTMLResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from .sales import load_invoices


templates_env = Environment(
    loader=FileSystemLoader("backend/templates"),
    autoescape=select_autoescape(["html", "xml"]),
)

router = APIRouter(prefix="/zatca", tags=["ZATCA"])


DATA_DIR = Path("backend/data")
COMPANY_FILE = DATA_DIR / "company.json"
DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_company() -> dict:
    """Load company settings; create defaults if missing."""
    if not COMPANY_FILE.exists():
        COMPANY_FILE.write_text(json.dumps({
    "name": "Matrix ERP Demo Co",
            "vat_number": "310000000000000",
            "country_code": "SA",
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        return json.loads(COMPANY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"name": "", "vat_number": "", "country_code": ""}


def tlv(tag: int, value: str) -> bytes:
    vbytes = value.encode("utf-8")
    return bytes([tag]) + bytes([len(vbytes)]) + vbytes


def zatca_qr_payload(seller_name: str, vat_number: str, timestamp_iso: str, total: float, vat_total: float) -> str:
    """Generate ZATCA Phase 1 TLV Base64 payload for QR.
    Tags: 1=Seller Name, 2=VAT Number, 3=Timestamp, 4=Total (with VAT), 5=VAT Total
    """
    parts = [
        tlv(1, seller_name or ""),
        tlv(2, vat_number or ""),
        tlv(3, timestamp_iso or ""),
        tlv(4, f"{float(total):.2f}"),
        tlv(5, f"{float(vat_total):.2f}"),
    ]
    return b64encode(b"".join(parts)).decode("ascii")


@router.get("/invoices/{invoice_id}/qr")
async def invoice_qr_payload(invoice_id: str):
    inv = next((i for i in load_invoices() if i.id == invoice_id), None)
    if not inv:
        return JSONResponse({"error": "Invoice not found"}, status_code=404)
    company = load_company()
    seller = company.get("name") or ""
    vat = company.get("vat_number") or ""
    ts = inv.date or datetime.utcnow().isoformat(timespec="seconds") + "Z"
    total = float(inv.total or 0.0)
    vat_total = float(inv.subtotal or 0.0) * float(inv.tax_rate or 0.0)
    payload = zatca_qr_payload(seller, vat, ts, total, vat_total)
    return JSONResponse({
        "payload": payload,
        "seller_name": seller,
        "vat_number": vat,
        "timestamp": ts,
        "total": total,
        "vat_total": vat_total,
    })


@router.get("/invoices/{invoice_id}", response_class=HTMLResponse)
async def invoice_qr_view(invoice_id: str):
    inv = next((i for i in load_invoices() if i.id == invoice_id), None)
    if not inv:
        return HTMLResponse("Invoice not found", status_code=404)
    tpl = templates_env.get_template("zatca_invoice_qr.html")
    return HTMLResponse(tpl.render(invoice=inv))