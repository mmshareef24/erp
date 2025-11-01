from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse
from starlette.responses import RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

# Import inventory helpers for stock moves
from .inventory import record_move, compute_on_hand, get_avg_cost
try:
    from ..db import SessionLocal, Product
except Exception:
    SessionLocal = None
    Product = None


templates_env = Environment(
    loader=FileSystemLoader("backend/templates"),
    autoescape=select_autoescape(["html", "xml"]),
)


DATA_DIR = Path("backend/data")
BOMS_FILE = DATA_DIR / "boms.json"
WORK_ORDERS_FILE = DATA_DIR / "work_orders.json"
WAREHOUSES_FILE = DATA_DIR / "warehouses.json"
LOCATIONS_FILE = DATA_DIR / "locations.json"
for f in [BOMS_FILE, WORK_ORDERS_FILE, WAREHOUSES_FILE, LOCATIONS_FILE]:
    if not f.exists():
        f.write_text("[]", encoding="utf-8")


def _load_json(path: Path) -> list[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_json(path: Path, data: list[dict]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_boms() -> list[dict]:
    return _load_json(BOMS_FILE)


def save_boms(boms: list[dict]) -> None:
    _save_json(BOMS_FILE, boms)


def load_work_orders() -> list[dict]:
    return _load_json(WORK_ORDERS_FILE)


def save_work_orders(wos: list[dict]) -> None:
    _save_json(WORK_ORDERS_FILE, wos)


def load_warehouses() -> list[dict]:
    return _load_json(WAREHOUSES_FILE)


def load_locations() -> list[dict]:
    return _load_json(LOCATIONS_FILE)


def load_products() -> list[dict]:
    products: list[dict] = []
    if SessionLocal and Product:
        try:
            with SessionLocal() as db:
                rows = db.query(Product).order_by(Product.id.desc()).all()
                products = [{"id": getattr(r, "id", None), "name": getattr(r, "name", "")} for r in rows]
        except Exception:
            products = []
    return products


router = APIRouter(prefix="/production", tags=["Production"])


@router.get("/", response_class=HTMLResponse)
async def prod_home():
    return RedirectResponse(url="/production/work_orders", status_code=303)


# BOMs
@router.get("/boms", response_class=HTMLResponse)
async def boms_list(request: Request):
    boms = load_boms()
    tpl = templates_env.get_template("production_boms.html")
    return HTMLResponse(tpl.render(request=request, boms=boms))


@router.get("/boms/{product}/requirements", response_class=HTMLResponse)
async def bom_requirements(request: Request, product: str, qty: float = 1.0, warehouse: str | None = None, location: str | None = None):
    """Show material requirements for a product given quantity, with on-hand and shortages."""
    bom = _find_bom(product)
    if not bom:
        return HTMLResponse(f"<h1>No BOM found for {product}</h1>", status_code=404)
    try:
        qty = float(qty)
    except Exception:
        qty = 1.0
    # On-hand summary optionally filtered by site
    from .inventory import compute_on_hand_site, compute_on_hand
    site_summary = compute_on_hand_site(warehouse=warehouse, location=location) if (warehouse or location) else compute_on_hand()
    rows: list[dict] = []
    for comp in bom.get("components", []):
        cp = comp.get("product")
        per_unit = float(comp.get("quantity", 0))
        required = per_unit * qty
        onhand = float(site_summary.get(cp, {}).get("qty", 0.0))
        shortage = max(0.0, required - onhand)
        rows.append({
            "product": cp,
            "per_unit": per_unit,
            "required": round(required, 3),
            "onhand": round(onhand, 3),
            "shortage": round(shortage, 3),
        })
    tpl = templates_env.get_template("production_bom_requirements.html")
    return HTMLResponse(tpl.render(
        request=request,
        bom=bom,
        rows=rows,
        fg_product=product,
        qty=qty,
        selected_warehouse=warehouse or "",
        selected_location=location or "",
    ))


@router.get("/boms/new", response_class=HTMLResponse)
async def boms_new(request: Request):
    products = load_products()
    tpl = templates_env.get_template("production_bom_new.html")
    return HTMLResponse(tpl.render(request=request, products=products))


@router.post("/boms")
async def boms_create(
    product: str = Form(...),
    comp_product: list[str] | None = Form(None),
    comp_qty: list[float] | None = Form(None),
    components_text: str = Form("")
):
    # Prefer array inputs; fallback to parsing textarea lines: "SKU: qty"
    components: list[dict] = []
    if comp_product and comp_qty and len(comp_product) == len(comp_qty):
        for i in range(len(comp_product)):
            sku = (comp_product[i] or "").strip()
            try:
                qty = float(comp_qty[i])
            except Exception:
                qty = 0.0
            if not sku:
                continue
            components.append({"product": sku, "quantity": qty})
    else:
        for line in components_text.splitlines():
            line = line.strip()
            if not line:
                continue
            if ":" in line:
                sku, qty_str = line.split(":", 1)
                try:
                    qty = float(qty_str.strip())
                except Exception:
                    qty = 0.0
                components.append({"product": sku.strip(), "quantity": qty})
            else:
                components.append({"product": line, "quantity": 1.0})
    boms = load_boms()
    # Replace existing BOM for product if any
    boms = [b for b in boms if b.get("product") != product]
    boms.append({"id": str(uuid4()), "product": product, "components": components})
    save_boms(boms)
    return RedirectResponse(url="/production/boms", status_code=303)


def _find_bom(product: str) -> dict | None:
    for b in load_boms():
        if b.get("product") == product:
            return b
    return None


# Work Orders
@router.get("/work_orders", response_class=HTMLResponse)
async def wos_list(request: Request):
    wos = load_work_orders()
    tpl = templates_env.get_template("production_wos.html")
    return HTMLResponse(tpl.render(request=request, wos=wos))


@router.get("/work_orders/new", response_class=HTMLResponse)
async def wos_new(request: Request):
    warehouses = load_warehouses()
    locations = load_locations()
    products = load_products()
    tpl = templates_env.get_template("production_wo_new.html")
    return HTMLResponse(tpl.render(request=request, warehouses=warehouses, locations=locations, products=products))


@router.post("/work_orders")
async def wos_create(
    product: str = Form(...),
    quantity: float = Form(...),
    warehouse: str = Form("Main"),
    location: str = Form(""),
    labor_cost: float = Form(0.0),
    overhead_cost: float = Form(0.0),
    issue_method: str = Form("manual"),  # manual or backflush
    reserve_components: bool = Form(False),
    operations_text: str = Form(""),
):
    wos = load_work_orders()
    wo_id = str(uuid4())
    # Parse operations: one per line: name,minutes,rate
    operations: list[dict] = []
    for line in operations_text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        name = parts[0]
        minutes = float(parts[1]) if len(parts) > 1 else 0.0
        rate = float(parts[2]) if len(parts) > 2 else 0.0
        operations.append({"name": name, "minutes": minutes, "rate": rate})
    # Optional reservations
    reserved: list[dict] = []
    bom = _find_bom(product)
    if reserve_components and bom:
        factor = float(quantity)
        for comp in bom.get("components", []):
            cp = comp.get("product")
            cqty = float(comp.get("quantity", 0)) * factor
            reserved.append({"product": cp, "quantity": cqty})
    wos.append({
        "id": wo_id,
        "date": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "product": product,
        "quantity": float(quantity),
        "warehouse": warehouse,
        "location": location,
        "status": "draft",
        "labor_cost": float(labor_cost),
        "overhead_cost": float(overhead_cost),
        "consumed": [],
        "produced": [],
        "operations": operations,
        "issue_method": issue_method,
        "reserved": reserved,
        "scrap": [],
    })
    save_work_orders(wos)
    return RedirectResponse(url=f"/production/work_orders/{wo_id}", status_code=303)


def _get_wo(wo_id: str) -> dict | None:
    for w in load_work_orders():
        if w.get("id") == wo_id:
            return w
    return None


def _save_wo(updated: dict) -> None:
    wos = load_work_orders()
    wos = [w if w.get("id") != updated.get("id") else updated for w in wos]
    save_work_orders(wos)


@router.get("/work_orders/{wo_id}", response_class=HTMLResponse)
async def wo_detail(request: Request, wo_id: str):
    wo = _get_wo(wo_id)
    if not wo:
        return HTMLResponse("Work Order not found", status_code=404)
    bom = _find_bom(wo.get("product"))
    tpl = templates_env.get_template("production_wo_detail.html")
    return HTMLResponse(tpl.render(request=request, wo=wo, bom=bom))


@router.post("/work_orders/batch")
async def wos_batch(action: str = Form(...), wo_ids: list[str] = Form([]), qty: float = Form(0.0)):
    """Batch operations for Work Orders: start, issue, complete."""
    processed: list[str] = []
    for wo_id in wo_ids:
        wo = _get_wo(wo_id)
        if not wo:
            continue
        bom = _find_bom(wo.get("product"))
        if action == "start":
            if wo.get("status") == "draft" and bom:
                if wo.get("issue_method") == "manual":
                    factor = float(wo.get("quantity", 0))
                    consumed_lines: list[dict] = []
                    for comp in bom.get("components", []):
                        cp = comp.get("product")
                        cqty = float(comp.get("quantity", 0)) * factor
                        avg_cost = get_avg_cost(cp, warehouse=wo.get("warehouse", "Main"), location=wo.get("location", ""))
                        record_move(product=cp, quantity=cqty, unit_cost=avg_cost, mtype="out", ref=f"WO-{wo_id}", warehouse=wo.get("warehouse", "Main"), location=wo.get("location", ""))
                        consumed_lines.append({"product": cp, "quantity": cqty, "unit_cost": avg_cost})
                    wo["consumed"] = (wo.get("consumed", []) or []) + consumed_lines
                wo["status"] = "in_progress"
                _save_wo(wo)
                processed.append(wo_id)
        elif action == "issue":
            try:
                issue_qty = float(qty)
            except Exception:
                issue_qty = 0.0
            if bom and issue_qty > 0:
                consumed_lines: list[dict] = []
                for comp in bom.get("components", []):
                    cp = comp.get("product")
                    cqty = float(comp.get("quantity", 0)) * float(issue_qty)
                    avg_cost = get_avg_cost(cp, warehouse=wo.get("warehouse", "Main"), location=wo.get("location", ""))
                    record_move(product=cp, quantity=cqty, unit_cost=avg_cost, mtype="out", ref=f"WO-{wo_id}", warehouse=wo.get("warehouse", "Main"), location=wo.get("location", ""))
                    consumed_lines.append({"product": cp, "quantity": cqty, "unit_cost": avg_cost})
                wo["consumed"] = (wo.get("consumed", []) or []) + consumed_lines
                if wo.get("status") == "draft":
                    wo["status"] = "in_progress"
                _save_wo(wo)
                processed.append(wo_id)
        elif action == "complete":
            try:
                produce_qty = float(qty) if qty else float(wo.get("quantity", 0))
            except Exception:
                produce_qty = float(wo.get("quantity", 0))
            if wo.get("status") in {"draft", "in_progress"}:
                # Backflush if needed
                if wo.get("issue_method") == "backflush" and not wo.get("consumed") and bom:
                    consumed_lines: list[dict] = []
                    for comp in bom.get("components", []):
                        cp = comp.get("product")
                        cqty = float(comp.get("quantity", 0)) * float(produce_qty)
                        avg_cost = get_avg_cost(cp, warehouse=wo.get("warehouse", "Main"), location=wo.get("location", ""))
                        record_move(product=cp, quantity=cqty, unit_cost=avg_cost, mtype="out", ref=f"WO-{wo_id}", warehouse=wo.get("warehouse", "Main"), location=wo.get("location", ""))
                        consumed_lines.append({"product": cp, "quantity": cqty, "unit_cost": avg_cost})
                    wo["consumed"] = (wo.get("consumed", []) or []) + consumed_lines
                # Material total
                mat_total = 0.0
                if bom:
                    for comp in bom.get("components", []):
                        cp = comp.get("product")
                        cqty = float(comp.get("quantity", 0)) * float(produce_qty)
                        avg_cost = get_avg_cost(cp, warehouse=wo.get("warehouse", "Main"), location=wo.get("location", ""))
                        mat_total += cqty * avg_cost
                extra_base = float(wo.get("labor_cost", 0)) + float(wo.get("overhead_cost", 0))
                ops_total = sum((float(op.get("minutes", 0)) * float(op.get("rate", 0))) for op in (wo.get("operations") or []))
                planned_qty = float(wo.get("quantity", 0))
                ratio = (produce_qty / planned_qty) if planned_qty else 1.0
                extra = (extra_base + ops_total) * ratio
                total_cost = mat_total + extra
                unit_cost = (total_cost / produce_qty) if produce_qty else 0.0
                record_move(product=wo.get("product"), quantity=produce_qty, unit_cost=unit_cost, mtype="in", ref=f"WO-{wo_id}", warehouse=wo.get("warehouse", "Main"), location=wo.get("location", ""))
                produced_lines = (wo.get("produced", []) or [])
                produced_lines.append({"product": wo.get("product"), "quantity": produce_qty, "unit_cost": unit_cost})
                wo["produced"] = produced_lines
                total_produced = sum(float(l.get("quantity", 0)) for l in wo.get("produced", []))
                planned_qty = float(wo.get("quantity", 0))
                wo["status"] = "completed" if total_produced >= planned_qty else "in_progress"
                _save_wo(wo)
                processed.append(wo_id)
    # Basic redirect back to list
    return RedirectResponse(url="/production/work_orders", status_code=303)


@router.post("/work_orders/{wo_id}/start")
async def wo_start(wo_id: str):
    wo = _get_wo(wo_id)
    if not wo:
        return HTMLResponse("Work Order not found", status_code=404)
    if wo.get("status") != "draft":
        return RedirectResponse(url=f"/production/work_orders/{wo_id}", status_code=303)
    bom = _find_bom(wo.get("product"))
    if not bom:
        # No BOM, keep as draft
        return RedirectResponse(url=f"/production/work_orders/{wo_id}", status_code=303)
    # For manual issue method, consume materials now per BOM * planned quantity; for backflush, just mark in progress
    if wo.get("issue_method") == "manual":
        factor = float(wo.get("quantity", 0))
        consumed_lines: list[dict] = []
        for comp in bom.get("components", []):
            cp = comp.get("product")
            cqty = float(comp.get("quantity", 0)) * factor
            avg_cost = get_avg_cost(cp, warehouse=wo.get("warehouse", "Main"), location=wo.get("location", ""))
            record_move(product=cp, quantity=cqty, unit_cost=avg_cost, mtype="out", ref=f"WO-{wo_id}", warehouse=wo.get("warehouse", "Main"), location=wo.get("location", ""))
            consumed_lines.append({"product": cp, "quantity": cqty, "unit_cost": avg_cost})
        wo["consumed"] = (wo.get("consumed", []) or []) + consumed_lines
    wo["status"] = "in_progress"
    _save_wo(wo)
    return RedirectResponse(url=f"/production/work_orders/{wo_id}", status_code=303)


@router.post("/work_orders/{wo_id}/issue")
async def wo_issue_materials(wo_id: str, issue_qty: float = Form(...)):
    """Manually issue materials scaled by the provided quantity (finished goods equivalent)."""
    wo = _get_wo(wo_id)
    if not wo:
        return HTMLResponse("Work Order not found", status_code=404)
    bom = _find_bom(wo.get("product"))
    if not bom:
        return RedirectResponse(url=f"/production/work_orders/{wo_id}", status_code=303)
    consumed_lines: list[dict] = []
    for comp in bom.get("components", []):
        cp = comp.get("product")
        cqty = float(comp.get("quantity", 0)) * float(issue_qty)
        avg_cost = get_avg_cost(cp, warehouse=wo.get("warehouse", "Main"), location=wo.get("location", ""))
        record_move(product=cp, quantity=cqty, unit_cost=avg_cost, mtype="out", ref=f"WO-{wo_id}", warehouse=wo.get("warehouse", "Main"), location=wo.get("location", ""))
        consumed_lines.append({"product": cp, "quantity": cqty, "unit_cost": avg_cost})
    wo["consumed"] = (wo.get("consumed", []) or []) + consumed_lines
    if wo.get("status") == "draft":
        wo["status"] = "in_progress"
    _save_wo(wo)
    return RedirectResponse(url=f"/production/work_orders/{wo_id}", status_code=303)


@router.post("/work_orders/{wo_id}/complete")
async def wo_complete(wo_id: str, produce_qty: float = Form(None), scrap_qty: float = Form(0.0)):
    wo = _get_wo(wo_id)
    if not wo:
        return HTMLResponse("Work Order not found", status_code=404)
    if wo.get("status") not in {"draft", "in_progress"}:
        return RedirectResponse(url=f"/production/work_orders/{wo_id}", status_code=303)
    planned_qty = float(wo.get("quantity", 0))
    qty = float(produce_qty) if produce_qty is not None else planned_qty
    # Compute material cost
    mat_total = 0.0
    # If backflush and no consumption recorded yet, consume for qty now
    bom = _find_bom(wo.get("product"))
    if wo.get("issue_method") == "backflush" and not wo.get("consumed") and bom:
        consumed_lines: list[dict] = []
        for comp in bom.get("components", []):
            cp = comp.get("product")
            cqty = float(comp.get("quantity", 0)) * float(qty)
            avg_cost = get_avg_cost(cp, warehouse=wo.get("warehouse", "Main"), location=wo.get("location", ""))
            record_move(product=cp, quantity=cqty, unit_cost=avg_cost, mtype="out", ref=f"WO-{wo_id}", warehouse=wo.get("warehouse", "Main"), location=wo.get("location", ""))
            consumed_lines.append({"product": cp, "quantity": cqty, "unit_cost": avg_cost})
        wo["consumed"] = (wo.get("consumed", []) or []) + consumed_lines
    # Material total for this completion: approximate using current avg costs × BOM × qty
    if bom:
        for comp in bom.get("components", []):
            cp = comp.get("product")
            cqty = float(comp.get("quantity", 0)) * float(qty)
            avg_cost = get_avg_cost(cp, warehouse=wo.get("warehouse", "Main"), location=wo.get("location", ""))
            mat_total += cqty * avg_cost
    extra_base = float(wo.get("labor_cost", 0)) + float(wo.get("overhead_cost", 0))
    # Operations cost: sum(minutes * rate)
    ops_total = sum((float(op.get("minutes", 0)) * float(op.get("rate", 0))) for op in (wo.get("operations") or []))
    # Prorate extra costs by produced ratio
    ratio = (qty / planned_qty) if planned_qty else 1.0
    extra = (extra_base + ops_total) * ratio
    total_cost = mat_total + extra
    unit_cost = (total_cost / qty) if qty else 0.0
    # Record finished goods in
    record_move(product=wo.get("product"), quantity=qty, unit_cost=unit_cost, mtype="in", ref=f"WO-{wo_id}", warehouse=wo.get("warehouse", "Main"), location=wo.get("location", ""))
    produced_lines = (wo.get("produced", []) or [])
    produced_lines.append({"product": wo.get("product"), "quantity": qty, "unit_cost": unit_cost})
    wo["produced"] = produced_lines
    # Scrap handling (metadata only)
    sqty = float(scrap_qty or 0)
    if sqty > 0:
        scrap = (wo.get("scrap", []) or [])
        scrap.append({"quantity": sqty, "date": datetime.utcnow().isoformat(timespec="seconds") + "Z"})
        wo["scrap"] = scrap
    # Determine status based on total produced
    total_produced = sum(float(l.get("quantity", 0)) for l in wo.get("produced", []))
    wo["status"] = "completed" if total_produced >= planned_qty else "in_progress"
    _save_wo(wo)
    # After producing finished goods (mtype=in), redirect to printable barcode labels
    try:
        label_qty = int(max(1, round(qty)))
    except Exception:
        label_qty = 1
    return RedirectResponse(url=f"/production/work_orders/{wo_id}/labels?qty={label_qty}", status_code=303)


@router.post("/work_orders/{wo_id}/execute/log")
async def wo_exec_log(wo_id: str, operator: str = Form(""), action: str = Form(""), step: str = Form(""), notes: str = Form("")):
    """Record an operator action (start/stop/inspect etc.) on a work order."""
    wo = _get_wo(wo_id)
    if not wo:
        return HTMLResponse("Work Order not found", status_code=404)
    logs = (wo.get("operator_logs") or [])
    logs.append({
        "date": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "operator": operator,
        "action": action,
        "step": step,
        "notes": notes,
    })
    wo["operator_logs"] = logs
    if wo.get("status") == "draft":
        wo["status"] = "in_progress"
    _save_wo(wo)
    return RedirectResponse(url=f"/production/work_orders/{wo_id}", status_code=303)


@router.post("/work_orders/{wo_id}/execute/downtime")
async def wo_exec_downtime(wo_id: str, minutes: float = Form(...), reason: str = Form("")):
    """Record downtime event with minutes and reason."""
    wo = _get_wo(wo_id)
    if not wo:
        return HTMLResponse("Work Order not found", status_code=404)
    dlist = (wo.get("downtime") or [])
    try:
        mins = float(minutes)
    except Exception:
        mins = 0.0
    dlist.append({
        "date": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "minutes": mins,
        "reason": reason,
    })
    wo["downtime"] = dlist
    _save_wo(wo)
    return RedirectResponse(url=f"/production/work_orders/{wo_id}", status_code=303)


@router.post("/work_orders/{wo_id}/execute/output")
async def wo_exec_output(wo_id: str, good_qty: float = Form(0.0), scrap_qty: float = Form(0.0)):
    """Record operator output counts (good and scrap) without inventory moves."""
    wo = _get_wo(wo_id)
    if not wo:
        return HTMLResponse("Work Order not found", status_code=404)
    try:
        g = float(good_qty or 0)
    except Exception:
        g = 0.0
    try:
        s = float(scrap_qty or 0)
    except Exception:
        s = 0.0
    outlogs = (wo.get("output_logs") or [])
    outlogs.append({
        "date": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "good_qty": g,
        "scrap_qty": s,
    })
    wo["output_logs"] = outlogs
    # Accumulate scrap metadata
    if s > 0:
        scrap = (wo.get("scrap", []) or [])
        scrap.append({"quantity": s, "date": datetime.utcnow().isoformat(timespec="seconds") + "Z"})
        wo["scrap"] = scrap
    if wo.get("status") == "draft":
        wo["status"] = "in_progress"
    _save_wo(wo)
    return RedirectResponse(url=f"/production/work_orders/{wo_id}", status_code=303)


@router.post("/work_orders/{wo_id}/produce_wip")
async def wo_produce_wip(wo_id: str, produce_qty: float = Form(...), wip_location: str = Form("WIP")):
    """Record semi-finished production to a WIP location with cost accumulation."""
    wo = _get_wo(wo_id)
    if not wo:
        return HTMLResponse("Work Order not found", status_code=404)
    try:
        qty = float(produce_qty)
    except Exception:
        qty = 0.0
    if qty <= 0:
        return RedirectResponse(url=f"/production/work_orders/{wo_id}", status_code=303)
    bom = _find_bom(wo.get("product"))
    # If backflush and no consumption yet, consume materials now for qty
    if wo.get("issue_method") == "backflush" and not wo.get("consumed") and bom:
        consumed_lines: list[dict] = []
        for comp in bom.get("components", []):
            cp = comp.get("product")
            cqty = float(comp.get("quantity", 0)) * float(qty)
            avg_cost = get_avg_cost(cp, warehouse=wo.get("warehouse", "Main"), location=wo.get("location", ""))
            record_move(product=cp, quantity=cqty, unit_cost=avg_cost, mtype="out", ref=f"WO-{wo_id}", warehouse=wo.get("warehouse", "Main"), location=wo.get("location", ""))
            consumed_lines.append({"product": cp, "quantity": cqty, "unit_cost": avg_cost})
        wo["consumed"] = (wo.get("consumed", []) or []) + consumed_lines
    # Cost estimate for WIP unit
    mat_total = 0.0
    if bom:
        for comp in bom.get("components", []):
            cp = comp.get("product")
            cqty = float(comp.get("quantity", 0)) * float(qty)
            avg_cost = get_avg_cost(cp, warehouse=wo.get("warehouse", "Main"), location=wo.get("location", ""))
            mat_total += cqty * avg_cost
    extra_base = float(wo.get("labor_cost", 0)) + float(wo.get("overhead_cost", 0))
    ops_total = sum((float(op.get("minutes", 0)) * float(op.get("rate", 0))) for op in (wo.get("operations") or []))
    planned_qty = float(wo.get("quantity", 0))
    ratio = (qty / planned_qty) if planned_qty else 1.0
    extra = (extra_base + ops_total) * ratio
    total_cost = mat_total + extra
    unit_cost = (total_cost / qty) if qty else 0.0
    # Record WIP as stock-in to WIP location (same warehouse)
    record_move(product=wo.get("product"), quantity=qty, unit_cost=unit_cost, mtype="in", ref=f"WO-{wo_id}-WIP", warehouse=wo.get("warehouse", "Main"), location=wip_location)
    wip_lines = (wo.get("wip", []) or [])
    wip_lines.append({"product": wo.get("product"), "quantity": qty, "unit_cost": unit_cost, "location": wip_location})
    wo["wip"] = wip_lines
    if wo.get("status") == "draft":
        wo["status"] = "in_progress"
    _save_wo(wo)
    return RedirectResponse(url=f"/production/work_orders/{wo_id}", status_code=303)


@router.get("/work_orders/{wo_id}/labels", response_class=HTMLResponse)
async def wo_labels(request: Request, wo_id: str, qty: int = 1):
    wo = _get_wo(wo_id)
    if not wo:
        return HTMLResponse("Work Order not found", status_code=404)
    try:
        count = int(max(1, qty))
    except Exception:
        count = 1
    product = wo.get("product")
    # Render a simple printable label sheet; frontend JS generates barcodes
    tpl = templates_env.get_template("production_labels.html")
    return HTMLResponse(tpl.render(request=request, product=product, qty=count, wo=wo))