from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, Response
from starlette.responses import RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
try:
    # Optional DB-backed products for dropdowns
    from ..db import SessionLocal, Product
except Exception:
    SessionLocal = None
    Product = None


# Jinja environment for Inventory templates
templates_env = Environment(
    loader=FileSystemLoader("backend/templates"),
    autoescape=select_autoescape(["html", "xml"]),
)


# Simple JSON storage for Inventory
DATA_DIR = Path("backend/data")
STOCK_MOVES_FILE = DATA_DIR / "stock_moves.json"
WAREHOUSES_FILE = DATA_DIR / "warehouses.json"
LOCATIONS_FILE = DATA_DIR / "locations.json"
STOCK_TRANSFERS_FILE = DATA_DIR / "stock_transfers.json"
DATA_DIR.mkdir(parents=True, exist_ok=True)
if not STOCK_MOVES_FILE.exists():
    STOCK_MOVES_FILE.write_text("[]", encoding="utf-8")
for f in [WAREHOUSES_FILE, LOCATIONS_FILE, STOCK_TRANSFERS_FILE]:
    if not f.exists():
        f.write_text("[]", encoding="utf-8")


def _load_json(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def load_moves() -> list[dict]:
    return _load_json(STOCK_MOVES_FILE)


def save_moves(moves: list[dict]) -> None:
    STOCK_MOVES_FILE.write_text(json.dumps(moves, ensure_ascii=False, indent=2), encoding="utf-8")


def load_warehouses() -> list[dict]:
    return _load_json(WAREHOUSES_FILE)


def load_locations() -> list[dict]:
    return _load_json(LOCATIONS_FILE)


def load_transfers() -> list[dict]:
    return _load_json(STOCK_TRANSFERS_FILE)


def save_transfers(transfers: list[dict]) -> None:
    STOCK_TRANSFERS_FILE.write_text(json.dumps(transfers, ensure_ascii=False, indent=2), encoding="utf-8")


def load_products() -> list[dict]:
    """Load products from the database if available, else return empty list."""
    results: list[dict] = []
    if SessionLocal and Product:
        try:
            with SessionLocal() as db:
                rows = db.query(Product).order_by(Product.id.desc()).all()
                results = [{"id": getattr(r, "id", None), "name": getattr(r, "name", "")} for r in rows]
        except Exception:
            results = []
    return results


def record_move(product: str, quantity: float, unit_cost: float, mtype: str, ref: str, memo: str = "", warehouse: str = "Main", location: str = "") -> dict:
    move = {
        "id": str(uuid4()),
        "date": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "product": product,
        "quantity": float(quantity),
        "unit_cost": float(unit_cost),
        "type": mtype,  # in, out
        "ref": ref,
        "memo": memo,
        "warehouse": warehouse,
        "location": location,
    }
    moves = load_moves()
    moves.append(move)
    save_moves(moves)
    return move


def compute_on_hand() -> dict[str, dict]:
    """Compute on-hand qty and average cost per product."""
    moves = load_moves()
    summary: dict[str, dict] = {}
    for m in moves:
        p = m.get("product")
        qty = float(m.get("quantity", 0) or 0)
        cost = float(m.get("unit_cost", 0) or 0)
        if p not in summary:
            summary[p] = {"qty": 0.0, "value": 0.0}
        if m.get("type") == "in":
            summary[p]["qty"] += qty
            summary[p]["value"] += qty * cost
        elif m.get("type") == "out":
            # For outs, reduce qty and value by the recorded cost
            summary[p]["qty"] -= qty
            summary[p]["value"] -= qty * cost
    for p, s in summary.items():
        qty = s["qty"]
        value = s["value"]
        avg = (value / qty) if qty else 0.0
        s["avg_cost"] = round(avg, 2)
        s["qty"] = round(qty, 2)
        s["value"] = round(value, 2)
    return summary


def compute_on_hand_site(warehouse: str | None = None, location: str | None = None) -> dict[str, dict]:
    """Compute on-hand and average cost per product filtered by warehouse/location.
    If neither filter is provided, falls back to global aggregation.
    """
    moves = load_moves()
    summary: dict[str, dict] = {}
    for m in moves:
        if warehouse is not None and m.get("warehouse") != warehouse:
            continue
        if location is not None and m.get("location") != location:
            continue
        p = m.get("product")
        qty = float(m.get("quantity", 0) or 0)
        cost = float(m.get("unit_cost", 0) or 0)
        if p not in summary:
            summary[p] = {"qty": 0.0, "value": 0.0}
        if m.get("type") == "in":
            summary[p]["qty"] += qty
            summary[p]["value"] += qty * cost
        elif m.get("type") == "out":
            summary[p]["qty"] -= qty
            summary[p]["value"] -= qty * cost
    for p, s in summary.items():
        qty = s["qty"]
        value = s["value"]
        avg = (value / qty) if qty else 0.0
        s["avg_cost"] = round(avg, 2)
        s["qty"] = round(qty, 2)
        s["value"] = round(value, 2)
    return summary


def get_avg_cost(product: str, warehouse: str | None = None, location: str | None = None) -> float:
    """Get average cost for a product optionally filtered by warehouse/location.
    Falls back to global average if site-specific average is not available.
    """
    site = compute_on_hand_site(warehouse=warehouse, location=location)
    avg = site.get(product, {}).get("avg_cost")
    if avg is None:
        avg = compute_on_hand().get(product, {}).get("avg_cost", 0.0)
    return float(avg or 0.0)


def record_transfer(product: str, quantity: float, from_wh: str, from_loc: str, to_wh: str, to_loc: str, memo: str = "") -> dict:
    """Record a stock transfer between warehouses/locations as an out and in move."""
    # Use site-specific average cost from source if available
    avg_cost = get_avg_cost(product, warehouse=from_wh, location=from_loc)
    transfer_id = str(uuid4())
    ref = f"XFER-{transfer_id}"
    record_move(product=product, quantity=quantity, unit_cost=avg_cost, mtype="out", ref=ref, memo=memo, warehouse=from_wh, location=from_loc)
    record_move(product=product, quantity=quantity, unit_cost=avg_cost, mtype="in", ref=ref, memo=memo, warehouse=to_wh, location=to_loc)
    transfers = load_transfers()
    transfers.append({
        "id": transfer_id,
        "date": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "product": product,
        "quantity": float(quantity),
        "from_warehouse": from_wh,
        "from_location": from_loc,
        "to_warehouse": to_wh,
        "to_location": to_loc,
        "unit_cost": avg_cost,
    })
    save_transfers(transfers)
    return {"id": transfer_id}


# Integration helpers
def record_purchase_receipt(bill: dict):
    """Record stock-in moves for each item on a purchase bill."""
    for it in bill.get("items", []):
        record_move(product=it.get("product"), quantity=float(it.get("quantity", 0)), unit_cost=float(it.get("unit_cost", 0)), mtype="in", ref=f"BILL-{bill.get('id')}")


def record_sales_delivery(delivery: dict):
    """Record stock-out moves for each item on a delivery note using current average cost."""
    on_hand = compute_on_hand()
    for it in delivery.get("items", []):
        p = it.get("product")
        qty = float(it.get("quantity", 0))
        avg_cost = on_hand.get(p, {}).get("avg_cost", 0.0)
        record_move(product=p, quantity=qty, unit_cost=avg_cost, mtype="out", ref=f"DEL-{delivery.get('id')}")


router = APIRouter(prefix="/inventory", tags=["Inventory"])


@router.get("/", response_class=HTMLResponse)
async def inventory_home():
    return RedirectResponse(url="/inventory/items", status_code=303)


@router.get("/items", response_class=HTMLResponse)
async def items_list(request: Request, warehouse: str | None = None, location: str | None = None):
    # If filters are provided, compute per-site summary; otherwise global
    if warehouse or location:
        summary = compute_on_hand_site(warehouse=warehouse, location=location)
    else:
        summary = compute_on_hand()
    items = [
        {"product": p, "qty": s.get("qty", 0.0), "avg_cost": s.get("avg_cost", 0.0), "value": s.get("value", 0.0)}
        for p, s in sorted(summary.items())
    ]
    warehouses = load_warehouses()
    locations = load_locations()
    tpl = templates_env.get_template("inventory_items.html")
    return HTMLResponse(tpl.render(request=request, items=items, warehouses=warehouses, locations=locations, selected_warehouse=warehouse or "", selected_location=location or ""))


@router.get("/moves")
async def moves_list(
    request: Request,
    ref: str | None = None,
    warehouse: str | None = None,
    location: str | None = None,
    type: str | None = None,
    memo: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    sort: str | None = None,
    order: str | None = None,
    page: int = 1,
    per_page: int = 50,
    format: str | None = None,
):
    moves = load_moves()
    # Filters
    if ref:
        moves = [m for m in moves if str(m.get("ref", "")) == ref]
    if warehouse:
        moves = [m for m in moves if m.get("warehouse") == warehouse]
    if location:
        moves = [m for m in moves if m.get("location") == location]
    if type in {"in", "out"}:
        moves = [m for m in moves if m.get("type") == type]
    if memo:
        q = memo.lower()
        moves = [m for m in moves if q in str(m.get("memo", "")).lower()]
    # Date range filter (expects YYYY-MM-DD)
    def _to_date(val: str | None):
        if not val:
            return None
        try:
            return datetime.fromisoformat(val).date()
        except Exception:
            return None
    sd = _to_date(start_date)
    ed = _to_date(end_date)
    if sd or ed:
        filtered: list[dict] = []
        for m in moves:
            md = None
            try:
                dstr = str(m.get("date", ""))
                # support Z suffix
                if dstr.endswith("Z"):
                    dstr = dstr[:-1]
                md = datetime.fromisoformat(dstr).date()
            except Exception:
                md = None
            if md is None:
                continue
            if sd and md < sd:
                continue
            if ed and md > ed:
                continue
            filtered.append(m)
        moves = filtered

    # Sorting (date, product, type)
    allowed_sorts = {"date", "product", "type"}
    if sort in allowed_sorts:
        reverse = (order or "asc").lower() == "desc"
        if sort == "date":
            def _parse_dt(m):
                try:
                    dstr = str(m.get("date", ""))
                    if dstr.endswith("Z"):
                        dstr = dstr[:-1]
                    return datetime.fromisoformat(dstr)
                except Exception:
                    return datetime.min
            moves = sorted(moves, key=_parse_dt, reverse=reverse)
        else:
            moves = sorted(moves, key=lambda m: str(m.get(sort, "")), reverse=reverse)

    # Summary totals over filtered set
    total_qty_in = sum(float(m.get("quantity", 0) or 0) for m in moves if m.get("type") == "in")
    total_qty_out = sum(float(m.get("quantity", 0) or 0) for m in moves if m.get("type") == "out")
    total_val_in = sum(float(m.get("quantity", 0) or 0) * float(m.get("unit_cost", 0) or 0) for m in moves if m.get("type") == "in")
    total_val_out = sum(float(m.get("quantity", 0) or 0) * float(m.get("unit_cost", 0) or 0) for m in moves if m.get("type") == "out")
    totals = {
        "qty_in": total_qty_in,
        "qty_out": total_qty_out,
        "qty_net": total_qty_in - total_qty_out,
        "val_in": total_val_in,
        "val_out": total_val_out,
        "val_net": total_val_in - total_val_out,
    }

    total_count = len(moves)
    # Basic pagination
    if per_page <= 0:
        per_page = 50
    if page <= 0:
        page = 1
    total_pages = (total_count + per_page - 1) // per_page if per_page else 1
    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    paged_moves = moves[start_idx:end_idx]

    # CSV export
    if (format or "").lower() == "csv":
        # generate CSV over the filtered (non-paged) set unless explicitly paged
        rows = [
            [
                m.get("id", ""),
                m.get("date", ""),
                m.get("type", ""),
                m.get("product", ""),
                str(m.get("quantity", "")),
                str(m.get("unit_cost", "")),
                m.get("ref", ""),
                m.get("memo", ""),
                m.get("warehouse", ""),
                m.get("location", ""),
            ]
            for m in moves
        ]
        header = ["id", "date", "type", "product", "quantity", "unit_cost", "ref", "memo", "warehouse", "location"]
        def _csv_escape(val: str) -> str:
            if any(ch in val for ch in [",", "\n", '"']):
                return '"' + val.replace('"', '""') + '"'
            return val
        lines = [",".join(header)] + [",".join(_csv_escape(str(v)) for v in row) for row in rows]
        csv_text = "\n".join(lines)
        return Response(content=csv_text, media_type="text/csv")

    # JSON export (filtered, non-paged)
    if (format or "").lower() == "json":
        return Response(content=json.dumps(moves, ensure_ascii=False, indent=2), media_type="application/json")

    # Print-friendly view (filtered, non-paged)
    if (format or "").lower() == "print":
        warehouses = load_warehouses()
        locations = load_locations()
        tpl = templates_env.get_template("inventory_moves_print.html")
        return HTMLResponse(
            tpl.render(
                request=request,
                moves=moves,
                selected_ref=ref or "",
                selected_warehouse=warehouse or "",
                selected_location=location or "",
                selected_type=type or "",
                selected_start_date=start_date or "",
                selected_end_date=end_date or "",
                selected_memo=memo or "",
                selected_sort=sort or "",
                selected_order=(order or "asc").lower(),
                warehouses=warehouses,
                locations=locations,
                total_count=len(moves),
            )
        )

    warehouses = load_warehouses()
    locations = load_locations()
    tpl = templates_env.get_template("inventory_moves.html")
    return HTMLResponse(
        tpl.render(
            request=request,
            moves=paged_moves,
            selected_ref=ref or "",
            selected_warehouse=warehouse or "",
            selected_location=location or "",
            selected_type=type or "",
            selected_memo=memo or "",
            selected_start_date=start_date or "",
            selected_end_date=end_date or "",
            selected_sort=sort or "",
            selected_order=(order or "asc").lower(),
            warehouses=warehouses,
            locations=locations,
            total_count=total_count,
            page=page,
            per_page=per_page,
            total_pages=total_pages,
            totals=totals,
        )
    )


@router.get("/receive", response_class=HTMLResponse)
async def receive_form(request: Request):
    warehouses = load_warehouses()
    locations = load_locations()
    products = load_products()
    tpl = templates_env.get_template("inventory_receive_new.html")
    return HTMLResponse(tpl.render(request=request, warehouses=warehouses, locations=locations, products=products))


@router.post("/receive")
async def receive_create(product: str = Form(...), quantity: float = Form(...), unit_cost: float = Form(0.0), warehouse: str = Form("Main"), location: str = Form(""), memo: str = Form("")):
    record_move(product=product, quantity=quantity, unit_cost=unit_cost, mtype="in", ref="MANUAL", memo=memo, warehouse=warehouse, location=location)
    return RedirectResponse(url="/inventory/items", status_code=303)


@router.get("/issue", response_class=HTMLResponse)
async def issue_form(request: Request):
    warehouses = load_warehouses()
    locations = load_locations()
    products = load_products()
    tpl = templates_env.get_template("inventory_issue_new.html")
    return HTMLResponse(tpl.render(request=request, warehouses=warehouses, locations=locations, products=products))


@router.post("/issue")
async def issue_create(product: str = Form(...), quantity: float = Form(...), warehouse: str = Form("Main"), location: str = Form(""), memo: str = Form("")):
    # Use current avg cost
    avg_cost = get_avg_cost(product, warehouse=warehouse, location=location)
    record_move(product=product, quantity=quantity, unit_cost=avg_cost, mtype="out", ref="MANUAL", memo=memo, warehouse=warehouse, location=location)
    return RedirectResponse(url="/inventory/items", status_code=303)


@router.get("/transfers", response_class=HTMLResponse)
async def transfers_list(request: Request):
    transfers = load_transfers()
    tpl = templates_env.get_template("inventory_transfers.html")
    return HTMLResponse(tpl.render(request=request, transfers=transfers))


@router.get("/transfers/{transfer_id}", response_class=HTMLResponse)
async def transfer_detail(request: Request, transfer_id: str):
    """Show details for a single stock transfer, including related moves."""
    transfers = load_transfers()
    transfer = next((t for t in transfers if t.get("id") == transfer_id), None)
    if not transfer:
        return HTMLResponse("<h1>Transfer not found</h1>", status_code=404)

    # Find the associated in/out moves by XFER reference
    ref = f"XFER-{transfer_id}"
    moves = [m for m in load_moves() if m.get("ref") == ref]

    tpl = templates_env.get_template("inventory_transfer_detail.html")
    return HTMLResponse(tpl.render(request=request, transfer=transfer, moves=moves))


@router.get("/transfers/new", response_class=HTMLResponse)
async def transfers_new(request: Request):
    warehouses = load_warehouses()
    locations = load_locations()
    products = load_products()
    tpl = templates_env.get_template("inventory_transfer_new.html")
    return HTMLResponse(tpl.render(request=request, warehouses=warehouses, locations=locations, products=products))


@router.post("/transfers")
async def transfers_create(
    product: str = Form(...),
    quantity: float = Form(...),
    from_warehouse: str = Form("Main"),
    from_location: str = Form(""),
    to_warehouse: str = Form("Main"),
    to_location: str = Form(""),
    memo: str = Form(""),
):
    record_transfer(product=product, quantity=quantity, from_wh=from_warehouse, from_loc=from_location, to_wh=to_warehouse, to_loc=to_location, memo=memo)
    return RedirectResponse(url="/inventory/transfers", status_code=303)