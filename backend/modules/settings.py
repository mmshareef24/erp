from __future__ import annotations

import json
from pathlib import Path
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse
from starlette.responses import RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape


templates_env = Environment(
    loader=FileSystemLoader("backend/templates"),
    autoescape=select_autoescape(["html", "xml"]),
)


DATA_DIR = Path("backend/data")
WAREHOUSES_FILE = DATA_DIR / "warehouses.json"
LOCATIONS_FILE = DATA_DIR / "locations.json"
COMPANY_FILE = DATA_DIR / "company.json"
ORG_UNITS_FILE = DATA_DIR / "org_units.json"
POSITIONS_FILE = DATA_DIR / "positions.json"
EMPLOYEES_FILE = DATA_DIR / "employees.json"
DATA_DIR.mkdir(parents=True, exist_ok=True)
for f in [WAREHOUSES_FILE, LOCATIONS_FILE, COMPANY_FILE, ORG_UNITS_FILE, POSITIONS_FILE]:
    if not f.exists():
        # company.json will be initialized below if missing
        if f.name == "company.json":
            f.write_text("{}", encoding="utf-8")
        elif f.name in ("org_units.json", "positions.json"):
            f.write_text("[]", encoding="utf-8")
        else:
            f.write_text("[]", encoding="utf-8")


def _load_json(path: Path) -> list[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_json(path: Path, data: list[dict]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


router = APIRouter(prefix="/settings", tags=["Settings"])
from .auth import require_roles


@router.get("/warehouses", response_class=HTMLResponse)
async def warehouses_list(request: Request):
    warehouses = _load_json(WAREHOUSES_FILE)
    tpl = templates_env.get_template("settings_warehouses.html")
    return HTMLResponse(tpl.render(request=request, warehouses=warehouses))


@router.get("/warehouses/new", response_class=HTMLResponse)
async def warehouses_new(request: Request):
    tpl = templates_env.get_template("settings_warehouse_new.html")
    return HTMLResponse(tpl.render(request=request))


@router.post("/warehouses")
async def warehouses_create(name: str = Form(...)):
    warehouses = _load_json(WAREHOUSES_FILE)
    if not any(w.get("name") == name for w in warehouses):
        warehouses.append({"name": name})
        _save_json(WAREHOUSES_FILE, warehouses)
    return RedirectResponse(url="/settings/warehouses", status_code=303)


@router.get("/locations", response_class=HTMLResponse)
async def locations_list(request: Request):
    locations = _load_json(LOCATIONS_FILE)
    warehouses = _load_json(WAREHOUSES_FILE)
    tpl = templates_env.get_template("settings_locations.html")
    return HTMLResponse(tpl.render(request=request, locations=locations, warehouses=warehouses))


@router.get("/locations/new", response_class=HTMLResponse)
async def locations_new(request: Request):
    warehouses = _load_json(WAREHOUSES_FILE)
    tpl = templates_env.get_template("settings_location_new.html")
    return HTMLResponse(tpl.render(request=request, warehouses=warehouses))


@router.post("/locations")
async def locations_create(name: str = Form(...), warehouse: str = Form("")):
    locations = _load_json(LOCATIONS_FILE)
    locations.append({"name": name, "warehouse": warehouse})
    _save_json(LOCATIONS_FILE, locations)
    return RedirectResponse(url="/settings/locations", status_code=303)

# -----------------
# Organization units
# -----------------
def _next_org_unit_id(org_units: list[dict]) -> str:
    # Simple incremental id OU-0001
    base = "OU-"
    num = 1
    if org_units:
        try:
            nums = [int(u.get("id", "OU-0").split("-")[-1]) for u in org_units]
            num = max(nums) + 1
        except Exception:
            num = len(org_units) + 1
    return f"{base}{num:04d}"


@router.get("/org-units", response_class=HTMLResponse)
async def org_units_list(request: Request):
    org_units = _load_json(ORG_UNITS_FILE)
    tpl = templates_env.get_template("settings_org_units.html")
    return HTMLResponse(tpl.render(request=request, org_units=org_units))


@router.get("/org-units/new", response_class=HTMLResponse)
async def org_units_new(request: Request):
    org_units = _load_json(ORG_UNITS_FILE)
    tpl = templates_env.get_template("settings_org_unit_new.html")
    return HTMLResponse(tpl.render(request=request, org_units=org_units))


@router.post("/org-units")
async def org_units_create(name: str = Form(...), parent_id: str = Form("")):
    org_units = _load_json(ORG_UNITS_FILE)
    unit_id = _next_org_unit_id(org_units)
    org_units.append({"id": unit_id, "name": name.strip(), "parent_id": parent_id or None})
    _save_json(ORG_UNITS_FILE, org_units)
    return RedirectResponse(url="/settings/org-units", status_code=303)


# ---------
# Positions
# ---------
def _next_position_id(positions: list[dict]) -> str:
    base = "POS-"
    num = 1
    if positions:
        try:
            nums = [int(p.get("id", "POS-0").split("-")[-1]) for p in positions]
            num = max(nums) + 1
        except Exception:
            num = len(positions) + 1
    return f"{base}{num:04d}"


@router.get("/positions", response_class=HTMLResponse)
async def positions_list(request: Request):
    positions = _load_json(POSITIONS_FILE)
    org_units = _load_json(ORG_UNITS_FILE)
    tpl = templates_env.get_template("settings_positions.html")
    return HTMLResponse(tpl.render(request=request, positions=positions, org_units=org_units))


@router.get("/positions/new", response_class=HTMLResponse)
async def positions_new(request: Request):
    org_units = _load_json(ORG_UNITS_FILE)
    tpl = templates_env.get_template("settings_position_new.html")
    return HTMLResponse(tpl.render(request=request, org_units=org_units))


@router.post("/positions")
async def positions_create(title: str = Form(...), org_unit_id: str = Form("")):
    positions = _load_json(POSITIONS_FILE)
    org_units = _load_json(ORG_UNITS_FILE)
    pos_id = _next_position_id(positions)
    # Validate org unit id if provided
    if org_unit_id:
        if not any(u.get("id") == org_unit_id for u in org_units):
            org_unit_id = ""
    positions.append({"id": pos_id, "title": title.strip(), "org_unit_id": org_unit_id or None})
    _save_json(POSITIONS_FILE, positions)
    return RedirectResponse(url="/settings/positions", status_code=303)


# -------------------------
# Edit endpoints (Org Units)
# -------------------------
@router.get("/org-units/{unit_id}/edit", response_class=HTMLResponse)
async def org_units_edit(request: Request, unit_id: str):
    org_units = _load_json(ORG_UNITS_FILE)
    unit = next((u for u in org_units if u.get("id") == unit_id), None)
    if not unit:
        return HTMLResponse("Org Unit not found", status_code=404)
    tpl = templates_env.get_template("settings_org_unit_edit.html")
    return HTMLResponse(tpl.render(request=request, unit=unit, org_units=org_units))


@router.post("/org-units/{unit_id}/edit")
async def org_units_update(unit_id: str, name: str = Form(...), parent_id: str = Form("")):
    org_units = _load_json(ORG_UNITS_FILE)
    unit = next((u for u in org_units if u.get("id") == unit_id), None)
    if not unit:
        return HTMLResponse("Org Unit not found", status_code=404)
    # Prevent self-parenting; basic validation only
    parent_val = parent_id if (parent_id and parent_id != unit_id) else None
    unit["name"] = name.strip()
    unit["parent_id"] = parent_val
    _save_json(ORG_UNITS_FILE, org_units)
    return RedirectResponse(url="/settings/org-units", status_code=303)


# ----------------------
# Edit endpoints (Positions)
# ----------------------
@router.get("/positions/{pos_id}/edit", response_class=HTMLResponse)
async def positions_edit(request: Request, pos_id: str):
    positions = _load_json(POSITIONS_FILE)
    org_units = _load_json(ORG_UNITS_FILE)
    pos = next((p for p in positions if p.get("id") == pos_id), None)
    if not pos:
        return HTMLResponse("Position not found", status_code=404)
    tpl = templates_env.get_template("settings_position_edit.html")
    return HTMLResponse(tpl.render(request=request, position=pos, org_units=org_units))


@router.post("/positions/{pos_id}/edit")
async def positions_update(pos_id: str, title: str = Form(...), org_unit_id: str = Form("")):
    positions = _load_json(POSITIONS_FILE)
    org_units = _load_json(ORG_UNITS_FILE)
    pos = next((p for p in positions if p.get("id") == pos_id), None)
    if not pos:
        return HTMLResponse("Position not found", status_code=404)
    pos["title"] = title.strip()
    pos["org_unit_id"] = org_unit_id if any(u.get("id") == org_unit_id for u in org_units) else None
    _save_json(POSITIONS_FILE, positions)
    return RedirectResponse(url="/settings/positions", status_code=303)

# -----------------------------
# Delete endpoints (Org Units)
# -----------------------------
@router.get("/org-units/{unit_id}/delete", response_class=HTMLResponse)
async def org_units_delete_confirm(request: Request, unit_id: str):
    denied = require_roles(request, ["admin", "hr"])
    if denied:
        return denied
    org_units = _load_json(ORG_UNITS_FILE)
    positions = _load_json(POSITIONS_FILE)
    employees = _load_json(EMPLOYEES_FILE)
    unit = next((u for u in org_units if u.get("id") == unit_id), None)
    if not unit:
        return HTMLResponse("Org Unit not found", status_code=404)
    child_count = sum(1 for u in org_units if u.get("parent_id") == unit_id)
    pos_count = sum(1 for p in positions if p.get("org_unit_id") == unit_id)
    emp_count = sum(1 for e in employees if e.get("org_unit_id") == unit_id)
    blocked = any([child_count, pos_count, emp_count])
    tpl = templates_env.get_template("settings_org_unit_delete.html")
    return HTMLResponse(
        tpl.render(
            request=request,
            unit=unit,
            child_count=child_count,
            pos_count=pos_count,
            emp_count=emp_count,
            blocked=blocked,
        )
    )


@router.post("/org-units/{unit_id}/delete")
async def org_units_delete(request: Request, unit_id: str):
    denied = require_roles(request, ["admin", "hr"])
    if denied:
        return denied
    org_units = _load_json(ORG_UNITS_FILE)
    positions = _load_json(POSITIONS_FILE)
    employees = _load_json(EMPLOYEES_FILE)
    unit = next((u for u in org_units if u.get("id") == unit_id), None)
    if not unit:
        return HTMLResponse("Org Unit not found", status_code=404)
    # Safeguards: block if has children, positions, or employees referencing
    if any(u.get("parent_id") == unit_id for u in org_units):
        return RedirectResponse(url=f"/settings/org-units/{unit_id}/delete", status_code=303)
    if any(p.get("org_unit_id") == unit_id for p in positions):
        return RedirectResponse(url=f"/settings/org-units/{unit_id}/delete", status_code=303)
    if any(e.get("org_unit_id") == unit_id for e in employees):
        return RedirectResponse(url=f"/settings/org-units/{unit_id}/delete", status_code=303)
    org_units = [u for u in org_units if u.get("id") != unit_id]
    _save_json(ORG_UNITS_FILE, org_units)
    return RedirectResponse(url="/settings/org-units", status_code=303)


# ---------------------------
# Delete endpoints (Positions)
# ---------------------------
@router.get("/positions/{pos_id}/delete", response_class=HTMLResponse)
async def positions_delete_confirm(request: Request, pos_id: str):
    denied = require_roles(request, ["admin", "hr"])
    if denied:
        return denied
    positions = _load_json(POSITIONS_FILE)
    employees = _load_json(EMPLOYEES_FILE)
    pos = next((p for p in positions if p.get("id") == pos_id), None)
    if not pos:
        return HTMLResponse("Position not found", status_code=404)
    emp_count = sum(1 for e in employees if e.get("position_id") == pos_id)
    blocked = emp_count > 0
    tpl = templates_env.get_template("settings_position_delete.html")
    return HTMLResponse(
        tpl.render(request=request, position=pos, emp_count=emp_count, blocked=blocked)
    )


@router.post("/positions/{pos_id}/delete")
async def positions_delete(request: Request, pos_id: str):
    denied = require_roles(request, ["admin", "hr"])
    if denied:
        return denied
    positions = _load_json(POSITIONS_FILE)
    employees = _load_json(EMPLOYEES_FILE)
    pos = next((p for p in positions if p.get("id") == pos_id), None)
    if not pos:
        return HTMLResponse("Position not found", status_code=404)
    # Safeguard: block if any employees reference this position
    if any(e.get("position_id") == pos_id for e in employees):
        return RedirectResponse(url=f"/settings/positions/{pos_id}/delete", status_code=303)
    positions = [p for p in positions if p.get("id") != pos_id]
    _save_json(POSITIONS_FILE, positions)
    return RedirectResponse(url="/settings/positions", status_code=303)
def load_company() -> dict:
    try:
        data = json.loads(COMPANY_FILE.read_text(encoding="utf-8") or "{}")
    except Exception:
        data = {}
    # Defaults suitable for ZATCA Phase 1 testing
    if not data.get("name"):
        data["name"] = "Matrix ERP Demo Co"
    if not data.get("vat_number"):
        data["vat_number"] = "310000000000000"
    if not data.get("country_code"):
        data["country_code"] = "SA"
    # MRP planning defaults
    if not data.get("mrp_make_lead_days"):
        data["mrp_make_lead_days"] = 3
    if not data.get("mrp_buy_lead_days"):
        data["mrp_buy_lead_days"] = 7
    return data


def save_company(company: dict) -> None:
    COMPANY_FILE.write_text(json.dumps(company, ensure_ascii=False, indent=2), encoding="utf-8")


@router.get("/company", response_class=HTMLResponse)
async def company_settings(request: Request):
    company = load_company()
    tpl = templates_env.get_template("settings_company.html")
    return HTMLResponse(tpl.render(request=request, company=company))


@router.post("/company")
async def company_settings_save(
    name: str = Form(""),
    vat_number: str = Form(""),
    country_code: str = Form(""),
    mrp_make_lead_days: int = Form(3),
    mrp_buy_lead_days: int = Form(7),
):
    company = {
        "name": name.strip(),
        "vat_number": vat_number.strip(),
        "country_code": country_code.strip().upper() if country_code else "",
        "mrp_make_lead_days": int(mrp_make_lead_days) if mrp_make_lead_days is not None else 3,
        "mrp_buy_lead_days": int(mrp_buy_lead_days) if mrp_buy_lead_days is not None else 7,
    }
    save_company(company)
    return RedirectResponse(url="/settings/company", status_code=303)