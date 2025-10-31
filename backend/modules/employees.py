from __future__ import annotations

import json
from pathlib import Path
from datetime import date, datetime
from typing import List, Dict

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse
from starlette.responses import RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape


templates_env = Environment(
    loader=FileSystemLoader("backend/templates"),
    autoescape=select_autoescape(["html", "xml"]),
)

router = APIRouter(prefix="/employees", tags=["Employees"])

DATA_DIR = Path("backend/data")
EMPLOYEES_FILE = DATA_DIR / "employees.json"
ORG_UNITS_FILE = DATA_DIR / "org_units.json"
POSITIONS_FILE = DATA_DIR / "positions.json"
DATA_DIR.mkdir(parents=True, exist_ok=True)
if not EMPLOYEES_FILE.exists():
    EMPLOYEES_FILE.write_text("[]", encoding="utf-8")
for f in [ORG_UNITS_FILE, POSITIONS_FILE]:
    if not f.exists():
        f.write_text("[]", encoding="utf-8")


def _load_json(path: Path) -> list | dict:
    try:
        return json.loads(path.read_text(encoding="utf-8") or "[]")
    except Exception:
        return []


def load_employees() -> List[dict]:
    data = _load_json(EMPLOYEES_FILE)
    return data if isinstance(data, list) else []


def save_employees(items: List[dict]) -> None:
    EMPLOYEES_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


# --- Saudi Labour Law Helpers (simplified) ---

def years_of_service(hire_date: str, as_of: date | None = None) -> float:
    try:
        d = datetime.fromisoformat(hire_date).date()
    except Exception:
        return 0.0
    asof = as_of or date.today()
    return max(0.0, (asof - d).days / 365.0)


def annual_leave_days(hire_date: str) -> int:
    # 21 days per year, increases to 30 after 5 years
    yos = years_of_service(hire_date)
    return 30 if yos >= 5.0 else 21


def overtime_hour_rate(base_salary: float) -> float:
    # Assume 48 hours per week, 4.33 weeks/month; hourly = salary / (48*4.33)
    try:
        hourly = float(base_salary) / (48.0 * 4.33)
    except Exception:
        hourly = 0.0
    return hourly


def compute_overtime_pay(base_salary: float, hours: float, day_type: str = "normal") -> float:
    # Normal days: 1.5x; official holiday/rest day: 2.0x (simplified)
    rate = overtime_hour_rate(base_salary)
    multiplier = 1.5 if day_type == "normal" else 2.0
    return round(rate * multiplier * float(hours), 2)


def compute_eos_benefit(base_salary: float, hire_date: str, separation_date: str, reason: str = "termination") -> float:
    # End of Service Benefit (simplified per KSA rules)
    # Termination: 0.5 month per year for first 5 years, 1 month per year thereafter
    # Resignation: none <2 years; 1/3 between 2-5; 2/3 between 5-10; full >=10
    try:
        start = datetime.fromisoformat(hire_date).date()
        end = datetime.fromisoformat(separation_date).date()
    except Exception:
        return 0.0
    yos = max(0.0, (end - start).days / 365.0)
    monthly = float(base_salary)
    if reason == "termination":
        if yos <= 0:
            return 0.0
        first = min(yos, 5.0) * (0.5 * monthly)
        remaining = max(0.0, yos - 5.0) * (1.0 * monthly)
        return round(first + remaining, 2)
    else:  # resignation
        if yos < 2.0:
            return 0.0
        # Full benefit magnitude
        full_first = min(yos, 5.0) * (0.5 * monthly)
        full_rem = max(0.0, yos - 5.0) * (1.0 * monthly)
        full_benefit = full_first + full_rem
        if 2.0 <= yos < 5.0:
            return round(full_benefit * (1.0 / 3.0), 2)
        elif 5.0 <= yos < 10.0:
            return round(full_benefit * (2.0 / 3.0), 2)
        else:
            return round(full_benefit, 2)


# --- Routes ---

@router.get("/", response_class=HTMLResponse)
async def employees_list(request: Request, org_unit_id: str | None = None, expiry: str | None = None):
    emps = load_employees()
    def _days_until(dstr: str) -> int | None:
        try:
            d = datetime.strptime(dstr.strip(), "%Y-%m-%d").date()
            return (d - date.today()).days
        except Exception:
            return None
    def _expiry_status(dstr: str, threshold: int = 60) -> dict:
        days = _days_until(dstr)
        if days is None:
            return {"status": "unknown", "days": None}
        if days < 0:
            return {"status": "expired", "days": days}
        if days <= threshold:
            return {"status": "soon", "days": days}
        return {"status": "ok", "days": days}
    for e in emps:
        e["_iqama_alert"] = _expiry_status(e.get("iqama_expiry", ""))
        e["_passport_alert"] = _expiry_status(e.get("passport_expiry", ""))
    # Optional filtering by org unit and iqama expiry status
    if org_unit_id:
        emps = [e for e in emps if (e.get("org_unit_id") or "") == org_unit_id]
    if expiry in {"expired", "soon", "ok"}:
        emps = [e for e in emps if (e.get("_iqama_alert", {}).get("status") == expiry)]
    # Load org units for filter dropdown
    try:
        org_units = json.loads(ORG_UNITS_FILE.read_text(encoding="utf-8"))
    except Exception:
        org_units = []
    tpl = templates_env.get_template("employees_list.html")
    return HTMLResponse(tpl.render(request=request, employees=emps, org_units=org_units, selected_org_unit=org_unit_id or "", selected_expiry=expiry or ""))


@router.get("/new", response_class=HTMLResponse)
async def employees_new(request: Request):
    tpl = templates_env.get_template("employees_new.html")
    return HTMLResponse(tpl.render(request=request))


@router.post("/")
async def employees_create(
    emp_id: str = Form(""),
    name: str = Form(""),
    nationality: str = Form(""),
    hire_date: str = Form(""),
    base_salary: float = Form(0.0),
    hra: float = Form(0.0),
    transport: float = Form(0.0),
    contract_type: str = Form("indefinite"),
    monthly_paid: bool = Form(True),
    iqama_number: str = Form(""),
    iqama_expiry: str = Form(""),
    passport_number: str = Form(""),
    passport_expiry: str = Form(""),
):
    emps = load_employees()
    if not any(e.get("emp_id") == emp_id for e in emps):
        # Server-side enforce allowance calculation
        computed_hra = round(float(base_salary) * 0.25, 2)
        computed_transport = round(float(base_salary) * 0.10, 2)
        emps.append({
            "emp_id": emp_id.strip(),
            "name": name.strip(),
            "nationality": nationality.strip(),
            "hire_date": hire_date.strip(),
            "base_salary": float(base_salary),
            "hra": computed_hra,
            "transport": computed_transport,
            "contract_type": contract_type,
            "monthly_paid": bool(monthly_paid),
            "org_unit_id": None,
            "position_id": None,
            "iqama_number": iqama_number.strip(),
            "iqama_expiry": iqama_expiry.strip(),
            "passport_number": passport_number.strip(),
            "passport_expiry": passport_expiry.strip(),
        })
        save_employees(emps)
    return RedirectResponse(url="/employees", status_code=303)


@router.get("/{emp_id}", response_class=HTMLResponse)
async def employees_detail(request: Request, emp_id: str):
    emp = next((e for e in load_employees() if e.get("emp_id") == emp_id), None)
    if not emp:
        return HTMLResponse("Employee not found", status_code=404)
    org_units = _load_json(ORG_UNITS_FILE)
    positions = _load_json(POSITIONS_FILE)
    # Identity expiry alerts
    def _days_until(dstr: str) -> int | None:
        try:
            d = datetime.strptime(dstr.strip(), "%Y-%m-%d").date()
            return (d - date.today()).days
        except Exception:
            return None
    def _expiry_status(dstr: str, threshold: int = 60) -> dict:
        days = _days_until(dstr)
        if days is None:
            return {"status": "unknown", "days": None}
        if days < 0:
            return {"status": "expired", "days": days}
        if days <= threshold:
            return {"status": "soon", "days": days}
        return {"status": "ok", "days": days}
    summary = {
        "years_of_service": round(years_of_service(emp.get("hire_date", "")), 2),
        "annual_leave_days": annual_leave_days(emp.get("hire_date", "")),
        "overtime_hour_rate": round(overtime_hour_rate(float(emp.get("base_salary", 0.0))), 2),
        "notice_period_days": 60 if emp.get("monthly_paid") else 30,
        "max_weekly_hours": 48,
        "max_weekly_hours_ramadan": 36,
    }
    tpl = templates_env.get_template("employees_detail.html")
    iqama_alert = _expiry_status(emp.get("iqama_expiry", ""))
    passport_alert = _expiry_status(emp.get("passport_expiry", ""))
    return HTMLResponse(tpl.render(request=request, emp=emp, summary=summary, org_units=org_units, positions=positions, iqama_alert=iqama_alert, passport_alert=passport_alert))


@router.post("/{emp_id}/eos")
async def employees_eos(emp_id: str, separation_date: str = Form(""), reason: str = Form("termination")):
    emp = next((e for e in load_employees() if e.get("emp_id") == emp_id), None)
    if not emp:
        return HTMLResponse("Employee not found", status_code=404)
    eos = compute_eos_benefit(float(emp.get("base_salary", 0.0)), emp.get("hire_date", ""), separation_date, reason)
    return RedirectResponse(url=f"/employees/{emp_id}?eos={eos}&reason={reason}&sep={separation_date}", status_code=303)


@router.post("/{emp_id}/overtime")
async def employees_overtime(emp_id: str, hours: float = Form(0.0), day_type: str = Form("normal")):
    emp = next((e for e in load_employees() if e.get("emp_id") == emp_id), None)
    if not emp:
        return HTMLResponse("Employee not found", status_code=404)
    pay = compute_overtime_pay(float(emp.get("base_salary", 0.0)), float(hours), day_type)
    return RedirectResponse(url=f"/employees/{emp_id}?otpay={pay}&hours={hours}&day_type={day_type}", status_code=303)


@router.post("/{emp_id}/assign_position")
async def employees_assign_position(emp_id: str, org_unit_id: str = Form(""), position_id: str = Form("")):
    emps = load_employees()
    org_units = _load_json(ORG_UNITS_FILE)
    positions = _load_json(POSITIONS_FILE)
    emp = next((e for e in emps if e.get("emp_id") == emp_id), None)
    if not emp:
        return HTMLResponse("Employee not found", status_code=404)
    # Validate IDs
    ou_valid = org_unit_id and any(u.get("id") == org_unit_id for u in org_units)
    pos_valid = position_id and any(p.get("id") == position_id for p in positions)
    emp["org_unit_id"] = org_unit_id if ou_valid else None
    emp["position_id"] = position_id if pos_valid else None
    save_employees(emps)
    return RedirectResponse(url=f"/employees/{emp_id}", status_code=303)


@router.post("/{emp_id}/identity")
async def employees_update_identity(
    request: Request,
    emp_id: str,
    iqama_number: str = Form(""),
    iqama_expiry: str = Form(""),
    passport_number: str = Form(""),
    passport_expiry: str = Form(""),
):
    denied = require_roles(request, ["admin", "hr"])
    if denied:
        return denied
    emps = load_employees()
    emp = next((e for e in emps if e.get("emp_id") == emp_id), None)
    if not emp:
        return HTMLResponse("Employee not found", status_code=404)
    emp["iqama_number"] = iqama_number.strip()
    emp["iqama_expiry"] = iqama_expiry.strip()
    emp["passport_number"] = passport_number.strip()
    emp["passport_expiry"] = passport_expiry.strip()
    save_employees(emps)
    return RedirectResponse(url=f"/employees/{emp_id}", status_code=303)
from .auth import require_roles