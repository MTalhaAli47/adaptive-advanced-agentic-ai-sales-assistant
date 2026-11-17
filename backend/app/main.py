import json
import difflib
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.ai.providers import get_provider
from app.config import get_settings
from app.db import connect, decode_context, initialize_database
from app.sales import choose_next_action, extract_context, stage_after_message
from app.schemas import AppointmentCreate, ConversationReply, CustomerCreate, CustomerOut, MessageIn, OrderCreate

settings = get_settings()
WEB_DIR = Path(__file__).resolve().parents[2] / "web"


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)


@app.get("/")
def home():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/chat")
def chat_page():
    return FileResponse(WEB_DIR / "chat.html")


@app.get("/sales")
def sales_page():
    return FileResponse(WEB_DIR / "sales.html")


app.mount("/assets", StaticFiles(directory=WEB_DIR / "assets"), name="assets")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def customer_from_row(row) -> CustomerOut:
    return CustomerOut(
        id=row["id"], name=row["name"], contact=row["contact"], channel=row["channel"],
        context=decode_context(row["context_json"]), sales_stage=row["sales_stage"],
        handoff_requested=bool(row["handoff_requested"]), created_at=row["created_at"],
    )


def get_customer_row(customer_id: str):
    with connect() as db:
        row = db.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    return row


def catalogue_matches(context: dict) -> list[dict]:
    category = context.get("product_category")
    query = "SELECT id,name,description,category,price,currency,brand,ram_gb,gpu_model,vram_gb,storage_gb,condition FROM products WHERE active=1"
    params: list = []
    ignored = {"a", "an", "the", "some", "any", "i", "need", "want", "looking", "search", "for", "do", "you", "have", "sell", "show", "me", "recommend", "please", "product", "products", "item", "items"}
    interest_terms = [term.rstrip("s") if len(term) > 3 and term.endswith("s") else term for term in re.findall(r"[a-z0-9]+", category or "") if term not in ignored]
    for term in interest_terms:
        query += " AND (category LIKE ? OR name LIKE ? OR description LIKE ?)"
        params.extend([f"%{term}%", f"%{term}%", f"%{term}%"])
    for term in re.findall(r"[a-z0-9]+", (context.get("use_case") or "").casefold()):
        if term in {"and", "or", "the", "workloads", "everyday", "tasks"}:
            continue
        query += " AND (name LIKE ? OR description LIKE ? OR category LIKE ?)"
        params.extend([f"%{term}%", f"%{term}%", f"%{term}%"])
    minimum = context.get("budget_min_pkr")
    maximum = context.get("budget_max_pkr")
    gpu_model = context.get("gpu_model")
    if gpu_model:
        query += " AND gpu_model = ?"
        params.append(gpu_model)
    if context.get("minimum_ram_gb") is not None:
        query += " AND ram_gb >= ?"
        params.append(context["minimum_ram_gb"])
    if context.get("minimum_vram_gb") is not None:
        query += " AND vram_gb >= ?"
        params.append(context["minimum_vram_gb"])
    if minimum is not None:
        query += " AND price >= ?"
        params.append(minimum)
    if maximum is not None:
        query += " AND price <= ?"
        params.append(maximum)
    price_order = "DESC" if context.get("price_order") == "DESC" else "ASC"
    limit = max(1, min(10, int(context.get("result_limit", 5))))
    query += f" ORDER BY price {price_order} LIMIT ?"
    params.append(limit)
    with connect() as db:
        return [dict(row) for row in db.execute(query, params).fetchall()]


def catalogue_categories() -> list[str]:
    with connect() as db:
        rows = db.execute("SELECT DISTINCT category FROM products WHERE active=1 ORDER BY category").fetchall()
    return [row["category"] for row in rows]


def normalize_catalogue_interest(context: dict) -> None:
    interest = context.get("product_category")
    if not interest:
        return
    with connect() as db:
        rows = db.execute("SELECT category,name FROM products WHERE active=1").fetchall()
    vocabulary = {word.casefold() for row in rows for field in ("category", "name") for word in re.findall(r"[a-z0-9]+", row[field].casefold())}
    words = re.findall(r"[a-z0-9]+", interest.casefold())
    corrected = []
    for word in words:
        if word in vocabulary or len(word) < 4:
            corrected.append(word)
            continue
        match = difflib.get_close_matches(word, vocabulary, n=1, cutoff=0.78)
        corrected.append(match[0] if match else word)
    normalized = " ".join(corrected)
    if "laptop" in normalized.split():
        normalized = "laptops"
    context["product_category"] = normalized


def restore_conversation_context(customer_id: str, context: dict) -> dict:
    """Backfill category/state for conversations created before the state parser."""
    with connect() as db:
        rows = db.execute(
            "SELECT role,content FROM messages WHERE customer_id=? ORDER BY created_at DESC LIMIT 30",
            (customer_id,),
        ).fetchall()
    rows = list(reversed(rows))
    if not context.get("product_category"):
        for item in rows:
            if item["role"] == "customer":
                extract_context(item["content"], context)
    last_assistant = next((item["content"] for item in reversed(rows) if item["role"] == "assistant"), "")
    if context.get("budget_max_pkr") and any(term in last_assistant.casefold() for term in ("options start at pkr", "within that budget")):
        context["budget_no_match"] = True
    if "budget" in last_assistant.casefold() and not context.get("budget_max_pkr"):
        context["awaiting_field"] = "budget"
    return context


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "environment": settings.environment}


@app.post("/api/v1/customers", response_model=CustomerOut, status_code=201)
def create_customer(payload: CustomerCreate) -> CustomerOut:
    customer_id, created = str(uuid4()), now()
    with connect() as db:
        db.execute(
            "INSERT INTO customers(id,name,contact,channel,created_at) VALUES(?,?,?,?,?)",
            (customer_id, payload.name, payload.contact, payload.channel, created),
        )
        row = db.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
    return customer_from_row(row)


@app.get("/api/v1/customers/{customer_id}", response_model=CustomerOut)
def read_customer(customer_id: str) -> CustomerOut:
    return customer_from_row(get_customer_row(customer_id))


@app.get("/api/v1/customers", response_model=list[CustomerOut])
def list_customers() -> list[CustomerOut]:
    with connect() as db:
        rows = db.execute("SELECT * FROM customers ORDER BY created_at DESC").fetchall()
    return [customer_from_row(row) for row in rows]


@app.get("/api/v1/products")
def list_products(q: str = "") -> list[dict]:
    with connect() as db:
        if q.strip():
            term = f"%{q.strip()}%"
            rows = db.execute("SELECT * FROM products WHERE active=1 AND (name LIKE ? OR description LIKE ? OR category LIKE ?) ORDER BY name", (term, term, term)).fetchall()
        else:
            rows = db.execute("SELECT * FROM products WHERE active=1 ORDER BY name").fetchall()
    return [dict(row) for row in rows]


@app.get("/api/v1/dashboard/summary")
def dashboard_summary() -> dict:
    with connect() as db:
        counts = db.execute("SELECT COUNT(*) AS leads, SUM(CASE WHEN handoff_requested=1 THEN 1 ELSE 0 END) AS handoffs FROM customers").fetchone()
        stages = db.execute("SELECT sales_stage,COUNT(*) AS count FROM customers GROUP BY sales_stage").fetchall()
        appointments = db.execute("SELECT COUNT(*) FROM appointments WHERE status='requested'").fetchone()[0]
        orders = db.execute("SELECT COUNT(*) FROM orders WHERE status='pending_approval'").fetchone()[0]
    return {"leads": counts["leads"], "handoffs": counts["handoffs"] or 0, "appointments_to_confirm": appointments, "orders_pending_approval": orders, "stages": {row["sales_stage"]: row["count"] for row in stages}}


@app.post("/api/v1/conversations/{customer_id}/messages", response_model=ConversationReply)
def send_message(customer_id: str, payload: MessageIn) -> ConversationReply:
    row = get_customer_row(customer_id)
    context = decode_context(row["context_json"])
    context = restore_conversation_context(customer_id, context)
    context = extract_context(payload.message, context)
    normalize_catalogue_interest(context)
    stage = stage_after_message(context, row["sales_stage"])
    action = choose_next_action(context, stage, payload.message)
    if action["action"] == "discover":
        categories = catalogue_categories()
        action["available_categories"] = categories
        if categories:
            action["question"] = f"What product or service are you looking for? The catalogue currently includes {', '.join(categories)}. I can check other items too."
    if action["action"] == "recommend":
        products = catalogue_matches(context)
        action["products"] = products
        if not products:
            action["action"] = "no_match"
            action["reason"] = "No active catalogue items match the selected category and budget."
            if category := context.get("product_category"):
                action["product_category"] = category
            action["budget_max_pkr"] = context.get("budget_max_pkr")
            if context.get("budget_max_pkr") is not None:
                all_category_items = catalogue_matches({**context, "budget_min_pkr": None, "budget_max_pkr": None})
                numeric_prices = [item["price"] for item in all_category_items if item["price"] is not None]
                if numeric_prices:
                    action["lowest_price_pkr"] = min(numeric_prices)
            if context.get("product_category") == "cars":
                action["question"] = None
                context.pop("awaiting_field", None)
            elif context.get("budget_max_pkr") is not None:
                context["awaiting_field"] = "budget"
                context["budget_no_match"] = True
    elif action["action"] == "answer":
        action["products"] = catalogue_matches(context)
    handoff = action["action"] == "human_handoff"
    if handoff:
        stage = "human_handoff"
        context["handoff_reason"] = action["reason"]
    if action.get("question") == "Do you have a budget range in mind?":
        context["awaiting_field"] = "budget"
    elif action["action"] != "clarify_budget":
        context.pop("awaiting_field", None)
    reply = get_provider().reply(payload.message, context, action)
    context.pop("budget_prompt_skipped", None)
    stamp = now()
    with connect() as db:
        db.execute("INSERT INTO messages VALUES(?,?,?,?,?)", (str(uuid4()), customer_id, "customer", payload.message, stamp))
        db.execute("INSERT INTO messages VALUES(?,?,?,?,?)", (str(uuid4()), customer_id, "assistant", reply, now()))
        db.execute(
            "UPDATE customers SET context_json=?, sales_stage=?, handoff_requested=? WHERE id=?",
            (json.dumps(context), stage, int(handoff), customer_id),
        )
    return ConversationReply(customer_id=customer_id, reply=reply, sales_stage=stage, context=context, next_action=action, handoff_required=handoff)


@app.get("/api/v1/conversations/{customer_id}")
def conversation_history(customer_id: str) -> dict:
    get_customer_row(customer_id)
    with connect() as db:
        rows = db.execute("SELECT id,customer_id,role,content,created_at FROM messages WHERE customer_id=? ORDER BY created_at", (customer_id,)).fetchall()
    return {"customer_id": customer_id, "messages": [dict(row) for row in rows]}


@app.get("/api/v1/sales/{customer_id}/next-action")
def next_action(customer_id: str) -> dict:
    row = get_customer_row(customer_id)
    context = decode_context(row["context_json"])
    latest = context.get("last_customer_message", "")
    action = choose_next_action(context, row["sales_stage"], latest)
    if action["action"] == "recommend":
        action["products"] = catalogue_matches(context)
        if not action["products"]:
            action["action"] = "no_match"
            action["reason"] = "No active catalogue items match the selected category and budget."
    return action


@app.post("/api/v1/sales/{customer_id}/handoff")
def request_handoff(customer_id: str, reason: str = "Salesperson assistance requested") -> dict:
    row = get_customer_row(customer_id)
    context = decode_context(row["context_json"])
    context["handoff_reason"] = reason[:500]
    with connect() as db:
        db.execute("UPDATE customers SET sales_stage='human_handoff', handoff_requested=1, context_json=? WHERE id=?", (json.dumps(context), customer_id))
    return {"customer_id": customer_id, "handoff_requested": True, "reason": context["handoff_reason"]}


@app.post("/api/v1/appointments/{customer_id}", status_code=201)
def request_appointment(customer_id: str, payload: AppointmentCreate) -> dict:
    get_customer_row(customer_id)
    appointment_id = str(uuid4())
    with connect() as db:
        db.execute("INSERT INTO appointments(id,customer_id,requested_time,notes,created_at) VALUES(?,?,?,?,?)", (appointment_id, customer_id, payload.requested_time, payload.notes, now()))
    return {"id": appointment_id, "customer_id": customer_id, "requested_time": payload.requested_time, "status": "requested"}


@app.post("/api/v1/orders/{customer_id}", status_code=201)
def create_order(customer_id: str, payload: OrderCreate) -> dict:
    get_customer_row(customer_id)
    order_id = str(uuid4())
    with connect() as db:
        product = db.execute("SELECT id,name FROM products WHERE id=? AND active=1", (payload.product_id,)).fetchone()
        if not product:
            raise HTTPException(status_code=404, detail="Product not found")
        db.execute("INSERT INTO orders(id,customer_id,product_id,quantity,created_at) VALUES(?,?,?,?,?)", (order_id, customer_id, payload.product_id, payload.quantity, now()))
    return {"id": order_id, "customer_id": customer_id, "product": product["name"], "quantity": payload.quantity, "status": "pending_approval", "message": "Order recorded for salesperson confirmation; no payment has been taken."}


@app.get("/api/v1/sales/workspace")
def sales_workspace() -> dict:
    customers = list_customers()
    items = []
    for customer in customers:
        with connect() as db:
            messages = db.execute("SELECT role,content,created_at FROM messages WHERE customer_id=? ORDER BY created_at DESC LIMIT 1", (customer.id,)).fetchone()
            action = choose_next_action(customer.context, customer.sales_stage, customer.context.get("last_customer_message", ""))
            normalize_catalogue_interest(customer.context)
            if action["action"] == "discover":
                categories = catalogue_categories()
                action["available_categories"] = categories
                if categories:
                    action["question"] = f"Catalogue categories: {', '.join(categories)}."
            if action["action"] == "recommend":
                action["products"] = catalogue_matches(customer.context)
                if not action["products"]:
                    action["action"] = "no_match"
                    action["reason"] = "No active catalogue items match the selected category and budget."
        items.append({"customer": customer.model_dump(), "last_message": dict(messages) if messages else None, "next_action": action})
    return {"conversations": items}
