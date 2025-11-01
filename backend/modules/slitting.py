from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.responses import RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape


# Jinja environment for Slitting templates
templates_env = Environment(
    loader=FileSystemLoader("backend/templates"),
    autoescape=select_autoescape(["html", "xml"]),
)


router = APIRouter(prefix="/slitting", tags=["Slitting"])


# Simple JSON storage for Slitting plans
DATA_DIR = Path("backend/data")
SLITTING_PLANS_FILE = DATA_DIR / "slitting_plans.json"
DATA_DIR.mkdir(parents=True, exist_ok=True)
if not SLITTING_PLANS_FILE.exists():
    SLITTING_PLANS_FILE.write_text("[]", encoding="utf-8")


def _load_json(fp: Path) -> List[Dict]:
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_json(fp: Path, rows: List[Dict]):
    fp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def _now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def compute_uniform_plan(
    coil_width_mm: float,
    usable_width_mm: float,
    strip_count: int,
    kerf_mm: float,
    left_trim_mm: float,
    right_trim_mm: float,
    max_knives: int,
    min_width_mm: float,
    max_width_mm: float,
    tolerance_mm: float,
) -> Dict:
    """Compute a simple uniform strips plan with machine constraints.
    Effective width = coil_width - trims - kerf*(n-1).
    Each strip width = effective_width / n.
    """
    n = max(1, int(strip_count))
    errors: List[str] = []
    if max_knives and n > max_knives:
        errors.append(f"Requested {n} strips exceeds max knives {max_knives}.")
    width_base = float(usable_width_mm) if float(usable_width_mm) > 0 else float(coil_width_mm)
    kerf_total = float(kerf_mm) * (n - 1)
    effective = max(0.0, width_base - float(left_trim_mm) - float(right_trim_mm) - kerf_total)
    strip_w = effective / n if n > 0 else 0.0
    if min_width_mm and strip_w < float(min_width_mm) - float(tolerance_mm):
        errors.append(f"Computed strip width {strip_w:.3f} mm below minimum {min_width_mm} mm (tolerance {tolerance_mm} mm).")
    if max_width_mm and strip_w > float(max_width_mm) + float(tolerance_mm):
        errors.append(f"Computed strip width {strip_w:.3f} mm above maximum {max_width_mm} mm (tolerance {tolerance_mm} mm).")
    offsets = []
    pos = float(left_trim_mm)
    for i in range(n):
        offsets.append(pos)
        pos += strip_w
        if i < n - 1:
            pos += float(kerf_mm)
    used_width = float(left_trim_mm) + n * strip_w + float(kerf_mm) * (n - 1) + float(right_trim_mm)
    scrap_width = max(0.0, float(coil_width_mm) - used_width)
    yield_pct = (coil_width_mm - left_trim_mm - right_trim_mm - kerf_mm * (n - 1) - scrap_width) / coil_width_mm * 100.0 if coil_width_mm > 0 else 0.0
    feasible = len(errors) == 0
    return {
        "mode": "uniform",
        "strip_count": n,
        "strip_width": round(strip_w, 3),
        "knife_offsets": [round(x, 3) for x in offsets],
        "base_width_mm": round(width_base, 3),
        "kerf_total_mm": round(kerf_total, 3),
        "strip_deviations": [],
        "effective_width": round(effective, 3),
        "margin_mm": round(max(0.0, effective - (strip_w * n)), 3),
        "used_width": round(used_width, 3),
        "scrap_width": round(scrap_width, 3),
        "yield_pct": round(yield_pct, 2),
        "feasible": feasible,
        "errors": errors,
    }


def compute_custom_plan(
    coil_width_mm: float,
    usable_width_mm: float,
    widths: List[float],
    kerf_mm: float,
    left_trim_mm: float,
    right_trim_mm: float,
    max_knives: int,
    min_width_mm: float,
    max_width_mm: float,
    tolerance_mm: float,
    strict_targets: bool,
) -> Dict:
    """Compute layout for explicit widths with constraints. If total exceeds effective width, scale proportionally.
    If scaling causes any width to deviate beyond tolerance from requested, mark infeasible.
    """
    sane_widths = [max(0.0, float(w)) for w in widths if float(w) > 0]
    n = len(sane_widths)
    errors: List[str] = []
    if max_knives and n > max_knives:
        errors.append(f"Requested {n} strips exceeds max knives {max_knives}.")
    width_base = float(usable_width_mm) if float(usable_width_mm) > 0 else float(coil_width_mm)
    kerf_total = float(kerf_mm) * (n - 1)
    effective = max(0.0, width_base - float(left_trim_mm) - float(right_trim_mm) - kerf_total)
    total_req = sum(sane_widths)
    scale = 1.0
    if strict_targets:
        if total_req > effective + float(tolerance_mm):
            errors.append(
                f"Requested total {total_req:.3f} mm exceeds effective usable width {effective:.3f} mm (tolerance {tolerance_mm} mm)."
            )
        final_widths = list(sane_widths)
    else:
        if total_req > effective and effective > 0:
            scale = effective / total_req
        final_widths = [w * scale for w in sane_widths]
    # Check min/max strip width boundaries
    for idx, w in enumerate(final_widths, start=1):
        if min_width_mm and w < float(min_width_mm) - float(tolerance_mm):
            errors.append(f"Strip {idx} width {w:.3f} mm below minimum {min_width_mm} mm (tolerance {tolerance_mm} mm).")
        if max_width_mm and w > float(max_width_mm) + float(tolerance_mm):
            errors.append(f"Strip {idx} width {w:.3f} mm above maximum {max_width_mm} mm (tolerance {tolerance_mm} mm).")
    # Check deviation from requested widths when scaled
    if not strict_targets and scale != 1.0:
        for idx, (req, got) in enumerate(zip(sane_widths, final_widths), start=1):
            if abs(got - req) > float(tolerance_mm):
                errors.append(f"Strip {idx} deviates {abs(got-req):.3f} mm from requested {req:.3f} mm beyond tolerance {tolerance_mm} mm.")
    offsets = []
    pos = float(left_trim_mm)
    for i, w in enumerate(final_widths):
        offsets.append(pos)
        pos += w
        if i < n - 1:
            pos += float(kerf_mm)
    used_width = float(left_trim_mm) + sum(final_widths) + float(kerf_mm) * (n - 1) + float(right_trim_mm)
    scrap_width = max(0.0, float(coil_width_mm) - used_width)
    yield_pct = (coil_width_mm - left_trim_mm - right_trim_mm - kerf_mm * (n - 1) - scrap_width) / coil_width_mm * 100.0 if coil_width_mm > 0 else 0.0
    feasible = len(errors) == 0
    # Build per-strip deviations (requested vs final) for operator visibility
    strip_deviations = []
    for idx, (req, got) in enumerate(zip(sane_widths, final_widths), start=1):
        delta = float(got) - float(req)
        pct = (delta / req * 100.0) if req > 0 else 0.0
        strip_deviations.append({
            "index": idx,
            "requested_mm": round(req, 3),
            "final_mm": round(got, 3),
            "delta_mm": round(delta, 3),
            "delta_pct": round(pct, 3),
        })
    return {
        "mode": "custom",
        "strip_count": n,
        "requested_widths": [round(w, 3) for w in sane_widths],
        "strip_widths": [round(w, 3) for w in final_widths],
        "knife_offsets": [round(x, 3) for x in offsets],
        "base_width_mm": round(width_base, 3),
        "kerf_total_mm": round(kerf_total, 3),
        "strip_deviations": strip_deviations,
        "effective_width": round(effective, 3),
        "margin_mm": round(max(0.0, effective - sum(final_widths)), 3),
        "used_width": round(used_width, 3),
        "scrap_width": round(scrap_width, 3),
        "yield_pct": round(yield_pct, 2),
        "scale_factor": round(scale, 6),
        "feasible": feasible,
        "errors": errors,
    }


def save_plan(plan: Dict) -> Dict:
    rows = _load_json(SLITTING_PLANS_FILE)
    rows.append(plan)
    _save_json(SLITTING_PLANS_FILE, rows)
    return plan


def load_plan(plan_id: str) -> Optional[Dict]:
    rows = _load_json(SLITTING_PLANS_FILE)
    for r in rows:
        if r.get("id") == plan_id:
            return r
    return None


@router.get("/new", response_class=HTMLResponse)
async def slitting_new(request: Request):
    tpl = templates_env.get_template("slitting_new.html")
    app_item = {"name": "Slitting", "slug": "slitting", "color": "#22c55e", "description": "Coil slitting planning and optimization"}
    return HTMLResponse(tpl.render(request=request, app_item=app_item))


@router.get("/plans", response_class=HTMLResponse)
async def slitting_plans(request: Request):
    plans = _load_json(SLITTING_PLANS_FILE)
    tpl = templates_env.get_template("slitting_plans.html")
    app_item = {"name": "Slitting", "slug": "slitting", "color": "#22c55e", "description": "Coil slitting planning and optimization"}
    return HTMLResponse(tpl.render(request=request, app_item=app_item, plans=list(reversed(plans))))


@router.post("/plan", response_class=HTMLResponse)
async def slitting_plan_post(
    request: Request,
    coil_id: str = Form("") ,
    coil_width_mm: float = Form(...),
    coil_thickness_mm: float = Form(0.0),
    material: str = Form(""),
    left_trim_mm: float = Form(2.0),
    right_trim_mm: float = Form(2.0),
    kerf_mm: float = Form(0.2),
    usable_width_mm: float = Form(0.0),
    mode: str = Form("uniform"),
    strip_count: int = Form(0),
    custom_widths: str = Form(""),
    max_knives: int = Form(20),
    min_width_mm: float = Form(0.0),
    max_width_mm: float = Form(0.0),
    tolerance_mm: float = Form(0.5),
    strict_targets: bool = Form(False),
):
    # Compute plan
    if mode == "uniform" and strip_count > 0:
        computed = compute_uniform_plan(coil_width_mm, usable_width_mm, strip_count, kerf_mm, left_trim_mm, right_trim_mm, max_knives, min_width_mm, max_width_mm, tolerance_mm)
    else:
        widths = []
        if custom_widths:
            try:
                widths = [float(x.strip()) for x in custom_widths.split(",") if x.strip()]
            except Exception:
                widths = []
        computed = compute_custom_plan(coil_width_mm, usable_width_mm, widths, kerf_mm, left_trim_mm, right_trim_mm, max_knives, min_width_mm, max_width_mm, tolerance_mm, strict_targets)

    plan = {
        "id": f"SLP-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}",
        "created_at": _now_iso(),
        "coil": {
            "id": coil_id or "",
            "width_mm": float(coil_width_mm),
            "thickness_mm": float(coil_thickness_mm),
            "material": material or "",
        },
        "params": {
            "left_trim_mm": float(left_trim_mm),
            "right_trim_mm": float(right_trim_mm),
            "kerf_mm": float(kerf_mm),
            "mode": mode,
        },
        "constraints": {
            "max_knives": int(max_knives),
            "min_width_mm": float(min_width_mm),
            "max_width_mm": float(max_width_mm),
            "tolerance_mm": float(tolerance_mm),
            "usable_width_mm": float(usable_width_mm),
            "strict_targets": bool(strict_targets),
        },
        "computed": computed,
    }
    if computed.get("feasible"):
        save_plan(plan)
    tpl = templates_env.get_template("slitting_plan.html")
    app_item = {"name": "Slitting", "slug": "slitting", "color": "#22c55e", "description": "Coil slitting planning and optimization"}
    return HTMLResponse(tpl.render(request=request, app_item=app_item, plan=plan))


@router.get("/plan/{plan_id}", response_class=HTMLResponse)
async def slitting_plan_view(request: Request, plan_id: str):
    plan = load_plan(plan_id)
    if not plan:
        return RedirectResponse(url="/slitting/plans")
    tpl = templates_env.get_template("slitting_plan.html")
    app_item = {"name": "Slitting", "slug": "slitting", "color": "#22c55e", "description": "Coil slitting planning and optimization"}
    return HTMLResponse(tpl.render(request=request, app_item=app_item, plan=plan))


@router.get("/plan/{plan_id}/print", response_class=HTMLResponse)
async def slitting_plan_print(request: Request, plan_id: str):
    plan = load_plan(plan_id)
    if not plan:
        return RedirectResponse(url="/slitting/plans")
    tpl = templates_env.get_template("slitting_plan_print.html")
    return HTMLResponse(tpl.render(request=request, plan=plan))


@router.get("/plan/{plan_id}/json")
async def slitting_plan_json(plan_id: str):
    plan = load_plan(plan_id)
    if not plan:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return JSONResponse(plan)