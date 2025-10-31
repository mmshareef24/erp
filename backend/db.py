from __future__ import annotations

from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, text
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime
import hashlib


DB_URL = "sqlite:///backend/data/app.db"
engine = create_engine(DB_URL, echo=False, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False)
    password_hash = Column(String(128), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    @staticmethod
    def hash_pw(pw: str) -> str:
        return hashlib.sha256(pw.encode("utf-8")).hexdigest()


class Customer(Base):
    __tablename__ = "customers"
    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False)
    email = Column(String(128), nullable=True)
    address1 = Column(String(256), nullable=True)
    address2 = Column(String(256), nullable=True)
    city = Column(String(128), nullable=True)
    state = Column(String(128), nullable=True)
    postal_code = Column(String(32), nullable=True)
    country_code = Column(String(2), nullable=True)
    vat_number = Column(String(64), nullable=True)
    vat_rate = Column(Float, nullable=True)
    ar_account = Column(String(32), nullable=True)


class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False)
    price = Column(Float, nullable=False, default=0.0)


def init_db():
    Base.metadata.create_all(bind=engine)
    # seed admin user if none exists
    with SessionLocal() as db:
        if not db.query(User).first():
            admin = User(username="admin", password_hash=User.hash_pw("admin"))
            db.add(admin)
            db.commit()

    # Add new Customer columns if missing (SQLite allows ADD COLUMN)
    with engine.connect() as conn:
        cols = conn.execute(text("PRAGMA table_info(customers)")).fetchall()
        existing = {c[1] for c in cols}
        add_cols = []
        if "address1" not in existing:
            add_cols.append("ALTER TABLE customers ADD COLUMN address1 TEXT")
        if "address2" not in existing:
            add_cols.append("ALTER TABLE customers ADD COLUMN address2 TEXT")
        if "city" not in existing:
            add_cols.append("ALTER TABLE customers ADD COLUMN city TEXT")
        if "state" not in existing:
            add_cols.append("ALTER TABLE customers ADD COLUMN state TEXT")
        if "postal_code" not in existing:
            add_cols.append("ALTER TABLE customers ADD COLUMN postal_code TEXT")
        if "country_code" not in existing:
            add_cols.append("ALTER TABLE customers ADD COLUMN country_code TEXT")
        if "vat_number" not in existing:
            add_cols.append("ALTER TABLE customers ADD COLUMN vat_number TEXT")
        if "vat_rate" not in existing:
            add_cols.append("ALTER TABLE customers ADD COLUMN vat_rate REAL")
        if "ar_account" not in existing:
            add_cols.append("ALTER TABLE customers ADD COLUMN ar_account TEXT")
        for sql in add_cols:
            conn.execute(text(sql))
            conn.commit()

def default_vat_rate(country_code: str | None) -> float:
    mapping = {
        "US": 0.0,
        "GB": 0.20,
        "DE": 0.19,
        "FR": 0.20,
        "AE": 0.05,
        "SA": 0.15,
        "IN": 0.18,
        "PK": 0.18,
        "CA": 0.05,
        "AU": 0.10,
    }
    if not country_code:
        return 0.0
    return mapping.get(country_code.upper(), 0.0)