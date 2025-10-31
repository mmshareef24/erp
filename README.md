Matrix ERP (Odoo-like Minimal Prototype)

Overview
- A lightweight, modular FastAPI application that mimics Odoo’s app launcher.
- Includes a simple apps registry, dashboard UI with tiles, and stub app pages.

Getting Started
1) Ensure Python 3.10+ is installed.
2) (Optional) Create and activate a virtualenv.
3) Install dependencies:
   pip install -r requirements.txt
4) Run the dev server:
   uvicorn backend.main:app --reload
5) Open the browser at:
   http://localhost:8000/

Project Structure
- backend/
  - main.py            FastAPI app, routes, template setup
  - apps_registry.py   Pydantic models and in-memory apps registry
  - templates/
    - index.html       Dashboard UI
    - app.html         Stub app page
  - static/
    - styles.css       Basic styling for the dashboard

Notes
- This is a foundation. We can add authentication, RBAC, multi-company, and module loading as next steps.