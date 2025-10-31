from pydantic import BaseModel


class App(BaseModel):
    name: str
    slug: str
    color: str  # hex color for tile background
    icon: str   # simple emoji/icon placeholder
    description: str = ""


# Minimal starter set; easy to extend
APPS: list[App] = [
    App(name="Discuss", slug="discuss", color="#F59E0B", icon="💬", description="Company-wide chat and threads"),
    App(name="Calendar", slug="calendar", color="#F97316", icon="📅", description="Meetings and events"),
    App(name="Appointments", slug="appointments", color="#10B981", icon="📆", description="Booking and availability"),
    App(name="To-do", slug="todo", color="#22C55E", icon="✅", description="Personal and team tasks"),
    App(name="Knowledge", slug="knowledge", color="#6366F1", icon="📚", description="Company docs and pages"),
    App(name="Sales", slug="sales", color="#EF4444", icon="📈", description="Pipeline and quotations"),
    App(name="Purchases", slug="purchases", color="#0EA5E9", icon="🛒", description="Procurement and vendor bills"),
    App(name="Accounting", slug="accounting", color="#8B5CF6", icon="🧾", description="Invoices and ledger"),
    App(name="Inventory", slug="inventory", color="#06B6D4", icon="📦", description="Stock and warehouses"),
    App(name="Production", slug="production", color="#84CC16", icon="🏭", description="Manufacturing and work orders"),
    App(name="MRP", slug="mrp", color="#0EA5E9", icon="🧮", description="Materials Requirements Planning"),
    App(name="Employees", slug="employees", color="#A855F7", icon="👥", description="Directory and HR"),
    App(name="Time", slug="time", color="#2563EB", icon="⏱️", description="Shifts, attendance, timesheets, overtime"),
    App(name="Settings", slug="settings", color="#F43F5E", icon="⚙️", description="System configuration"),
]