from pydantic import BaseModel


class App(BaseModel):
    name: str
    slug: str
    color: str  # hex color for tile background
    icon: str   # simple emoji/icon placeholder
    description: str = ""


# Single Chart app
APPS: list[App] = [
    App(name="Chart", slug="chart", color="#3B82F6", icon="📊", description="Analytics and reporting"),
    # Inventory Management app activation
    App(name="Inventory", slug="inventory", color="#F59E0B", icon="📦", description="Stock, transfers, adjustments"),
    # Coil Slitting optimization app
    App(name="Slitting", slug="slitting", color="#22C55E", icon="🧪", description="Coil slitting planning and optimization"),
    # Quality Assurance app activation
    App(name="Quality", slug="quality", color="#8B5CF6", icon="✅", description="QC inspections, defects, and compliance"),
]