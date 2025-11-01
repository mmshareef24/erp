from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

router = APIRouter(prefix="/chart", tags=["chart"])

# Jinja2 templates setup
templates_env = Environment(
    loader=FileSystemLoader("backend/templates"),
    autoescape=select_autoescape(["html", "xml"]),
)


@router.get("/", response_class=HTMLResponse)
async def chart_overview(request: Request):
    """Chart overview page with analytics and reporting."""
    template = templates_env.get_template("chart_overview.html")
    html = template.render(request=request)
    return HTMLResponse(content=html, status_code=200)