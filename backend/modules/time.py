from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, date, timedelta
from typing import List, Dict

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse
from starlette.responses import RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

try:
    # For employee dropdowns
    from .employees import load_employees
except Exception:
    def load_employees() -> List[dict]:
        return []


templates_env = Environment(
    loader=FileSystemLoader("backend/templates"),
    autoescape=select_autoescape(["html", "xml"]),
)

router = APIRouter(prefix="/time", tags=["Time Management"])

DATA_DIR = Path("backend/data")
SHIFTS_FILE = DATA_DIR / "time_shifts.json"
ATTENDANCE_FILE = DATA_DIR / "time_attendance.json"
TIMESHEETS_FILE = DATA_DIR / "time_timesheets.json"
OVERTIME_FILE = DATA_DIR / "time_overtime.json"
LEAVE_FILE = DATA_DIR / "time_leave.json"
DATA_DIR.mkdir(parents=True, exist_ok=True)
for f in [SHIFTS_FILE, ATTENDANCE_FILE, TIMESHEETS_FILE, OVERTIME_FILE, LEAVE_FILE]:
    if not f.exists():
        f.write_text("[]", encoding="utf-8")


def _load_json(path: Path) -> list | dict:
    try:
        txt = path.read_text(encoding="utf-8")
        return json.loads(txt or "[]")
    except Exception:
        return []


def _save_json(path: Path, data: list | dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# --- Leave policy (Saudi labor law, simplified) ---
LEAVE_TYPES: List[dict] = [
    {"code": "annual", "name": "Annual", "max_days": 30, "paid": "full", "note": "21 days; 30 after 5 years of service"},
    {"code": "sick", "name": "Sick", "max_days": 120, "paid": "mixed", "note": "30 days full, next 60 at 75%, next 30 unpaid"},
    {"code": "maternity", "name": "Maternity", "max_days": 70, "paid": "varies", "note": "10 weeks; pay depends on service duration"},
    {"code": "paternity", "name": "Paternity", "max_days": 3, "paid": "full", "note": "3 days"},
    {"code": "marriage", "name": "Marriage", "max_days": 5, "paid": "full", "note": "5 days"},
    {"code": "bereavement", "name": "Bereavement", "max_days": 5, "paid": "full", "note": "5 days for close relatives"},
    {"code": "hajj", "name": "Hajj", "max_days": 10, "paid": "varies", "note": "Once after two years of service"},
    {"code": "unpaid", "name": "Unpaid", "max_days": 365, "paid": "none", "note": "Unpaid leave by agreement"},
]

def _find_leave_type(code: str) -> dict | None:
    for t in LEAVE_TYPES:
        if t.get("code") == code:
            return t
    return None


# --- Shifts ---
@router.get("/shifts", response_class=HTMLResponse)
async def time_shifts(request: Request):
    shifts = _load_json(SHIFTS_FILE)
    tpl = templates_env.get_template("time_shifts.html")
    return HTMLResponse(tpl.render(request=request, shifts=shifts))


@router.get("/shifts/new", response_class=HTMLResponse)
async def time_shift_new(request: Request):
    tpl = templates_env.get_template("time_shift_new.html")
    return HTMLResponse(tpl.render(request=request))


@router.post("/shifts")
async def time_shift_create(
    name: str = Form("General"),
    start_time: str = Form("09:00"),
    end_time: str = Form("18:00"),
    days: str = Form("Mon,Tue,Wed,Thu,Fri"),
):
    shifts = _load_json(SHIFTS_FILE)
    shifts.append({
        "name": name.strip(),
        "start": start_time.strip(),
        "end": end_time.strip(),
        "days": [d.strip() for d in days.split(',') if d.strip()],
    })
    _save_json(SHIFTS_FILE, shifts)
    return RedirectResponse(url="/time/shifts", status_code=303)


# --- Attendance ---
def _today_str() -> str:
    return date.today().isoformat()


@router.get("/attendance", response_class=HTMLResponse)
async def time_attendance(request: Request):
    employees = load_employees()
    entries = _load_json(ATTENDANCE_FILE)
    tpl = templates_env.get_template("time_attendance.html")
    return HTMLResponse(tpl.render(request=request, employees=employees, entries=entries, today=_today_str()))


def _find_open_attendance(emp_id: str, on_date: str) -> dict | None:
    for e in _load_json(ATTENDANCE_FILE):
        if e.get("emp_id") == emp_id and e.get("date") == on_date and not e.get("out"):
            return e
    return None


@router.post("/attendance/checkin")
async def attendance_checkin(emp_id: str = Form(""), when: str = Form(""), shift: str = Form("")):
    # when format HH:MM; default now local time
    now = datetime.now()
    check_time = when.strip() or now.strftime("%H:%M")
    entries = _load_json(ATTENDANCE_FILE)
    entries.append({
        "emp_id": emp_id.strip(),
        "date": _today_str(),
        "in": check_time,
        "out": "",
        "hours": 0.0,
        "shift": shift.strip(),
    })
    _save_json(ATTENDANCE_FILE, entries)
    return RedirectResponse(url="/time/attendance", status_code=303)


@router.post("/attendance/checkout")
async def attendance_checkout(emp_id: str = Form(""), when: str = Form("")):
    # when format HH:MM; default now
    now = datetime.now()
    out_time = when.strip() or now.strftime("%H:%M")
    entries = _load_json(ATTENDANCE_FILE)
    # find open entry
    open_entry = None
    for e in entries:
        if e.get("emp_id") == emp_id and e.get("date") == _today_str() and not e.get("out"):
            open_entry = e
            break
    if open_entry:
        open_entry["out"] = out_time
        # compute hours
        try:
            dt_in = datetime.strptime(f"{open_entry['date']} {open_entry['in']}", "%Y-%m-%d %H:%M")
            dt_out = datetime.strptime(f"{open_entry['date']} {open_entry['out']}", "%Y-%m-%d %H:%M")
            delta = dt_out - dt_in
            open_entry["hours"] = round(delta.total_seconds() / 3600.0, 2)
        except Exception:
            open_entry["hours"] = 0.0
        _save_json(ATTENDANCE_FILE, entries)
    return RedirectResponse(url="/time/attendance", status_code=303)


# --- Timesheets ---
def _week_start(d: date) -> date:
    # week starts on Monday
    return d - timedelta(days=d.weekday())


@router.get("/timesheets", response_class=HTMLResponse)
async def time_timesheets(request: Request, week: str | None = None):
    # week param is ISO date of any day in the week
    today = date.today()
    target = datetime.strptime(week, "%Y-%m-%d").date() if week else today
    ws = _week_start(target).isoformat()
    # aggregate hours by employee for the week
    entries = _load_json(ATTENDANCE_FILE)
    totals: Dict[str, float] = {}
    for e in entries:
        try:
            edate = datetime.strptime(e.get("date", ""), "%Y-%m-%d").date()
        except Exception:
            continue
        if _week_start(edate).isoformat() == ws:
            emp = e.get("emp_id", "")
            totals[emp] = round(totals.get(emp, 0.0) + float(e.get("hours", 0.0)), 2)
    employees = load_employees()
    rows = []
    for emp in employees:
        rows.append({
            "emp_id": emp.get("emp_id"),
            "name": emp.get("name"),
            "week_start": ws,
            "hours": totals.get(emp.get("emp_id"), 0.0),
        })
    tpl = templates_env.get_template("time_timesheets.html")
    return HTMLResponse(tpl.render(request=request, rows=rows, week_start=ws))


# --- Overtime ---
@router.get("/overtime", response_class=HTMLResponse)
async def time_overtime(request: Request):
    employees = load_employees()
    items = _load_json(OVERTIME_FILE)
    tpl = templates_env.get_template("time_overtime.html")
    return HTMLResponse(tpl.render(request=request, employees=employees, items=items))


def _next_ot_id(items: List[dict]) -> str:
    prefix = "OT"
    num = len(items) + 1
    return f"{prefix}-{date.today().year}-{num:04d}"


@router.post("/overtime")
async def overtime_create(emp_id: str = Form(""), hours: float = Form(0.0), reason: str = Form("")):
    items = _load_json(OVERTIME_FILE)
    ot = {
        "id": _next_ot_id(items),
        "emp_id": emp_id.strip(),
        "date": _today_str(),
        "hours": float(hours),
        "status": "pending",
        "reason": reason.strip(),
    }
    items.append(ot)
    _save_json(OVERTIME_FILE, items)
    return RedirectResponse(url="/time/overtime", status_code=303)


@router.post("/overtime/{ot_id}/approve")
async def overtime_approve(ot_id: str):
    items = _load_json(OVERTIME_FILE)
    for it in items:
        if it.get("id") == ot_id:
            it["status"] = "approved"
            break
    _save_json(OVERTIME_FILE, items)
    return RedirectResponse(url="/time/overtime", status_code=303)


# --- Leave Requests ---
@router.get("/leave", response_class=HTMLResponse)
async def time_leave(request: Request):
    employees = load_employees()
    items = _load_json(LEAVE_FILE)
    tpl = templates_env.get_template("time_leave.html")
    return HTMLResponse(tpl.render(request=request, employees=employees, items=items, leave_types=LEAVE_TYPES, error_message=None))


def _next_leave_id(items: List[dict]) -> str:
    prefix = "LV"
    num = len(items) + 1
    return f"{prefix}-{date.today().year}-{num:04d}"


@router.post("/leave")
async def leave_create(emp_id: str = Form(""), leave_type: str = Form("annual"), start_date: str = Form(""), end_date: str = Form(""), reason: str = Form("")):
    # Compute days inclusive
    try:
        sd = datetime.strptime(start_date.strip(), "%Y-%m-%d").date()
        ed = datetime.strptime(end_date.strip(), "%Y-%m-%d").date()
        days = (ed - sd).days + 1
        days = max(days, 0)
    except Exception:
        days = 0
    items = _load_json(LEAVE_FILE)
    lt = _find_leave_type(leave_type.strip()) or {"code": leave_type.strip(), "name": leave_type.strip().title(), "max_days": days or 0, "paid": "", "note": ""}
    exceeds = bool(days and lt.get("max_days") and days > int(lt.get("max_days", 0)))

    # Block creation if exceeding policy; re-render with error
    if exceeds:
        employees = load_employees()
        tpl = templates_env.get_template("time_leave.html")
        msg = f"Requested {days} day(s) exceeds policy max of {lt.get('max_days')} for {lt.get('name')}"
        return HTMLResponse(tpl.render(request=request, employees=employees, items=items, leave_types=LEAVE_TYPES, error_message=msg))
    req = {
        "id": _next_leave_id(items),
        "emp_id": emp_id.strip(),
        "type": lt.get("code"),
        "type_name": lt.get("name"),
        "start": start_date.strip(),
        "end": end_date.strip(),
        "days": days,
        "status": "pending",
        "reason": reason.strip(),
        "policy_max_days": lt.get("max_days"),
        "policy_paid": lt.get("paid"),
        "policy_note": lt.get("note"),
        "exceeds_policy": exceeds,
    }
    items.append(req)
    _save_json(LEAVE_FILE, items)
    return RedirectResponse(url="/time/leave", status_code=303)


@router.post("/leave/{leave_id}/approve")
async def leave_approve(leave_id: str):
    items = _load_json(LEAVE_FILE)
    for it in items:
        if it.get("id") == leave_id:
            it["status"] = "approved"
            break
    _save_json(LEAVE_FILE, items)
    return RedirectResponse(url="/time/leave", status_code=303)