import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.config import get_settings


def _database_path() -> Path:
    url = get_settings().database_url
    if not url.startswith("sqlite:///"):
        raise ValueError("This initial backend supports SQLite URLs only.")
    path = Path(url.removeprefix("sqlite:///"))
    if not path.is_absolute():
        path = Path.cwd() / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(_database_path())
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database() -> None:
    with connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS customers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                contact TEXT,
                channel TEXT NOT NULL DEFAULT 'web',
                context_json TEXT NOT NULL DEFAULT '{}',
                sales_stage TEXT NOT NULL DEFAULT 'new',
                handoff_requested INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                customer_id TEXT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                role TEXT NOT NULL CHECK(role IN ('customer','assistant','system')),
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_messages_customer_created
                ON messages(customer_id, created_at);
            CREATE TABLE IF NOT EXISTS products (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                category TEXT NOT NULL,
                price REAL,
                currency TEXT NOT NULL DEFAULT 'PKR',
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS appointments (
                id TEXT PRIMARY KEY,
                customer_id TEXT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                requested_time TEXT NOT NULL,
                notes TEXT,
                status TEXT NOT NULL DEFAULT 'requested',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS orders (
                id TEXT PRIMARY KEY,
                customer_id TEXT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                product_id TEXT NOT NULL REFERENCES products(id),
                quantity INTEGER NOT NULL CHECK(quantity > 0),
                status TEXT NOT NULL DEFAULT 'pending_approval',
                created_at TEXT NOT NULL
            );
            """
        )
        db.executemany(
            "INSERT OR IGNORE INTO products(id,name,description,category,price,currency) VALUES(?,?,?,?,?,?)",
            [
                ("solar-home", "Home Solar Assessment", "A tailored rooftop solar assessment based on your location, electricity usage, and roof space.", "Solar", None, "PKR"),
                ("sales-starter", "Sales Starter Plan", "A practical starter package for small teams looking to organize leads and automate routine follow-up.", "Software", 12000, "PKR"),
                ("sales-growth", "Sales Growth Plan", "A growth package with shared pipeline visibility, team workflows, and expanded reporting.", "Software", 28000, "PKR"),
            ],
        )


def decode_context(value: str) -> dict:
    return json.loads(value or "{}")
