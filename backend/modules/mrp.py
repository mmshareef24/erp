from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Set

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse
from starlette.responses import RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

# Reuse existing loaders and helpers from modules
from .sales import load_orders as load_sales_orders, load_deliveries
from .purchases import load_orders as load_purchase_orders, save_orders as save_purchase_orders
from .production import (
    load_boms,
    load_work_orders,
    save_work_orders,
)
from .inventory import compute_on_hand, compute_on_hand_site


templates_env = Environment(
    loader=FileSystemLoader("backend/templates"),
    autoescape=select_autoescape(["html", "xml"]),
)


router = APIRouter(prefix="/mrp", tags=["MRP"])

# Simple default lead times in days (fallbacks; overridden by company settings)
MAKE_LEAD_DAYS = 3
BUY_LEAD_DAYS = 7


def _date_in_range(d: str, start: str | None, end: str | None) -> bool:
    try:
        dt = datetime.fromisoformat(d.replace("Z", ""))
    except Exception:
        return True
    if start:
        try:
            s = datetime.fromisoformat(start)
            if dt < s:
                return False
        except Exception:
            pass
    if end:
        try:
            e = datetime.fromisoformat(end)
            if dt > e:
                return False
        except Exception:
            pass
    return True


def _build_bom_index(boms: List[dict]) -> Dict[str, dict]:
    return {b.get("product"): b for b in boms}


def _explode_requirements(product: str, qty: float, bom_index: Dict[str, dict], level: int = 0, visited: Set[str] | None = None, max_depth: int = 5) -> Tuple[Dict[str, float], Dict[str, float]]:
    """Explode BOM requirements.
    Returns (make_items, buy_items) dicts mapping product->qty.
    - make_items: items that have BOM (subassemblies/FG) requiring work orders
    - buy_items: leaf components that should be purchased
    """
    if visited is None:
        visited = set()
    if level > max_depth or product in visited:
        return ({}, {})
    visited.add(product)
    bom = bom_index.get(product)
    if not bom:
        # No BOM => this is a buy item at requested qty
        return ({}, {product: float(qty)})
    # Product itself is make item
    make: Dict[str, float] = {product: float(qty)}
    buy: Dict[str, float] = {}
    for comp in bom.get("components", []) or []:
        cp = str(comp.get("product"))
        cqty = float(comp.get("quantity", 0)) * float(qty)
        sub_bom = bom_index.get(cp)
        if sub_bom:
            sub_make, sub_buy = _explode_requirements(cp, cqty, bom_index, level + 1, visited, max_depth)
            # accumulate subassemblies and leaf buys
            for k, v in sub_make.items():
                make[k] = make.get(k, 0.0) + float(v)
            for k, v in sub_buy.items():
                buy[k] = buy.get(k, 0.0) + float(v)
        else:
            buy[cp] = buy.get(cp, 0.0) + cqty
    return (make, buy)


def _aggregate(lst: List[Tuple[str, float]]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for p, q in lst:
        out[p] = out.get(p, 0.0) + float(q)
    return out


def plan_mrp(
    warehouse: str | None = None,
    location: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
):
    # Demand: outstanding sales order quantities within date range
    orders = [o for o in load_sales_orders() if _date_in_range(o.date, start_date, end_date)]
    demand_lines: List[Tuple[str, float]] = []
    for o in orders:
        for it in o.items:
            demand_lines.append((it.product, float(it.quantity)))
    delivered = load_deliveries()
    delivered_map: Dict[Tuple[str, str], float] = {}
    for d in delivered:
        if not _date_in_range(d.date, start_date, end_date):
            continue
        for it in d.items:
            key = (d.order_id, it.product)
            delivered_map[key] = delivered_map.get(key, 0.0) + float(it.quantity)
    # Outstanding demand per product: naive approach (subtract total delivered for each order)
    demand: Dict[str, float] = {}
    for o in orders:
        per_product: Dict[str, float] = {}
        for it in o.items:
            per_product[it.product] = per_product.get(it.product, 0.0) + float(it.quantity)
        for p, qty in per_product.items():
            # subtract delivered for this order-product
            delivered_qty = delivered_map.get((o.id, p), 0.0)
            outstanding = max(0.0, float(qty) - float(delivered_qty))
            if outstanding > 0:
                demand[p] = demand.get(p, 0.0) + outstanding

    # Supply: on-hand, incoming POs, planned WOs
    onhand_site = compute_on_hand_site(warehouse=warehouse, location=location) if (warehouse or location) else compute_on_hand()
    onhand: Dict[str, float] = {p: s.get("qty", 0.0) for p, s in onhand_site.items()}

    purchase_orders = load_purchase_orders()
    incoming_po: Dict[str, float] = {}
    for po in purchase_orders:
        if po.status != "confirmed":
            continue
        if not _date_in_range(po.date, start_date, end_date):
            continue
        for it in po.items:
            incoming_po[it.product] = incoming_po.get(it.product, 0.0) + float(it.quantity)

    wos = load_work_orders()
    planned_wo_supply: Dict[str, float] = {}
    for w in wos:
        if w.get("status") in {"draft", "in_progress"}:
            planned_wo_supply[w.get("product")] = planned_wo_supply.get(w.get("product"), 0.0) + float(w.get("quantity", 0))

    # Determine FG net requirements and explode to components
    boms = load_boms()
    bom_index = _build_bom_index(boms)
    fg_net: Dict[str, float] = {}
    for p, dqty in demand.items():
        supply_qty = (onhand.get(p, 0.0) + incoming_po.get(p, 0.0) + planned_wo_supply.get(p, 0.0))
        net = max(0.0, float(dqty) - float(supply_qty))
        if net > 0:
            fg_net[p] = net

    make_suggestions: Dict[str, float] = {}
    buy_suggestions: Dict[str, float] = {}
    for p, net_qty in fg_net.items():
        make, buy = _explode_requirements(p, net_qty, bom_index)
        # make includes FG and any subassemblies; buy includes leaf components
        for k, v in make.items():
            # Reduce by on-hand and other supplies
            supply = onhand.get(k, 0.0) + incoming_po.get(k, 0.0) + planned_wo_supply.get(k, 0.0)
            needed = max(0.0, float(v) - float(supply))
            if needed > 0:
                make_suggestions[k] = make_suggestions.get(k, 0.0) + needed
        for k, v in buy.items():
            supply = onhand.get(k, 0.0) + incoming_po.get(k, 0.0)
            needed = max(0.0, float(v) - float(supply))
            if needed > 0:
                buy_suggestions[k] = buy_suggestions.get(k, 0.0) + needed

    # Build row data for UI with availability context
    rows: List[dict] = []
    # Determine planning dates
    today = datetime.utcnow().date()
    try:
        need_by_dt = datetime.fromisoformat(end_date).date() if end_date else today
    except Exception:
        need_by_dt = today
    plan_start_dt = need_by_dt
    order_by_dt = need_by_dt
    # Dates adjusted by lead times from company settings
    try:
        from .settings import load_company
        company = load_company()
        make_lead = int(company.get("mrp_make_lead_days", MAKE_LEAD_DAYS))
        buy_lead = int(company.get("mrp_buy_lead_days", BUY_LEAD_DAYS))
        plan_start_dt = need_by_dt - timedelta(days=make_lead)
        order_by_dt = need_by_dt - timedelta(days=buy_lead)
    except Exception:
        # Fallback to module defaults
        plan_start_dt = need_by_dt - timedelta(days=MAKE_LEAD_DAYS)
        order_by_dt = need_by_dt - timedelta(days=BUY_LEAD_DAYS)
    all_products: Set[str] = set(demand.keys()) | set(make_suggestions.keys()) | set(buy_suggestions.keys())
    for p in sorted(all_products):
        rows.append({
            "product": p,
            "demand": round(float(demand.get(p, 0.0)), 3),
            "onhand": round(float(onhand.get(p, 0.0)), 3),
            "incoming_po": round(float(incoming_po.get(p, 0.0)), 3),
            "planned_wo": round(float(planned_wo_supply.get(p, 0.0)), 3),
            "suggest_make": round(float(make_suggestions.get(p, 0.0)), 3),
            "suggest_buy": round(float(buy_suggestions.get(p, 0.0)), 3),
            "need_by": need_by_dt.isoformat(),
            "plan_start": plan_start_dt.isoformat(),
            "order_by": order_by_dt.isoformat(),
        })

    return {
        "rows": rows,
        "demand": demand,
        "onhand": onhand,
        "incoming_po": incoming_po,
        "planned_wo": planned_wo_supply,
        "make_suggestions": make_suggestions,
        "buy_suggestions": buy_suggestions,
    }


@router.get("/plan", response_class=HTMLResponse)
async def mrp_plan(request: Request, warehouse: str | None = None, location: str | None = None, start_date: str | None = None, end_date: str | None = None):
    data = plan_mrp(warehouse=warehouse, location=location, start_date=start_date, end_date=end_date)
    tpl = templates_env.get_template("mrp_plan.html")
    return HTMLResponse(tpl.render(
        request=request,
        rows=data["rows"],
        selected_warehouse=warehouse or "",
        selected_location=location or "",
        selected_start_date=start_date or "",
        selected_end_date=end_date or "",
    ))


@router.post("/execute")
async def mrp_execute(
    create_pos: bool = Form(False),
    create_wos: bool = Form(False),
    warehouse: str = Form("Main"),
    location: str = Form(""),
):
    # Compute suggestions
    data = plan_mrp(warehouse=warehouse, location=location)
    # Create aggregated PO for buys
    created: Dict[str, List[str]] = {"pos": [], "wos": []}
    if create_pos and data["buy_suggestions"]:
        from uuid import uuid4
        from .purchases import PurchaseItem, PurchaseOrder, load_orders as load_po
        items: List[PurchaseItem] = []
        for p, q in data["buy_suggestions"].items():
            if q <= 0:
                continue
            items.append(PurchaseItem(product=p, quantity=float(q), unit_cost=0.0))
        if items:
            total = sum(it.line_total() for it in items)
            po = PurchaseOrder(
                id=str(uuid4()),
                vendor="AUTO-VENDOR",
                date=datetime.utcnow().isoformat(timespec="seconds") + "Z",
                items=items,
                status="confirmed",
                total=total,
            )
            pos = load_po()
            pos.append(po)
            save_purchase_orders(pos)
            created["pos"].append(po.id)
    # Create WOs for make items
    if create_wos and data["make_suggestions"]:
        from uuid import uuid4
        wos = load_work_orders()
        for p, q in data["make_suggestions"].items():
            if q <= 0:
                continue
            wos.append({
                "id": str(uuid4()),
                "date": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                "product": p,
                "quantity": float(q),
                "warehouse": warehouse,
                "location": location,
                "status": "draft",
                "labor_cost": 0.0,
                "overhead_cost": 0.0,
                "consumed": [],
                "produced": [],
                "operations": [],
                "issue_method": "backflush",
                "reserved": [],
                "scrap": [],
            })
        save_work_orders(wos)
        # Note: we don't have IDs easily per product here; just indicate count
        created["wos"].append(str(len(data["make_suggestions"])) + " WO(s)")
    # Redirect to plan with a basic notice via query string
    msg_parts: List[str] = []
    if created["pos"]:
        msg_parts.append(f"POs: {', '.join(created['pos'])}")
    if created["wos"]:
        msg_parts.append(f"WOs: {', '.join(created['wos'])}")
    notice = "; ".join(msg_parts) if msg_parts else "No actions taken"
    return RedirectResponse(url=f"/mrp/plan?warehouse={warehouse}&location={location}&notice={notice}", status_code=303)