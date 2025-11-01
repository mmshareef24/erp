from __future__ import annotations

from fastapi import APIRouter, Request, Form
import uuid
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pathlib import Path
from datetime import datetime
import json


templates_env = Environment(
    loader=FileSystemLoader("backend/templates"),
    autoescape=select_autoescape(["html", "xml"]),
)

router = APIRouter(prefix="/hr", tags=["Human Resources"]) 

# --- Simple JSON persistence ---
DATA_DIR = Path("backend/data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
PAYROLL_RUNS_FILE = DATA_DIR / "payroll_runs.json"
JOBS_FILE = DATA_DIR / "jobs.json"


def _load_json(path: Path):
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_json(path: Path, data):
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


@router.get("/payroll", response_class=HTMLResponse)
async def payroll_page(request: Request):
    tpl = templates_env.get_template("hr_payroll.html")
    app_item = {
        "name": "Human Resources",
        "slug": "hr",
        "color": "#4a90e2",
        "description": "People, payroll, attendance, leave",
    }
    runs = _load_json(PAYROLL_RUNS_FILE)
    recent_runs = list(reversed(runs))[:5]
    return HTMLResponse(tpl.render(request=request, app_item=app_item, recent_runs=recent_runs))


@router.get("/recruitment", response_class=HTMLResponse)
async def recruitment_page(request: Request):
    tpl = templates_env.get_template("hr_recruitment.html")
    app_item = {
        "name": "Human Resources",
        "slug": "hr",
        "color": "#4a90e2",
        "description": "People, payroll, attendance, leave",
    }
    jobs = _load_json(JOBS_FILE)
    recent_jobs = list(reversed(jobs))[:5]
    return HTMLResponse(tpl.render(request=request, app_item=app_item, recent_jobs=recent_jobs))


@router.get("/payroll/run", response_class=HTMLResponse)
async def payroll_run(request: Request):
    tpl = templates_env.get_template("hr_payroll_run.html")
    app_item = {
        "name": "Human Resources",
        "slug": "hr",
        "color": "#4a90e2",
        "description": "People, payroll, attendance, leave",
    }
    return HTMLResponse(tpl.render(request=request, app_item=app_item))


@router.post("/payroll/run")
async def payroll_run_submit(
    period: str = Form(""),
    bonuses: str = Form("no"),
    deductions: str = Form("no"),
):
    items = _load_json(PAYROLL_RUNS_FILE)
    items.append({
        "id": str(uuid.uuid4()),
        "period": period.strip(),
        "bonuses": bonuses,
        "deductions": deductions,
        "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    })
    _save_json(PAYROLL_RUNS_FILE, items)
    return RedirectResponse(url=f"/hr/payroll?run=ok&period={period}", status_code=303)


@router.get("/payroll/runs", response_class=HTMLResponse)
async def payroll_runs_list(request: Request):
    runs = _load_json(PAYROLL_RUNS_FILE)
    tpl = templates_env.get_template("hr_payroll_runs.html")
    app_item = {
        "name": "Human Resources",
        "slug": "hr",
        "color": "#4a90e2",
        "description": "People, payroll, attendance, leave",
    }
    return HTMLResponse(tpl.render(request=request, app_item=app_item, runs=runs))


@router.get("/payroll/run/{index}", response_class=HTMLResponse)
async def payroll_run_detail(request: Request, index: int):
    runs = _load_json(PAYROLL_RUNS_FILE)
    item = runs[index] if 0 <= index < len(runs) else None
    tpl = templates_env.get_template("hr_payroll_run_detail.html")
    app_item = {
        "name": "Human Resources",
        "slug": "hr",
        "color": "#4a90e2",
        "description": "People, payroll, attendance, leave",
    }
    status = 200 if item is not None else 404
    return HTMLResponse(tpl.render(request=request, app_item=app_item, item=item, index=index), status_code=status)


@router.get("/payroll/run/id/{run_id}", response_class=HTMLResponse)
async def payroll_run_detail_by_id(request: Request, run_id: str):
    runs = _load_json(PAYROLL_RUNS_FILE)
    item = next((r for r in runs if r.get("id") == run_id), None)
    tpl = templates_env.get_template("hr_payroll_run_detail.html")
    app_item = {
        "name": "Human Resources",
        "slug": "hr",
        "color": "#4a90e2",
        "description": "People, payroll, attendance, leave",
    }
    status = 200 if item is not None else 404
    return HTMLResponse(tpl.render(request=request, app_item=app_item, item=item, index=None), status_code=status)


@router.get("/recruitment/new", response_class=HTMLResponse)
async def recruitment_new(request: Request):
    tpl = templates_env.get_template("hr_recruitment_new.html")
    app_item = {
        "name": "Human Resources",
        "slug": "hr",
        "color": "#4a90e2",
        "description": "People, payroll, attendance, leave",
    }
    return HTMLResponse(tpl.render(request=request, app_item=app_item))


@router.post("/jobs")
async def recruitment_job_create(
    title: str = Form(""),
    department: str = Form(""),
    location: str = Form(""),
):
    if not title.strip():
        return HTMLResponse("Title is required", status_code=400)
    items = _load_json(JOBS_FILE)
    items.append({
        "id": str(uuid.uuid4()),
        "title": title.strip(),
        "department": department.strip(),
        "location": location.strip(),
        "status": "open",
        "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    })
    _save_json(JOBS_FILE, items)
    return RedirectResponse(url=f"/hr/recruitment?created=1&title={title}", status_code=303)


@router.get("/jobs", response_class=HTMLResponse)
async def jobs_list(request: Request):
    jobs = _load_json(JOBS_FILE)
    tpl = templates_env.get_template("hr_jobs.html")
    app_item = {
        "name": "Human Resources",
        "slug": "hr",
        "color": "#4a90e2",
        "description": "People, payroll, attendance, leave",
    }
    return HTMLResponse(tpl.render(request=request, app_item=app_item, jobs=jobs))


@router.get("/jobs/{index}", response_class=HTMLResponse)
async def job_detail(request: Request, index: int):
    jobs = _load_json(JOBS_FILE)
    item = jobs[index] if 0 <= index < len(jobs) else None
    tpl = templates_env.get_template("hr_job_detail.html")
    app_item = {
        "name": "Human Resources",
        "slug": "hr",
        "color": "#4a90e2",
        "description": "People, payroll, attendance, leave",
    }
    status = 200 if item is not None else 404
    return HTMLResponse(tpl.render(request=request, app_item=app_item, item=item, index=index), status_code=status)


@router.get("/jobs/id/{job_id}", response_class=HTMLResponse)
async def job_detail_by_id(request: Request, job_id: str):
    jobs = _load_json(JOBS_FILE)
    item = next((j for j in jobs if j.get("id") == job_id), None)
    tpl = templates_env.get_template("hr_job_detail.html")
    app_item = {
        "name": "Human Resources",
        "slug": "hr",
        "color": "#4a90e2",
        "description": "People, payroll, attendance, leave",
    }
    status = 200 if item is not None else 404
    return HTMLResponse(tpl.render(request=request, app_item=app_item, item=item, index=None), status_code=status)