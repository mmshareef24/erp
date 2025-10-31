from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse
from starlette.responses import RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..db import SessionLocal, Customer, Product, default_vat_rate

templates_env = Environment(
    loader=FileSystemLoader("backend/templates"),
    autoescape=select_autoescape(["html", "xml"]),
)

router = APIRouter(prefix="/catalogs", tags=["Catalogs"])


# Customers
@router.get("/customers", response_class=HTMLResponse)
async def customers_list(request: Request):
    with SessionLocal() as db:
        customers = db.query(Customer).order_by(Customer.id.desc()).all()
    tpl = templates_env.get_template("customers_list.html")
    return HTMLResponse(tpl.render(request=request, customers=customers))


@router.get("/customers/new", response_class=HTMLResponse)
async def customers_new(request: Request):
    countries = [
        {"code": "US", "name": "United States"},
        {"code": "GB", "name": "United Kingdom"},
        {"code": "DE", "name": "Germany"},
        {"code": "FR", "name": "France"},
        {"code": "AE", "name": "United Arab Emirates"},
        {"code": "SA", "name": "Saudi Arabia"},
        {"code": "IN", "name": "India"},
        {"code": "PK", "name": "Pakistan"},
        {"code": "CA", "name": "Canada"},
        {"code": "AU", "name": "Australia"},
    ]
    tpl = templates_env.get_template("customers_new.html")
    return HTMLResponse(tpl.render(request=request, countries=countries))


@router.post("/customers")
async def customers_create(
    name: str = Form(...),
    email: str = Form(""),
    address1: str = Form(""),
    address2: str = Form(""),
    city: str = Form(""),
    state: str = Form(""),
    postal_code: str = Form(""),
    country: str = Form(""),
    vat_number: str = Form(""),
    vat_rate: float = Form(None),
):
    # compute defaults
    vat = vat_rate if vat_rate is not None else default_vat_rate(country)
    ar_account = f"AR-{country.upper()}" if country else "AR"
    with SessionLocal() as db:
        c = Customer(
            name=name,
            email=email,
            address1=address1,
            address2=address2,
            city=city,
            state=state,
            postal_code=postal_code,
            country_code=country or None,
            vat_number=vat_number or None,
            vat_rate=vat,
            ar_account=ar_account,
        )
        db.add(c)
        db.commit()
    return RedirectResponse(url="/catalogs/customers", status_code=303)


# Products
@router.get("/products", response_class=HTMLResponse)
async def products_list(request: Request):
    with SessionLocal() as db:
        products = db.query(Product).order_by(Product.id.desc()).all()
    tpl = templates_env.get_template("products_list.html")
    return HTMLResponse(tpl.render(request=request, products=products))


@router.get("/products/new", response_class=HTMLResponse)
async def products_new(request: Request):
    tpl = templates_env.get_template("products_new.html")
    return HTMLResponse(tpl.render(request=request))


@router.post("/products")
async def products_create(name: str = Form(...), price: float = Form(0.0)):
    with SessionLocal() as db:
        p = Product(name=name, price=price)
        db.add(p)
        db.commit()
    return RedirectResponse(url="/catalogs/products", status_code=303)