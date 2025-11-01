from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse
from starlette.responses import RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from urllib.parse import quote


# Jinja environment for Quality templates
templates_env = Environment(
    loader=FileSystemLoader("backend/templates"),
    autoescape=select_autoescape(["html", "xml"]),
)


router = APIRouter(prefix="/quality", tags=["Quality Assurance"])


# Simple JSON storage for Quality Assurance
DATA_DIR = Path("backend/data")
INSPECTIONS_FILE = DATA_DIR / "quality_inspections.json"
DEFECTS_FILE = DATA_DIR / "quality_defects.json"
REPORTS_FILE = DATA_DIR / "quality_test_reports.json"
COMPLIANCE_FILE = DATA_DIR / "quality_compliance_docs.json"
DATA_DIR.mkdir(parents=True, exist_ok=True)
for f in [INSPECTIONS_FILE, DEFECTS_FILE, REPORTS_FILE, COMPLIANCE_FILE]:
    if not f.exists():
        f.write_text("[]", encoding="utf-8")


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_json(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def _get_by_id(rows: list[dict], rid: str) -> dict | None:
    for r in rows:
        if r.get("id") == rid:
            return r
    return None

def _delete_by_id(rows: list[dict], rid: str) -> list[dict]:
    return [r for r in rows if r.get("id") != rid]

def _soft_delete(rows: list[dict], rid: str) -> None:
    item = _get_by_id(rows, rid)
    if item is not None:
        item["deleted_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

def _restore(rows: list[dict], rid: str) -> None:
    item = _get_by_id(rows, rid)
    if item is not None and item.get("deleted_at"):
        item.pop("deleted_at", None)


# --- QC Inspections ---
@router.get("/inspections", response_class=HTMLResponse)
async def inspections_list(request: Request, stage: str | None = None, show_deleted: int | None = None):
    rows = _load_json(INSPECTIONS_FILE)
    if not show_deleted:
        rows = [r for r in rows if not r.get("deleted_at")]
    if stage:
        rows = [r for r in rows if (r.get("stage") or "").lower() == stage.lower()]
    tpl = templates_env.get_template("quality_inspections.html")
    return HTMLResponse(tpl.render(request=request, rows=rows, selected_stage=stage or "", show_deleted=bool(show_deleted)))


@router.get("/inspections/new", response_class=HTMLResponse)
async def inspections_new(request: Request):
    tpl = templates_env.get_template("quality_inspection_new.html")
    return HTMLResponse(tpl.render(request=request))


@router.post("/inspections")
async def inspections_create(
    reference: str = Form(...),
    stage: str = Form(...),  # raw | in_process | final
    inspector: str = Form(""),
    criteria: str = Form(""),  # JSON string of criteria list
    status: str = Form("pending"),  # pending | pass | fail
    notes: str = Form(""),
):
    rows = _load_json(INSPECTIONS_FILE)
    try:
        crit_list = json.loads(criteria) if criteria else []
    except Exception:
        crit_list = []
    rows.append({
        "id": str(uuid4()),
        "reference": reference,
        "stage": stage,
        "inspector": inspector,
        "date": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "criteria": crit_list,
        "status": status,
        "notes": notes,
    })
    _save_json(INSPECTIONS_FILE, rows)
    return RedirectResponse(url="/quality/inspections", status_code=303)

@router.get("/inspections/{rid}/edit", response_class=HTMLResponse)
async def inspections_edit(request: Request, rid: str):
    rows = _load_json(INSPECTIONS_FILE)
    item = _get_by_id(rows, rid)
    tpl = templates_env.get_template("quality_inspection_new.html")
    # Reuse new form template; when item exists, prefill via context
    criteria_json = ""
    if item:
        try:
            criteria_json = json.dumps(item.get("criteria", []), ensure_ascii=False, indent=2)
        except Exception:
            criteria_json = "[]"
    return HTMLResponse(tpl.render(request=request, item=item, criteria_json=criteria_json))

@router.post("/inspections/{rid}")
async def inspections_update(
    rid: str,
    reference: str = Form(...),
    stage: str = Form(...),
    inspector: str = Form(""),
    criteria: str = Form(""),
    status: str = Form("pending"),
    notes: str = Form(""),
):
    rows = _load_json(INSPECTIONS_FILE)
    item = _get_by_id(rows, rid)
    if item:
        try:
            crit_list = json.loads(criteria) if criteria else []
        except Exception:
            crit_list = []
        item.update({
            "reference": reference,
            "stage": stage,
            "inspector": inspector,
            "criteria": crit_list,
            "status": status,
            "notes": notes,
        })
        _save_json(INSPECTIONS_FILE, rows)
    return RedirectResponse(url="/quality/inspections", status_code=303)

@router.post("/inspections/{rid}/delete")
async def inspections_delete(rid: str):
    rows = _load_json(INSPECTIONS_FILE)
    _soft_delete(rows, rid)
    _save_json(INSPECTIONS_FILE, rows)
    msg = quote("Inspection deleted. Undo?")
    undo = quote(f"/quality/inspections/{rid}/restore")
    return RedirectResponse(url=f"/quality/inspections?msg={msg}&undo={undo}", status_code=303)

@router.post("/inspections/{rid}/restore")
async def inspections_restore(rid: str):
    rows = _load_json(INSPECTIONS_FILE)
    _restore(rows, rid)
    _save_json(INSPECTIONS_FILE, rows)
    return RedirectResponse(url="/quality/inspections", status_code=303)


# --- Defect Logging ---
@router.get("/defects", response_class=HTMLResponse)
async def defects_list(request: Request, status: str | None = None, show_deleted: int | None = None):
    rows = _load_json(DEFECTS_FILE)
    if not show_deleted:
        rows = [r for r in rows if not r.get("deleted_at")]
    if status:
        rows = [r for r in rows if (r.get("status") or "").lower() == status.lower()]
    tpl = templates_env.get_template("quality_defects.html")
    return HTMLResponse(tpl.render(request=request, rows=rows, selected_status=status or "", show_deleted=bool(show_deleted)))


@router.get("/defects/new", response_class=HTMLResponse)
async def defects_new(request: Request):
    tpl = templates_env.get_template("quality_defect_new.html")
    return HTMLResponse(tpl.render(request=request))


@router.post("/defects")
async def defects_create(
    reference: str = Form(""),
    stage: str = Form(""),
    category: str = Form(""),
    severity: str = Form("minor"),  # minor | major | critical
    description: str = Form(""),
    actions: str = Form(""),
    status: str = Form("open"),
    nc_code: str = Form(""),
):
    rows = _load_json(DEFECTS_FILE)
    rows.append({
        "id": str(uuid4()),
        "reference": reference,
        "stage": stage,
        "category": category,
        "severity": severity,
        "description": description,
        "actions": actions,
        "status": status,
        "nc_code": nc_code,
        "date": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    })
    _save_json(DEFECTS_FILE, rows)
    return RedirectResponse(url="/quality/defects", status_code=303)

@router.get("/defects/{rid}/edit", response_class=HTMLResponse)
async def defects_edit(request: Request, rid: str):
    rows = _load_json(DEFECTS_FILE)
    item = _get_by_id(rows, rid)
    tpl = templates_env.get_template("quality_defect_new.html")
    return HTMLResponse(tpl.render(request=request, item=item))

@router.post("/defects/{rid}")
async def defects_update(
    rid: str,
    reference: str = Form(""),
    stage: str = Form(""),
    category: str = Form(""),
    severity: str = Form("minor"),
    description: str = Form(""),
    actions: str = Form(""),
    status: str = Form("open"),
    nc_code: str = Form(""),
):
    rows = _load_json(DEFECTS_FILE)
    item = _get_by_id(rows, rid)
    if item:
        item.update({
            "reference": reference,
            "stage": stage,
            "category": category,
            "severity": severity,
            "description": description,
            "actions": actions,
            "status": status,
            "nc_code": nc_code,
        })
        _save_json(DEFECTS_FILE, rows)
    return RedirectResponse(url="/quality/defects", status_code=303)

@router.post("/defects/{rid}/delete")
async def defects_delete(rid: str):
    rows = _load_json(DEFECTS_FILE)
    _soft_delete(rows, rid)
    _save_json(DEFECTS_FILE, rows)
    msg = quote("Defect deleted. Undo?")
    undo = quote(f"/quality/defects/{rid}/restore")
    return RedirectResponse(url=f"/quality/defects?msg={msg}&undo={undo}", status_code=303)

@router.post("/defects/{rid}/restore")
async def defects_restore(rid: str):
    rows = _load_json(DEFECTS_FILE)
    _restore(rows, rid)
    _save_json(DEFECTS_FILE, rows)
    return RedirectResponse(url="/quality/defects", status_code=303)


# --- Test Reports ---
@router.get("/reports", response_class=HTMLResponse)
async def reports_list(request: Request, rtype: str | None = None, show_deleted: int | None = None):
    rows = _load_json(REPORTS_FILE)
    if not show_deleted:
        rows = [r for r in rows if not r.get("deleted_at")]
    if rtype:
        rows = [r for r in rows if (r.get("type") or "").lower() == rtype.lower()]
    tpl = templates_env.get_template("quality_reports.html")
    return HTMLResponse(tpl.render(request=request, rows=rows, selected_type=rtype or "", show_deleted=bool(show_deleted)))


@router.get("/reports/new", response_class=HTMLResponse)
async def reports_new(request: Request):
    tpl = templates_env.get_template("quality_report_new.html")
    return HTMLResponse(tpl.render(request=request))


@router.post("/reports")
async def reports_create(
    reference: str = Form(""),
    type: str = Form("manual"),  # automated | manual
    results: str = Form(""),
    summary: str = Form(""),
):
    rows = _load_json(REPORTS_FILE)
    rows.append({
        "id": str(uuid4()),
        "reference": reference,
        "type": type,
        "results": results,
        "summary": summary,
        "date": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    })
    _save_json(REPORTS_FILE, rows)
    return RedirectResponse(url="/quality/reports", status_code=303)

@router.get("/reports/{rid}/edit", response_class=HTMLResponse)
async def reports_edit(request: Request, rid: str):
    rows = _load_json(REPORTS_FILE)
    item = _get_by_id(rows, rid)
    tpl = templates_env.get_template("quality_report_new.html")
    return HTMLResponse(tpl.render(request=request, item=item))

@router.get("/reports/{rid}/mtc-print", response_class=HTMLResponse)
async def reports_mtc_print(request: Request, rid: str):
    rows = _load_json(REPORTS_FILE)
    item = _get_by_id(rows, rid)
    # Parse results JSON if possible for tabular display
    results_obj = None
    if item:
        try:
            rtxt = item.get("results") or ""
            results_obj = json.loads(rtxt) if rtxt else None
        except Exception:
            results_obj = None
    # Load company info for header (may be empty)
    try:
        company = json.loads(Path("backend/data/company.json").read_text(encoding="utf-8"))
    except Exception:
        company = {}
    tpl = templates_env.get_template("quality_mtc_print.html")
    return HTMLResponse(tpl.render(request=request, item=item, company=company, results_obj=results_obj))

@router.post("/reports/{rid}")
async def reports_update(
    rid: str,
    reference: str = Form(""),
    type: str = Form("manual"),
    results: str = Form(""),
    summary: str = Form(""),
):
    rows = _load_json(REPORTS_FILE)
    item = _get_by_id(rows, rid)
    if item:
        item.update({
            "reference": reference,
            "type": type,
            "results": results,
            "summary": summary,
        })
        _save_json(REPORTS_FILE, rows)
    return RedirectResponse(url="/quality/reports", status_code=303)

@router.post("/reports/{rid}/delete")
async def reports_delete(rid: str):
    rows = _load_json(REPORTS_FILE)
    _soft_delete(rows, rid)
    _save_json(REPORTS_FILE, rows)
    msg = quote("Report deleted. Undo?")
    undo = quote(f"/quality/reports/{rid}/restore")
    return RedirectResponse(url=f"/quality/reports?msg={msg}&undo={undo}", status_code=303)

@router.post("/reports/{rid}/restore")
async def reports_restore(rid: str):
    rows = _load_json(REPORTS_FILE)
    _restore(rows, rid)
    _save_json(REPORTS_FILE, rows)
    return RedirectResponse(url="/quality/reports", status_code=303)


# --- Compliance Documents ---
@router.get("/compliance", response_class=HTMLResponse)
async def compliance_list(request: Request, status: str | None = None, show_deleted: int | None = None):
    rows = _load_json(COMPLIANCE_FILE)
    if not show_deleted:
        rows = [r for r in rows if not r.get("deleted_at")]
    if status:
        rows = [r for r in rows if (r.get("status") or "").lower() == status.lower()]
    tpl = templates_env.get_template("quality_compliance.html")
    return HTMLResponse(tpl.render(request=request, rows=rows, selected_status=status or "", show_deleted=bool(show_deleted)))


@router.get("/compliance/new", response_class=HTMLResponse)
async def compliance_new(request: Request):
    tpl = templates_env.get_template("quality_compliance_new.html")
    return HTMLResponse(tpl.render(request=request))


@router.post("/compliance")
async def compliance_create(
    doc_type: str = Form("ISO"),  # ISO | Safety | Regulatory
    title: str = Form(""),
    number: str = Form(""),
    issue_date: str = Form(""),
    expiry_date: str = Form(""),
    owner: str = Form(""),
    status: str = Form("active"),
):
    rows = _load_json(COMPLIANCE_FILE)
    rows.append({
        "id": str(uuid4()),
        "doc_type": doc_type,
        "title": title,
        "number": number,
        "issue_date": issue_date,
        "expiry_date": expiry_date,
        "owner": owner,
        "status": status,
        "created": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    })
    _save_json(COMPLIANCE_FILE, rows)
    return RedirectResponse(url="/quality/compliance", status_code=303)

@router.get("/compliance/{rid}/edit", response_class=HTMLResponse)
async def compliance_edit(request: Request, rid: str):
    rows = _load_json(COMPLIANCE_FILE)
    item = _get_by_id(rows, rid)
    tpl = templates_env.get_template("quality_compliance_new.html")
    return HTMLResponse(tpl.render(request=request, item=item))

@router.post("/compliance/{rid}")
async def compliance_update(
    rid: str,
    doc_type: str = Form("ISO"),
    title: str = Form(""),
    number: str = Form(""),
    issue_date: str = Form(""),
    expiry_date: str = Form(""),
    owner: str = Form(""),
    status: str = Form("active"),
):
    rows = _load_json(COMPLIANCE_FILE)
    item = _get_by_id(rows, rid)
    if item:
        item.update({
            "doc_type": doc_type,
            "title": title,
            "number": number,
            "issue_date": issue_date,
            "expiry_date": expiry_date,
            "owner": owner,
            "status": status,
        })
        _save_json(COMPLIANCE_FILE, rows)
    return RedirectResponse(url="/quality/compliance", status_code=303)

@router.post("/compliance/{rid}/delete")
async def compliance_delete(rid: str):
    rows = _load_json(COMPLIANCE_FILE)
    _soft_delete(rows, rid)
    _save_json(COMPLIANCE_FILE, rows)
    msg = quote("Compliance doc deleted. Undo?")
    undo = quote(f"/quality/compliance/{rid}/restore")
    return RedirectResponse(url=f"/quality/compliance?msg={msg}&undo={undo}", status_code=303)

@router.post("/compliance/{rid}/restore")
async def compliance_restore(rid: str):
    rows = _load_json(COMPLIANCE_FILE)
    _restore(rows, rid)
    _save_json(COMPLIANCE_FILE, rows)
    return RedirectResponse(url="/quality/compliance", status_code=303)