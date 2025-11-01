from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timedelta, date
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


# --- Demand Forecasting ---

DATA_DIR = Path("backend/data")
MACHINES_FILE = DATA_DIR / "machines.json"
DATA_DIR.mkdir(parents=True, exist_ok=True)
if not MACHINES_FILE.exists():
    MACHINES_FILE.write_text("[]", encoding="utf-8")


def _month_key(dt: date) -> str:
    return f"{dt.year:04d}-{dt.month:02d}"


def _load_json(path: Path) -> list:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def load_machines() -> List[dict]:
    return _load_json(MACHINES_FILE)


def save_machines(rows: List[dict]) -> None:
    try:
        MACHINES_FILE.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def forecast_demand(months_ahead: int = 3, seasonality: bool = True) -> List[dict]:
    """Simple demand forecast per product using sales history with optional seasonal index.
    - Aggregates monthly sales quantities from orders for the last 12 months.
    - Computes per-month seasonal index relative to the 12-month mean.
    - Forecasts the next N months using mean × seasonal index for corresponding calendar months.
    """
    # Build monthly totals per product
    orders = load_sales_orders()
    by_month: Dict[str, Dict[str, float]] = {}
    for o in orders:
        try:
            od = datetime.fromisoformat(o.date.replace("Z", "")).date()
        except Exception:
            continue
        mk = _month_key(od)
        for it in o.items:
            p = it.product
            qty = float(it.quantity)
            by_month.setdefault(p, {})
            by_month[p][mk] = by_month[p].get(mk, 0.0) + qty
    results: List[dict] = []
    today = datetime.utcnow().date()
    # Historical window: last 12 months
    hist_months: List[str] = []
    for i in range(12, 0, -1):
        d = (today.replace(day=1) - timedelta(days=30 * i))
        hist_months.append(_month_key(d))
    # Prepare forecast months ahead
    ahead_keys: List[str] = []
    base = today.replace(day=1)
    for i in range(1, months_ahead + 1):
        d = base + timedelta(days=30 * i)
        ahead_keys.append(_month_key(d))
    for p, mon in by_month.items():
        # Mean over available hist months
        hist_vals = [mon.get(m, 0.0) for m in hist_months]
        if not hist_vals:
            continue
        mean = sum(hist_vals) / max(1, len(hist_vals))
        # Seasonal index by calendar month number (1-12)
        month_index: Dict[int, float] = {}
        if seasonality:
            month_totals: Dict[int, List[float]] = {}
            for mk, qty in mon.items():
                try:
                    y, m = mk.split("-")
                    mnum = int(m)
                except Exception:
                    continue
                month_totals.setdefault(mnum, []).append(float(qty))
            for mnum, vals in month_totals.items():
                avg_m = sum(vals) / max(1, len(vals))
                month_index[mnum] = (avg_m / mean) if mean else 1.0
        # Build forecast rows
        for ak in ahead_keys:
            try:
                _, m = ak.split("-")
                mnum = int(m)
            except Exception:
                mnum = today.month
            idx = month_index.get(mnum, 1.0) if seasonality else 1.0
            fqty = mean * idx
            results.append({
                "product": p,
                "month": ak,
                "forecast_qty": round(float(fqty), 3),
                "mean": round(mean, 3),
                "season_index": round(idx, 3),
            })
    # Sort by product, then month
    results.sort(key=lambda r: (r["product"], r["month"]))
    return results


def _load_policies() -> Dict[str, dict]:
    POLICIES_FILE = DATA_DIR / "planning_policies.json"
    if not POLICIES_FILE.exists():
        try:
            POLICIES_FILE.write_text("[]", encoding="utf-8")
        except Exception:
            pass
    rows = _load_json(POLICIES_FILE)
    out: Dict[str, dict] = {}
    for r in rows:
        out[str(r.get("product"))] = {
            "mode": str(r.get("mode", "mixed")).lower(),
            "reorder_level": float(r.get("reorder_level", 0.0)),
            "target_level": float(r.get("target_level", 0.0)),
        }
    return out


def _save_policies(policies: List[dict]) -> None:
    POLICIES_FILE = DATA_DIR / "planning_policies.json"
    try:
        POLICIES_FILE.write_text(json.dumps(policies, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def plan_mrp(
    warehouse: str | None = None,
    location: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    mode: str | None = None,
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
    policies = _load_policies()
    selected_mode = (mode or "mixed").lower()
    fg_net: Dict[str, float] = {}
    if selected_mode in {"mixed", "mto"}:
        # MTO: net demand from sales orders
        for p, dqty in demand.items():
            supply_qty = (onhand.get(p, 0.0) + incoming_po.get(p, 0.0) + planned_wo_supply.get(p, 0.0))
            net = max(0.0, float(dqty) - float(supply_qty))
            if net > 0:
                fg_net[p] = fg_net.get(p, 0.0) + net
    if selected_mode in {"mixed", "mts"}:
        # MTS: fill up to target_level for products with MTS policy
        for p, policy in policies.items():
            if str(policy.get("mode", "mixed")).lower() not in {"mixed", "mts"}:
                continue
            target = float(policy.get("target_level", 0.0))
            if target <= 0:
                continue
            supply_qty = onhand.get(p, 0.0) + incoming_po.get(p, 0.0) + planned_wo_supply.get(p, 0.0)
            deficit = max(0.0, target - float(supply_qty))
            if deficit > 0:
                fg_net[p] = fg_net.get(p, 0.0) + deficit

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
            "policy": str(policies.get(p, {}).get("mode", "mixed")),
            "target_level": float(policies.get(p, {}).get("target_level", 0.0)),
        })

    return {
        "rows": rows,
        "demand": demand,
        "onhand": onhand,
        "incoming_po": incoming_po,
        "planned_wo": planned_wo_supply,
        "make_suggestions": make_suggestions,
        "buy_suggestions": buy_suggestions,
        "policies": policies,
    }


@router.get("/plan", response_class=HTMLResponse)
async def mrp_plan(request: Request, warehouse: str | None = None, location: str | None = None, start_date: str | None = None, end_date: str | None = None, mode: str | None = None):
    data = plan_mrp(warehouse=warehouse, location=location, start_date=start_date, end_date=end_date, mode=mode)
    tpl = templates_env.get_template("mrp_plan.html")
    return HTMLResponse(tpl.render(
        request=request,
        rows=data["rows"],
        selected_warehouse=warehouse or "",
        selected_location=location or "",
        selected_start_date=start_date or "",
        selected_end_date=end_date or "",
        selected_mode=(mode or "mixed"),
    ))


@router.get("/forecast", response_class=HTMLResponse)
async def mrp_forecast(request: Request, months_ahead: int = 3, seasonality: bool = True):
    try:
        months = int(months_ahead)
    except Exception:
        months = 3
    rows = forecast_demand(months_ahead=months, seasonality=bool(seasonality))
    tpl = templates_env.get_template("mrp_forecast.html")
    return HTMLResponse(tpl.render(
        request=request,
        rows=rows,
        months_ahead=months,
        seasonality=seasonality,
    ))


@router.get("/capacity", response_class=HTMLResponse)
async def mrp_capacity(request: Request):
    # Compute simple capacity summary: labor and machines per day
    machines = load_machines()
    emps_count = 0
    try:
        from .employees import load_employees as _load_emps
        emps_count = len(_load_emps())
    except Exception:
        emps_count = 0
    labor_minutes_per_day = emps_count * 8 * 60
    # Planned load from open work orders operations
    wos = load_work_orders()
    planned_minutes = sum(float(op.get("minutes", 0)) for w in wos if w.get("status") in {"draft", "in_progress"} for op in (w.get("operations") or []))
    tpl = templates_env.get_template("mrp_capacity.html")
    return HTMLResponse(tpl.render(
        request=request,
        machines=machines,
        labor_minutes_per_day=labor_minutes_per_day,
        planned_minutes=round(planned_minutes, 2),
        employees_count=emps_count,
    ))


@router.post("/capacity/machines")
async def mrp_capacity_add_machine(name: str = Form(...), minutes_per_day: float = Form(480)):
    machines = load_machines()
    machines.append({"name": name, "minutes_per_day": float(minutes_per_day)})
    save_machines(machines)
    return RedirectResponse(url="/mrp/capacity", status_code=303)


@router.get("/schedule", response_class=HTMLResponse)
async def mrp_schedule(request: Request, view: str = "daily"):
    # Derive a simple schedule from draft/in_progress WOs and operations
    wos = load_work_orders()
    # For demo: build buckets by planned_start date if present, else today
    today = datetime.utcnow().date()
    buckets: Dict[str, List[dict]] = {}
    for w in wos:
        if w.get("status") not in {"draft", "in_progress"}:
            continue
        start_str = w.get("planned_start") or today.isoformat()
        buckets.setdefault(start_str, []).append(w)
    rows = []
    for dstr, lst in sorted(buckets.items()):
        total_minutes = sum(float(op.get("minutes", 0)) for w in lst for op in (w.get("operations") or []))
        rows.append({"date": dstr, "wo_count": len(lst), "total_minutes": round(total_minutes, 2)})
    tpl = templates_env.get_template("mrp_schedule.html")
    return HTMLResponse(tpl.render(request=request, view=view, rows=rows, wos=wos))


@router.post("/schedule/auto")
async def mrp_schedule_auto():
    # Auto-assign planned_start based on simple capacity threshold
    machines = load_machines()
    total_machine_minutes = sum(float(m.get("minutes_per_day", 0)) for m in machines) or 480.0
    try:
        from .employees import load_employees as _load_emps
        labor_minutes = len(_load_emps()) * 8 * 60
    except Exception:
        labor_minutes = 0.0
    daily_capacity = min(total_machine_minutes, labor_minutes or total_machine_minutes)
    wos = load_work_orders()
    # Assign sequential days while filling capacity with operations minutes
    day = datetime.utcnow().date()
    used = 0.0
    for w in wos:
        if w.get("status") not in {"draft", "in_progress"}:
            continue
        op_minutes = sum(float(op.get("minutes", 0)) for op in (w.get("operations") or []))
        if used + op_minutes > daily_capacity:
            # move to next day
            day = day + timedelta(days=1)
            used = 0.0
        w["planned_start"] = day.isoformat()
        used += op_minutes
    save_work_orders(wos)
    return RedirectResponse(url="/mrp/schedule", status_code=303)


@router.get("/allocation", response_class=HTMLResponse)
async def mrp_allocation(request: Request):
    machines = load_machines()
    try:
        from .employees import load_employees as _load_emps
        employees = _load_emps()
    except Exception:
        employees = []
    wos = load_work_orders()
    open_wos = [w for w in wos if w.get("status") in {"draft", "in_progress"}]
    tpl = templates_env.get_template("mrp_allocation.html")
    return HTMLResponse(tpl.render(request=request, machines=machines, employees=employees, work_orders=open_wos))


@router.post("/allocation/assign")
async def mrp_allocation_assign(wo_id: str = Form(...), op_index: int = Form(...), machine_name: str = Form(""), operator_emp_id: str = Form("")):
    wos = load_work_orders()
    for w in wos:
        if w.get("id") == wo_id:
            ops = (w.get("operations") or [])
            if 0 <= int(op_index) < len(ops):
                ops[int(op_index)]["machine"] = machine_name or None
                ops[int(op_index)]["operator_emp_id"] = operator_emp_id or None
                w["operations"] = ops
            break
    save_work_orders(wos)
    return RedirectResponse(url="/mrp/allocation", status_code=303)


@router.post("/execute")
async def mrp_execute(
    create_pos: bool = Form(False),
    create_wos: bool = Form(False),
    warehouse: str = Form("Main"),
    location: str = Form(""),
    mode: str = Form("mixed"),
):
    # Compute suggestions
    data = plan_mrp(warehouse=warehouse, location=location, mode=mode)
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
                "planning_mode": (mode or "mixed"),
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
    return RedirectResponse(url=f"/mrp/plan?warehouse={warehouse}&location={location}&mode={mode}&notice={notice}", status_code=303)


@router.get("/policies", response_class=HTMLResponse)
async def mrp_policies(request: Request):
    POLICIES_FILE = DATA_DIR / "planning_policies.json"
    rows = _load_json(POLICIES_FILE)
    # Load products list for convenience if available
    products: List[str] = []
    try:
        from .inventory import load_products as _lp
        products = [p.get("name") for p in _lp()] or []
    except Exception:
        products = []
    tpl = templates_env.get_template("mrp_policies.html")
    return HTMLResponse(tpl.render(request=request, rows=rows, products=products))


@router.post("/policies")
async def mrp_policies_save(product: str = Form(...), mode: str = Form("mixed"), reorder_level: float = Form(0.0), target_level: float = Form(0.0)):
    POLICIES_FILE = DATA_DIR / "planning_policies.json"
    rows = _load_json(POLICIES_FILE)
    # Upsert
    found = False
    for r in rows:
        if str(r.get("product")) == product:
            r["mode"] = str(mode).lower()
            r["reorder_level"] = float(reorder_level)
            r["target_level"] = float(target_level)
            found = True
            break
    if not found:
        rows.append({
            "product": product,
            "mode": str(mode).lower(),
            "reorder_level": float(reorder_level),
            "target_level": float(target_level),
        })
    _save_policies(rows)
    return RedirectResponse(url="/mrp/policies", status_code=303)