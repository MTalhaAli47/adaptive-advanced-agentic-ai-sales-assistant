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
                access_token_hash TEXT,
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
                active INTEGER NOT NULL DEFAULT 1,
                brand TEXT,
                ram_gb INTEGER,
                gpu_model TEXT,
                vram_gb INTEGER,
                storage_gb INTEGER,
                condition TEXT,
                subcategory TEXT,
                tags_json TEXT NOT NULL DEFAULT '[]',
                attributes_json TEXT NOT NULL DEFAULT '{}',
                price_tier TEXT,
                use_cases_json TEXT NOT NULL DEFAULT '[]',
                performance_level TEXT,
                compatibility_json TEXT NOT NULL DEFAULT '{}',
                features_json TEXT NOT NULL DEFAULT '[]',
                suitable_profiles_json TEXT NOT NULL DEFAULT '[]',
                created_by_user_id TEXT
            );
            CREATE TABLE IF NOT EXISTS appointments (
                id TEXT PRIMARY KEY,
                customer_id TEXT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                requested_time TEXT NOT NULL,
                notes TEXT,
                status TEXT NOT NULL DEFAULT 'requested',
                updated_by_user_id TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS orders (
                id TEXT PRIMARY KEY,
                customer_id TEXT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                product_id TEXT NOT NULL REFERENCES products(id),
                quantity INTEGER NOT NULL CHECK(quantity > 0),
                status TEXT NOT NULL DEFAULT 'pending_confirmation',
                payment_status TEXT NOT NULL DEFAULT 'not_paid',
                fulfillment_status TEXT NOT NULL DEFAULT 'not_started',
                payment_method TEXT,
                payment_reference TEXT,
                admin_note TEXT,
                confirmed_at TEXT,
                updated_by_user_id TEXT,
                payment_updated_at TEXT,
                updated_at TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS cart_items (
                customer_id TEXT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                quantity INTEGER NOT NULL CHECK(quantity BETWEEN 1 AND 100),
                updated_at TEXT NOT NULL,
                PRIMARY KEY(customer_id, product_id)
            );
            CREATE TABLE IF NOT EXISTS product_relationships (
                id TEXT PRIMARY KEY,
                main_product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                related_product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                relationship_type TEXT NOT NULL,
                weight REAL NOT NULL DEFAULT 1.0,
                note TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                created_by_user_id TEXT,
                UNIQUE(main_product_id,related_product_id,relationship_type),
                CHECK(main_product_id <> related_product_id)
            );
            """
        )
        message_schema = db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='messages'"
        ).fetchone()[0]
        if "'salesperson'" not in message_schema:
            db.execute(
                """CREATE TABLE messages_new (
                    id TEXT PRIMARY KEY,
                    customer_id TEXT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK(role IN ('customer','assistant','system','salesperson')),
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )"""
            )
            db.execute("INSERT INTO messages_new SELECT * FROM messages")
            db.execute("DROP TABLE messages")
            db.execute("ALTER TABLE messages_new RENAME TO messages")
            db.execute("CREATE INDEX idx_messages_customer_created ON messages(customer_id, created_at)")
        order_columns = {row["name"] for row in db.execute("PRAGMA table_info(orders)").fetchall()}
        for column, definition in (
            ("checkout_id", "TEXT"),
            ("payment_status", "TEXT NOT NULL DEFAULT 'not_paid'"),
            ("fulfillment_status", "TEXT NOT NULL DEFAULT 'not_started'"),
            ("payment_method", "TEXT"), ("payment_reference", "TEXT"), ("admin_note", "TEXT"),
            ("confirmed_at", "TEXT"), ("updated_by_user_id", "TEXT"), ("payment_updated_at", "TEXT"), ("updated_at", "TEXT"),
        ):
            if column not in order_columns:
                db.execute(f"ALTER TABLE orders ADD COLUMN {column} {definition}")
        customer_columns = {row["name"] for row in db.execute("PRAGMA table_info(customers)").fetchall()}
        if "access_token_hash" not in customer_columns:
            db.execute("ALTER TABLE customers ADD COLUMN access_token_hash TEXT")
        appointment_columns = {row["name"] for row in db.execute("PRAGMA table_info(appointments)").fetchall()}
        if "updated_by_user_id" not in appointment_columns:
            db.execute("ALTER TABLE appointments ADD COLUMN updated_by_user_id TEXT")
        existing_columns = {row["name"] for row in db.execute("PRAGMA table_info(products)").fetchall()}
        for column, definition in (
            ("brand", "TEXT"), ("ram_gb", "INTEGER"), ("gpu_model", "TEXT"),
            ("vram_gb", "INTEGER"), ("storage_gb", "INTEGER"), ("condition", "TEXT"),
            ("subcategory", "TEXT"), ("tags_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("attributes_json", "TEXT NOT NULL DEFAULT '{}'"), ("price_tier", "TEXT"),
            ("use_cases_json", "TEXT NOT NULL DEFAULT '[]'"), ("performance_level", "TEXT"),
            ("compatibility_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("features_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("suitable_profiles_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("created_by_user_id", "TEXT"),
        ):
            if column not in existing_columns:
                db.execute(f"ALTER TABLE products ADD COLUMN {column} {definition}")
        relationship_columns = {row["name"] for row in db.execute("PRAGMA table_info(product_relationships)").fetchall()}
        if "created_by_user_id" not in relationship_columns:
            db.execute("ALTER TABLE product_relationships ADD COLUMN created_by_user_id TEXT")
        db.executemany(
            "INSERT OR IGNORE INTO products(id,name,description,category,price,currency) VALUES(?,?,?,?,?,?)",
            [
                ("solar-home", "Home Solar Assessment", "A tailored rooftop solar assessment based on your location, electricity usage, and roof space.", "Solar", None, "PKR"),
                ("sales-starter", "Sales Starter Plan", "A practical starter package for small teams looking to organize leads and automate routine follow-up.", "Software", 12000, "PKR"),
                ("sales-growth", "Sales Growth Plan", "A growth package with shared pipeline visibility, team workflows, and expanded reporting.", "Software", 28000, "PKR"),
                ("lenovo-rtx5060-32gb", "Lenovo Legion Gaming Laptop — RTX 5060, 32GB", "Gaming, 3D development, and AI workloads. 32GB RAM, NVIDIA GeForce RTX 5060 Laptop GPU with 8GB VRAM, 512GB SSD. Sample Pakistan market price; final CPU, storage, condition, and availability require seller confirmation.", "Gaming Laptops", 680000, "PKR"),
                ("lenovo-rtx5070-32gb", "Lenovo Legion Gaming Laptop — RTX 5070, 32GB", "Gaming, 3D development, and AI workloads. 32GB RAM, NVIDIA GeForce RTX 5070 Laptop GPU with 8GB VRAM, 1TB SSD. Sample Pakistan market price; final CPU, storage, condition, and availability require seller confirmation.", "Gaming Laptops", 929999, "PKR"),
                ("lenovo-rtx5070ti-32gb", "Lenovo Legion Gaming Laptop — RTX 5070 Ti, 32GB", "Gaming, 3D development, and AI workloads. 32GB RAM, NVIDIA GeForce RTX 5070 Ti Laptop GPU with 12GB VRAM, 2TB SSD. Sample Pakistan market price; final CPU, storage, condition, and availability require seller confirmation.", "Gaming Laptops", 974999, "PKR"),
                ("lenovo-rtx5080-32gb", "Lenovo Legion Gaming Laptop — RTX 5080, 32GB", "Gaming, 3D development, and AI workloads. 32GB RAM, NVIDIA GeForce RTX 5080 Laptop GPU with 16GB VRAM, 1TB SSD. Sample Pakistan market price; final CPU, storage, condition, and availability require seller confirmation.", "Gaming Laptops", 1094999, "PKR"),
                ("lenovo-rtx5090-32gb", "Lenovo Legion Gaming Laptop — RTX 5090, 32GB", "Gaming, 3D development, and AI workloads. 32GB RAM, NVIDIA GeForce RTX 5090 Laptop GPU with 24GB VRAM, 2TB SSD. Sample Pakistan market price; final CPU, storage, condition, and availability require seller confirmation.", "Gaming Laptops", 1474999, "PKR"),
                ("hp-840g5-i5-8350u", "Used HP EliteBook 840 G5 — Core i5, 8GB, 256GB", "Used office laptop for everyday office work and light tasks. Intel Core i5-8350U, 8GB RAM, 256GB SSD. Used-unit condition and battery health should be checked. Sample Pakistan market price.", "Office Laptops", 58500, "PKR"),
                ("hp-840g5-i5-8250u", "Used HP EliteBook 840 G5 — Core i5, 8GB, 256GB SSD", "Used office laptop for everyday office work and light tasks. Intel Core i5-8250U, 8GB RAM, 256GB SSD. Used-unit condition and battery health should be checked. Sample Pakistan market price.", "Office Laptops", 61000, "PKR"),
                ("hp-840g5-i7-8650u", "Used HP EliteBook 840 G5 — Core i7, 16GB, 512GB SSD", "Used office laptop for everyday office work and light tasks. Intel Core i7-8650U, 16GB RAM, 512GB SSD. Used-unit condition and battery health should be checked. Sample Pakistan market price.", "Office Laptops", 78000, "PKR"),
            ],
        )
        db.executemany(
            "UPDATE products SET brand=?,ram_gb=?,gpu_model=?,vram_gb=?,storage_gb=?,condition=? WHERE id=?",
            [
                ("Lenovo", 32, "RTX 5060", 8, 512, "new; sample listing", "lenovo-rtx5060-32gb"),
                ("Lenovo", 32, "RTX 5070", 8, 1024, "new; sample listing", "lenovo-rtx5070-32gb"),
                ("Lenovo", 32, "RTX 5070 Ti", 12, 2048, "new; sample listing", "lenovo-rtx5070ti-32gb"),
                ("Lenovo", 32, "RTX 5080", 16, 1024, "new; sample listing", "lenovo-rtx5080-32gb"),
                ("Lenovo", 32, "RTX 5090", 24, 2048, "new; sample listing", "lenovo-rtx5090-32gb"),
                ("HP", 8, None, None, 256, "used; verify unit condition", "hp-840g5-i5-8350u"),
                ("HP", 8, None, None, 256, "used; verify unit condition", "hp-840g5-i5-8250u"),
                ("HP", 16, None, None, 512, "used; verify unit condition", "hp-840g5-i7-8650u"),
            ],
        )
        db.executemany(
            "UPDATE products SET tags_json=?,use_cases_json=?,price_tier=?,performance_level=? WHERE id=?",
            [
                ('["gaming","laptop","nvidia","high performance"]','["gaming","3d development","ai workloads","rendering"]',"mid_range","high","lenovo-rtx5060-32gb"),
                ('["gaming","laptop","nvidia","high performance"]','["gaming","3d development","ai workloads","rendering"]',"premium","high","lenovo-rtx5070-32gb"),
                ('["gaming","laptop","nvidia","high performance"]','["gaming","3d development","ai workloads","rendering"]',"premium","very high","lenovo-rtx5070ti-32gb"),
                ('["gaming","laptop","nvidia","high performance"]','["gaming","3d development","ai workloads","rendering"]',"premium","very high","lenovo-rtx5080-32gb"),
                ('["gaming","laptop","nvidia","high performance"]','["gaming","3d development","ai workloads","rendering"]',"premium","enthusiast","lenovo-rtx5090-32gb"),
                ('["office","laptop","used","everyday"]','["office","productivity","light tasks"]',"budget","entry level","hp-840g5-i5-8350u"),
                ('["office","laptop","used","everyday"]','["office","productivity","light tasks"]',"budget","entry level","hp-840g5-i5-8250u"),
                ('["office","laptop","used","everyday"]','["office","productivity","light tasks"]',"budget","mid range","hp-840g5-i7-8650u"),
            ],
        )


def decode_context(value: str) -> dict:
    return json.loads(value or "{}")
