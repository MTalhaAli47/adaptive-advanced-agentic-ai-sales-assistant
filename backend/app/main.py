import json
import difflib
import hashlib
import re
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

from app.ai.providers import get_provider
from app.config import get_settings
from app.db import connect, decode_context, initialize_database
from app.recommendations import recommend_related_products
from app.sales import CART_RE, choose_next_action, extract_context, stage_after_message
from app.schemas import AdminAppointmentUpdate, AdminOrderUpdate, AppointmentCreate, CartItemCreate, CartItemUpdate, CheckoutCreate, ConversationReply, CustomerCreate, CustomerOut, MessageIn, OrderCreate, ProductCreate, ProductRelationshipCreate

settings = get_settings()
WEB_DIR = Path(__file__).resolve().parents[2] / "web"


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
staff_security = HTTPBasic(auto_error=False)


def require_staff(role: str, credentials: HTTPBasicCredentials | None = Depends(staff_security)) -> dict:
    login_id = settings.admin_login_id if role == "admin" else settings.salesperson_login_id
    password = settings.admin_login_password if role == "admin" else settings.salesperson_login_password
    staff_id = "admin-001" if role == "admin" else "sales-001"
    if not password:
        if settings.environment.casefold() == "production":
            raise HTTPException(status_code=503, detail=f"Configure {role.upper()}_LOGIN_PASSWORD before using this workspace")
        return {"id": staff_id, "login_id": login_id, "role": role}
    username_ok = credentials is not None and secrets.compare_digest(credentials.username, login_id)
    password_ok = credentials is not None and secrets.compare_digest(credentials.password, password)
    if not (username_ok and password_ok):
        raise HTTPException(status_code=401, detail=f"Valid {role} login is required", headers={"WWW-Authenticate": "Basic"})
    return {"id": staff_id, "login_id": login_id, "role": role}


def require_admin(credentials: HTTPBasicCredentials | None = Depends(staff_security)) -> dict:
    return require_staff("admin", credentials)


def require_salesperson(credentials: HTTPBasicCredentials | None = Depends(staff_security)) -> dict:
    return require_staff("salesperson", credentials)


@app.get("/api/v1/admin/identity")
def admin_identity(staff: dict = Depends(require_admin)) -> dict:
    return staff


@app.get("/api/v1/sales/identity")
def salesperson_identity(staff: dict = Depends(require_salesperson)) -> dict:
    return staff


@app.get("/")
def home():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/chat")
def chat_page():
    return FileResponse(WEB_DIR / "chat.html")


@app.get("/cart")
def cart_page():
    return FileResponse(WEB_DIR / "cart.html")


@app.get("/sales")
def sales_page():
    return FileResponse(WEB_DIR / "sales.html")


@app.get("/admin")
def admin_page():
    return FileResponse(WEB_DIR / "admin.html")


app.mount("/assets", StaticFiles(directory=WEB_DIR / "assets"), name="assets")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def customer_from_row(row) -> CustomerOut:
    return CustomerOut(
        id=row["id"], user_id=row["id"], name=row["name"], contact=row["contact"], channel=row["channel"],
        context=decode_context(row["context_json"]), sales_stage=row["sales_stage"],
        handoff_requested=bool(row["handoff_requested"]), created_at=row["created_at"],
    )


def get_customer_row(customer_id: str):
    with connect() as db:
        row = db.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    return row


def verify_customer_token(customer_id: str, token: str | None) -> None:
    row = get_customer_row(customer_id)
    token_hash = row["access_token_hash"] if "access_token_hash" in row.keys() else None
    supplied_hash = hashlib.sha256((token or "").encode()).hexdigest()
    if not token_hash or not secrets.compare_digest(token_hash, supplied_hash):
        raise HTTPException(status_code=401, detail="Sign in to this customer profile to view its orders")


def serialize_product(row) -> dict:
    item = dict(row)
    for field, target, default in (("tags_json", "tags", []), ("attributes_json", "attributes", {}),
                                   ("use_cases_json", "use_cases", []), ("compatibility_json", "compatibility", {}),
                                   ("features_json", "features", []), ("suitable_profiles_json", "suitable_profiles", [])):
        try:
            item[target] = json.loads(item.pop(field, "") or json.dumps(default))
        except (TypeError, ValueError):
            item[target] = default
    return item


def catalogue_matches(context: dict) -> list[dict]:
    category = context.get("product_category")
    query = "SELECT * FROM products WHERE active=1"
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


def laptop_price_starts() -> dict[str, int]:
    with connect() as db:
        rows = db.execute(
            "SELECT category,MIN(price) AS price FROM products WHERE active=1 AND category LIKE '%laptop%' GROUP BY category"
        ).fetchall()
    return {row["category"]: int(row["price"]) for row in rows if row["price"] is not None}


def recommended_products(context: dict) -> list[dict]:
    ids = context.get("last_recommended_product_ids") or []
    if not ids:
        return catalogue_matches(context)
    placeholders = ",".join("?" for _ in ids)
    with connect() as db:
        rows = db.execute(
            f"SELECT * FROM products WHERE active=1 AND id IN ({placeholders})",
            ids,
        ).fetchall()
    by_id = {row["id"]: dict(row) for row in rows}
    return [by_id[item_id] for item_id in ids if item_id in by_id]


def related_for_products(products: list[dict], context: dict, limit: int = 4, exclude_ids: set[str] | None = None) -> list[dict]:
    if not products:
        return []
    with connect() as db:
        candidates = [dict(row) for row in db.execute("SELECT * FROM products WHERE active=1", ()).fetchall()]
        links = [dict(row) for row in db.execute("SELECT * FROM product_relationships WHERE active=1", ()).fetchall()]
    output, seen = [], set()
    for main in products[:3]:
        product_links = [link for link in links if link["main_product_id"] == main["id"]]
        for item in recommend_related_products(main, candidates, context, product_links, limit=limit):
            related_id = item["related_product"]["id"]
            if related_id in (exclude_ids or set()):
                continue
            if related_id not in seen:
                seen.add(related_id)
                output.append(item)
    output.sort(key=lambda item: -item["relevance_score"])
    return output[:limit]


def select_order_product(products: list[dict], message: str, context: dict) -> dict | None:
    if not products:
        return None
    lowered = message.casefold()
    position = re.fullmatch(r"\s*(?:option\s*)?(\d{1,2})[.!\s]*", lowered)
    if position and 1 <= int(position.group(1)) <= len(products):
        return products[int(position.group(1)) - 1]
    gpu_model = context.get("gpu_model")
    if gpu_model and "rtx" in lowered:
        matches = [item for item in products if (item.get("gpu_model") or "").casefold() == gpu_model.casefold()]
        if len(matches) == 1:
            return matches[0]
    named = [item for item in products if item["name"].casefold() in lowered]
    if len(named) == 1:
        return named[0]
    if len(products) == 1:
        return products[0]
    if re.search(r"\b(?:first|cheapest|lowest price)\b", lowered):
        return min((item for item in products if item.get("price") is not None), key=lambda item: item["price"], default=None)
    if re.search(r"\b(?:last|most expensive|highest price)\b", lowered):
        return max((item for item in products if item.get("price") is not None), key=lambda item: item["price"], default=None)
    return None


def normalize_catalogue_interest(context: dict) -> None:
    interest = context.get("product_category")
    if not interest:
        return
    if re.search(r"\b(?:talk|speak|chat|connect|call)\b.*\b(?:human|person|agent|sales\s*person|sales\s*rep|representative|employee|staff)\b", interest) or re.search(r"\b(?:book|schedule|arrange)\b.*\b(?:appointment|meeting|demo|call)\b", interest):
        context.pop("product_category", None)
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
    customer_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(customer_token.encode()).hexdigest()
    with connect() as db:
        db.execute(
            "INSERT INTO customers(id,name,contact,channel,access_token_hash,created_at) VALUES(?,?,?,?,?,?)",
            (customer_id, payload.name, payload.contact, payload.channel, token_hash, created),
        )
        row = db.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
    return customer_from_row(row).model_copy(update={"customer_token": customer_token})


@app.get("/api/v1/customers/{customer_id}", response_model=CustomerOut)
def read_customer(customer_id: str) -> CustomerOut:
    return customer_from_row(get_customer_row(customer_id))


@app.get("/api/v1/customers/{customer_id}/orders")
def customer_orders(customer_id: str, x_customer_token: str | None = Header(default=None)) -> list[dict]:
    verify_customer_token(customer_id, x_customer_token)
    with connect() as db:
        rows = db.execute("""SELECT o.id,o.customer_id,o.customer_id AS user_id,o.product_id,o.quantity,o.status,o.payment_status,o.fulfillment_status,o.payment_method,o.created_at,o.updated_at,p.name AS product_name,p.price,p.currency
            FROM orders o JOIN products p ON p.id=o.product_id WHERE o.customer_id=? ORDER BY o.created_at DESC""", (customer_id,)).fetchall()
    return [dict(row) for row in rows]


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
    return [serialize_product(row) for row in rows]


@app.get("/api/v1/products/{product_id}/related")
def get_related_products(product_id: str, customer_id: str | None = None) -> dict:
    with connect() as db:
        main_row = db.execute("SELECT * FROM products WHERE id=? AND active=1", (product_id,)).fetchone()
        if not main_row:
            raise HTTPException(status_code=404, detail="Product not found")
        products = [dict(row) for row in db.execute("SELECT * FROM products WHERE active=1", ()).fetchall()]
        links = [dict(row) for row in db.execute("SELECT * FROM product_relationships WHERE main_product_id=? AND active=1", (product_id,)).fetchall()]
        context = {}
        if customer_id:
            customer = db.execute("SELECT context_json FROM customers WHERE id=?", (customer_id,)).fetchone()
            if customer:
                context = decode_context(customer["context_json"])
    return {"main_product": serialize_product(main_row), "recommendations": recommend_related_products(dict(main_row), products, context, links)}


@app.get("/api/v1/admin/products")
def admin_products(staff: dict = Depends(require_admin)) -> list[dict]:
    with connect() as db:
        rows = db.execute("SELECT * FROM products ORDER BY active DESC,category,name").fetchall()
    return [serialize_product(row) for row in rows]


@app.post("/api/v1/admin/products", status_code=201)
def add_product(payload: ProductCreate, staff: dict = Depends(require_admin)) -> dict:
    product_id = payload.id or re.sub(r"[^a-z0-9]+", "-", payload.name.casefold()).strip("-")[:100]
    fields = ("subcategory", "tags", "attributes", "price_tier", "use_cases", "performance_level", "compatibility", "features", "suitable_profiles")
    values = {field: getattr(payload, field) for field in fields}
    try:
        with connect() as db:
            db.execute(
                """INSERT INTO products(id,name,description,category,price,currency,active,subcategory,tags_json,attributes_json,price_tier,use_cases_json,performance_level,compatibility_json,features_json,suitable_profiles_json,created_by_user_id)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (product_id,payload.name,payload.description,payload.category,payload.price,payload.currency,int(payload.active),values["subcategory"],json.dumps(values["tags"]),json.dumps(values["attributes"]),values["price_tier"],json.dumps(values["use_cases"]),values["performance_level"],json.dumps(values["compatibility"]),json.dumps(values["features"]),json.dumps(values["suitable_profiles"]),staff["id"]),
            )
            row = db.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
    except Exception as exc:
        if "UNIQUE" in str(exc):
            raise HTTPException(status_code=409, detail="A product with that ID already exists") from exc
        raise
    return serialize_product(row)


@app.delete("/api/v1/admin/products/{product_id}")
def remove_product(product_id: str, staff: dict = Depends(require_admin)) -> dict:
    with connect() as db:
        result = db.execute("UPDATE products SET active=0 WHERE id=? AND active=1", (product_id,))
        if not result.rowcount:
            raise HTTPException(status_code=404, detail="Active product not found")
    return {"id": product_id, "active": False, "message": "Product removed from the active catalogue."}


@app.get("/api/v1/admin/product-relationships")
def list_product_relationships(staff: dict = Depends(require_admin)) -> list[dict]:
    with connect() as db:
        rows = db.execute("""SELECT r.*,m.name AS main_product_name,p.name AS related_product_name
            FROM product_relationships r JOIN products m ON m.id=r.main_product_id
            JOIN products p ON p.id=r.related_product_id ORDER BY m.name,p.name""").fetchall()
    return [dict(row) for row in rows]


@app.post("/api/v1/admin/product-relationships", status_code=201)
def create_product_relationship(payload: ProductRelationshipCreate, staff: dict = Depends(require_admin)) -> dict:
    relationship_id = str(uuid4())
    with connect() as db:
        for product_id in (payload.main_product_id, payload.related_product_id):
            if not db.execute("SELECT id FROM products WHERE id=? AND active=1", (product_id,)).fetchone():
                raise HTTPException(status_code=404, detail="Both products must be active catalogue entries")
        db.execute("INSERT INTO product_relationships(id,main_product_id,related_product_id,relationship_type,weight,note,created_by_user_id) VALUES(?,?,?,?,?,?,?) ON CONFLICT(main_product_id,related_product_id,relationship_type) DO UPDATE SET weight=excluded.weight,note=excluded.note,active=1,created_by_user_id=excluded.created_by_user_id", (relationship_id,payload.main_product_id,payload.related_product_id,payload.relationship_type,payload.weight,payload.note,staff["id"]))
        row = db.execute("SELECT * FROM product_relationships WHERE main_product_id=? AND related_product_id=? AND relationship_type=?", (payload.main_product_id,payload.related_product_id,payload.relationship_type)).fetchone()
    return dict(row)


@app.delete("/api/v1/admin/product-relationships/{relationship_id}")
def remove_product_relationship(relationship_id: str, staff: dict = Depends(require_admin)) -> dict:
    with connect() as db:
        result = db.execute("DELETE FROM product_relationships WHERE id=?", (relationship_id,))
        if not result.rowcount:
            raise HTTPException(status_code=404, detail="Relationship not found")
    return {"id": relationship_id, "deleted": True}


@app.get("/api/v1/dashboard/summary")
def dashboard_summary(staff: dict = Depends(require_salesperson)) -> dict:
    with connect() as db:
        counts = db.execute("SELECT COUNT(*) AS leads, SUM(CASE WHEN handoff_requested=1 THEN 1 ELSE 0 END) AS handoffs FROM customers").fetchone()
        stages = db.execute("SELECT sales_stage,COUNT(*) AS count FROM customers GROUP BY sales_stage").fetchall()
        appointments = db.execute("SELECT COUNT(*) FROM appointments WHERE status='requested'").fetchone()[0]
        orders = db.execute("SELECT COUNT(*) FROM orders WHERE status IN ('pending_approval','pending_confirmation')").fetchone()[0]
    return {"leads": counts["leads"], "handoffs": counts["handoffs"] or 0, "appointments_to_confirm": appointments, "orders_pending_approval": orders, "stages": {row["sales_stage"]: row["count"] for row in stages}}


@app.post("/api/v1/conversations/{customer_id}/messages", response_model=ConversationReply)
def send_message(customer_id: str, payload: MessageIn, x_customer_token: str | None = Header(default=None)) -> ConversationReply:
    row = get_customer_row(customer_id)
    context = decode_context(row["context_json"])
    context = restore_conversation_context(customer_id, context)
    context = extract_context(payload.message, context)
    normalize_catalogue_interest(context)
    stage = stage_after_message(context, row["sales_stage"])
    action = choose_next_action(context, stage, payload.message)
    human_requested_this_turn = action["action"] in {"human_handoff", "payment_help"}
    direct_reply = None
    cart_added = None
    if CART_RE.search(payload.message) or action["action"] == "add_to_cart":
        cart_candidates = recommended_products(context)
        cart_product = select_order_product(cart_candidates, payload.message, context)
        if cart_product:
            with connect() as db:
                existing_cart_item = db.execute("SELECT quantity FROM cart_items WHERE customer_id=? AND product_id=?", (customer_id, cart_product["id"])).fetchone()
                new_quantity = min(100, (existing_cart_item["quantity"] if existing_cart_item else 0) + 1)
                db.execute("INSERT INTO cart_items(customer_id,product_id,quantity,updated_at) VALUES(?,?,?,?) ON CONFLICT(customer_id,product_id) DO UPDATE SET quantity=excluded.quantity,updated_at=excluded.updated_at", (customer_id, cart_product["id"], new_quantity, now()))
            cart_added = cart_product
            if action["action"] == "add_to_cart":
                action = {"action": "cart_item_added", "product": cart_product, "quantity": new_quantity, "reason": "Added the selected catalogue product to the customer's cart."}
                direct_reply = f"Added {cart_product['name']} to your cart. You can review it on the Cart page. Would you also like a salesperson to confirm availability?"
        else:
            action = {"action": "select_product", "reason": "More than one product could match the cart request.", "products": cart_candidates, "question": "Which product should I add? Reply with the model name or option number."}
            context["awaiting_field"] = "cart_product_selection"
            if human_requested_this_turn:
                context["handoff_reason"] = "Customer requested an availability confirmation while selecting a cart item."
                direct_reply = f"{action['question']} I’ll also keep your availability request in the salesperson queue."
    self_service_actions = {"place_order", "create_appointment", "clarify_appointment", "recommend", "answer", "clarify_use", "clarify_budget", "track_order", "add_to_cart", "related_recommendations", "select_product", "cancel_order_request", "smalltalk", "apology", "greet", "discover", "no_match", "cart_item_added", "appointment_created"}
    if row["handoff_requested"] and action["action"] not in self_service_actions | {"human_handoff", "payment_help"}:
        context.pop("awaiting_field", None)
        action = {"action": "awaiting_salesperson", "reason": "A salesperson is handling this conversation."}
        reply = "I’ve added your message to the conversation. A salesperson will reply here."
        stamp = now()
        with connect() as db:
            db.execute("INSERT INTO messages VALUES(?,?,?,?,?)", (str(uuid4()), customer_id, "customer", payload.message, stamp))
            db.execute("INSERT INTO messages VALUES(?,?,?,?,?)", (str(uuid4()), customer_id, "assistant", reply, now()))
            db.execute(
                "UPDATE customers SET context_json=?, sales_stage='human_handoff', handoff_requested=1 WHERE id=?",
                (json.dumps(context), customer_id),
            )
        return ConversationReply(customer_id=customer_id, reply=reply, sales_stage="human_handoff", context=context,
                                 next_action=action, handoff_required=True)
    if action["action"] == "track_order":
        verify_customer_token(customer_id, x_customer_token)
        with connect() as db:
            orders = db.execute("""SELECT o.id,o.status,o.payment_status,o.fulfillment_status,o.quantity,o.created_at,p.name AS product_name,p.price,p.currency
                FROM orders o JOIN products p ON p.id=o.product_id WHERE o.customer_id=? ORDER BY o.created_at DESC""", (customer_id,)).fetchall()
        reference = re.search(r"\b([a-f0-9]{8,36})\b", payload.message, re.IGNORECASE)
        if reference:
            prefix = reference.group(1).casefold()
            matches = [dict(order) for order in orders if order["id"].casefold().startswith(prefix)]
            if matches:
                order = matches[0]
                direct_reply = f"Order {order['id'][:8]} for {order['product_name']}: {order['status'].replace('_',' ')}, payment {order['payment_status'].replace('_',' ')}, delivery {order['fulfillment_status'].replace('_',' ')}."
            else:
                direct_reply = f"I couldn’t find an order matching {prefix} in your account. Here are your recent orders: " + (", ".join(f"{o['id'][:8]} — {o['product_name']}" for o in orders[:5]) if orders else "there are no orders on this account yet.")
        elif orders:
            choices = "\n".join(f"• {order['id'][:8]} — {order['product_name']} ({order['status'].replace('_',' ')}, {order['fulfillment_status'].replace('_',' ')})" for order in orders[:5])
            direct_reply = f"Here are your recent orders. Send the reference to see full status:\n{choices}"
        else:
            direct_reply = "There are no orders on this customer profile yet. If you used a different profile, start a chat with that profile or ask a salesperson for help."
    if action["action"] == "related_recommendations":
        main_products = recommended_products(context)
        if not main_products:
            main_products = catalogue_matches(context)
        related = related_for_products(main_products, context, exclude_ids=set(context.get("last_recommended_product_ids", [])))
        action["related_products"] = related
        action["products"] = main_products
        if related:
            lines = []
            for item in related:
                product = item["related_product"]
                price = f"{product.get('currency','PKR')} {int(product['price']):,}" if product.get("price") is not None else "price on request"
                lines.append(f"• {product['name']} — {price}. {item['reason']} (match {item['relevance_score']:.0%})")
            direct_reply = "Here are related options from our catalogue:\n" + "\n".join(lines)
        else:
            budget_max = context.get("budget_max_pkr")
            if budget_max is not None and main_products:
                broader_context = {**context, "budget_min_pkr": None, "budget_max_pkr": None, "budget_flexible": True, "result_limit": 10, "price_order": "ASC"}
                broader_options = catalogue_matches(broader_context)
                main_ids = {product["id"] for product in main_products}
                next_up = min((product for product in broader_options if product["id"] not in main_ids and product.get("price") is not None and product["price"] > budget_max), key=lambda product: product["price"], default=None)
                if next_up:
                    context["pending_budget_alternative"] = True
                    current = main_products[0]
                    direct_reply = (f"{current['name']} is the only match in your PKR {budget_max:,} maximum. "
                                    f"The next higher option is {next_up['name']} at PKR {int(next_up['price']):,}, above your budget. "
                                    "Say ‘show higher options’ if you’d like to compare it.")
                else:
                    direct_reply = f"{main_products[0]['name']} is the only match within your PKR {budget_max:,} budget. I don’t have another verified related product for it in the catalogue."
            elif len(main_products) > 1:
                direct_reply = f"I’ve already shown all {len(main_products)} current matches in this category. I can compare their specifications or narrow them by price, GPU, memory, or intended use."
            else:
                direct_reply = "I don’t have a verified complementary product linked to this item yet. I can still compare other products in the same category if you tell me what matters most."
    if action["action"] == "human_handoff" and cart_added:
        direct_reply = f"Added {cart_added['name']} to your cart, and I’ve sent a salesperson your request to confirm availability."
    if action["action"] == "clarify_use":
        starts = laptop_price_starts()
        office = next((price for category, price in starts.items() if "office" in category.casefold()), None)
        gaming = next((price for category, price in starts.items() if "gaming" in category.casefold()), None)
        price_note = ""
        if office is not None and gaming is not None:
            price_note = f" Used office models start at PKR {office:,}; gaming models start at PKR {gaming:,}."
        action["question"] = (action.get("question", "What will you use the laptop for?") + price_note)
    if action["action"] == "discover":
        categories = catalogue_categories()
        action["available_categories"] = categories
        if categories:
            action["question"] = f"What product or service are you looking for? The catalogue currently includes {', '.join(categories)}. I can check other items too."
    if action["action"] == "recommend":
        products = catalogue_matches(context)
        action["products"] = products
        context["last_recommended_product_ids"] = [product["id"] for product in products]
        if products:
            action["related_products"] = related_for_products(products, context, limit=3, exclude_ids={product["id"] for product in products})
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
        context["last_recommended_product_ids"] = [product["id"] for product in action["products"]]
    if action["action"] == "create_appointment":
        requested_time = context.get("appointment_requested_time")
        if not requested_time:
            action = {"action": "clarify_appointment", "reason": "An appointment time is required.", "question": "What day and time should I request?"}
            context["awaiting_field"] = "appointment_time"
        else:
            appointment_id, stamp = str(uuid4()), now()
            with connect() as db:
                db.execute(
                    "INSERT INTO appointments(id,customer_id,requested_time,created_at) VALUES(?,?,?,?)",
                    (appointment_id, customer_id, requested_time, stamp),
                )
            context["last_appointment_id"] = appointment_id
            context["last_appointment_status"] = "requested"
            stage = "appointment"
            context.pop("pending_appointment", None)
            context.pop("appointment_requested_time", None)
            context.pop("awaiting_field", None)
            action = {"action": "appointment_created", "appointment_id": appointment_id, "requested_time": requested_time}
            direct_reply = f"I’ve sent an appointment request for {requested_time}. It’s waiting for the sales team to confirm; this isn’t confirmed on the calendar yet."
    if action["action"] == "cancel_order_request":
        context.pop("awaiting_field", None)
        direct_reply = "No problem—I haven’t placed an order. Tell me if you’d like to compare another option."
    if action["action"] == "place_order":
        if re.search(r"\b(?:another|a new|one more)\s+order\b", payload.message, re.IGNORECASE):
            context["new_order_requested"] = True
        products = recommended_products(context)
        selected_product = select_order_product(products, payload.message, context)
        if selected_product is None:
            action = {
                "action": "select_product",
                "reason": "More than one catalogue item could match this order request.",
                "products": products,
            }
            choices = [
                f"{index}. {product['name']}" + (f" — {product['currency']} {int(product['price']):,}" if product.get("price") is not None else "")
                for index, product in enumerate(products[:5], start=1)
            ]
            action["question"] = "Which one should I put in the order request? Reply with the model name, GPU, or list number.\n" + "\n".join(choices)
            context["awaiting_field"] = "product_selection"
        else:
            quantity_match = re.search(r"\b(?:quantity|qty)\s*(\d{1,2})\b", payload.message, re.IGNORECASE)
            quantity = max(1, min(100, int(quantity_match.group(1)))) if quantity_match else 1
            existing_id = context.get("last_order_id")
            existing = None
            if existing_id and context.get("last_order_product_id") == selected_product["id"] and not context.get("new_order_requested") and not re.search(r"\b(?:another|new|one more)\b", payload.message, re.IGNORECASE):
                with connect() as db:
                    existing = db.execute("SELECT status FROM orders WHERE id=?", (existing_id,)).fetchone()
            if existing and existing["status"] in {"pending_confirmation", "pending_approval", "confirmed"}:
                order_id = existing_id
                existing_label = "confirmed" if existing["status"] == "confirmed" else "still awaiting salesperson confirmation"
                direct_reply = f"You already have an order request for {selected_product['name']} ({existing_label}). I haven’t created a duplicate. Reference: {order_id[:8]}."
                action.update({"action": "order_existing", "order_id": order_id, "product": selected_product, "quantity": quantity})
            else:
                order_id, stamp = str(uuid4()), now()
                with connect() as db:
                    db.execute(
                        "INSERT INTO orders(id,customer_id,product_id,quantity,status,payment_status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                        (order_id, customer_id, selected_product["id"], quantity, "pending_confirmation", "not_paid", stamp, stamp),
                    )
                context["last_order_id"] = order_id
                context["last_order_product_id"] = selected_product["id"]
                context["last_order_status"] = "pending_confirmation"
                context.pop("new_order_requested", None)
                stage = "order_pending_confirmation"
                action.update({"action": "order_created", "order_id": order_id, "product": selected_product, "quantity": quantity})
                price = f"PKR {int(selected_product['price']):,}" if selected_product.get("price") is not None else "price to be confirmed"
                direct_reply = (f"I’ve recorded your order request for {selected_product['name']} × {quantity} ({price}). "
                                f"It’s awaiting salesperson confirmation; no payment has been taken. Order reference: {order_id[:8]}.")
            context.pop("awaiting_field", None)
    handoff = action["action"] in {"human_handoff", "payment_help"} or human_requested_this_turn
    if handoff:
        stage = "human_handoff"
        context["handoff_reason"] = action["reason"]
    if action["action"] == "payment_help":
        stage = "human_handoff"
        context["handoff_reason"] = "Customer requested payment assistance."
    if action.get("question") == "Do you have a budget range in mind?":
        context["awaiting_field"] = "budget"
    elif action["action"] not in {"clarify_budget", "clarify_use", "clarify_appointment", "select_product", "apology"}:
        context.pop("awaiting_field", None)
    reply = direct_reply or get_provider().reply(payload.message, context, action)
    context.pop("budget_prompt_skipped", None)
    stamp = now()
    with connect() as db:
        db.execute("INSERT INTO messages VALUES(?,?,?,?,?)", (str(uuid4()), customer_id, "customer", payload.message, stamp))
        db.execute("INSERT INTO messages VALUES(?,?,?,?,?)", (str(uuid4()), customer_id, "assistant", reply, now()))
        db.execute(
            "UPDATE customers SET context_json=?, sales_stage=?, handoff_requested=? WHERE id=?",
            (json.dumps(context), stage, int(handoff or row["handoff_requested"]), customer_id),
        )
    return ConversationReply(customer_id=customer_id, reply=reply, sales_stage=stage, context=context, next_action=action, handoff_required=bool(handoff or row["handoff_requested"]))


@app.get("/api/v1/conversations/{customer_id}")
def conversation_history(customer_id: str) -> dict:
    get_customer_row(customer_id)
    with connect() as db:
        rows = db.execute("SELECT id,customer_id,role,content,created_at FROM messages WHERE customer_id=? ORDER BY created_at", (customer_id,)).fetchall()
    return {"customer_id": customer_id, "messages": [dict(row) for row in rows]}


@app.get("/api/v1/sales/handoffs")
def sales_handoffs(staff: dict = Depends(require_salesperson)) -> dict:
    with connect() as db:
        rows = db.execute(
            """SELECT c.*, (SELECT content FROM messages m WHERE m.customer_id=c.id
                   ORDER BY m.created_at DESC LIMIT 1) AS last_message,
                   (SELECT created_at FROM messages m WHERE m.customer_id=c.id
                   ORDER BY m.created_at DESC LIMIT 1) AS last_message_at
               FROM customers c WHERE c.handoff_requested=1
               ORDER BY COALESCE(last_message_at,c.created_at) DESC"""
        ).fetchall()
    return {"handoffs": [
        {"customer": customer_from_row(row).model_dump(), "last_message": row["last_message"],
         "last_message_at": row["last_message_at"]}
        for row in rows
    ]}


@app.post("/api/v1/sales/{customer_id}/messages")
def salesperson_message(customer_id: str, payload: MessageIn, staff: dict = Depends(require_salesperson)) -> dict:
    row = get_customer_row(customer_id)
    if not row["handoff_requested"]:
        raise HTTPException(status_code=409, detail="This conversation is not waiting for a salesperson")
    message_id, stamp = str(uuid4()), now()
    with connect() as db:
        db.execute(
            "INSERT INTO messages(id,customer_id,role,content,created_at) VALUES(?,?,?,?,?)",
            (message_id, customer_id, "salesperson", payload.message, stamp),
        )
    return {"id": message_id, "customer_id": customer_id, "role": "salesperson",
            "content": payload.message, "created_at": stamp}


@app.post("/api/v1/sales/{customer_id}/handoff/resolve")
def resolve_handoff(customer_id: str, staff: dict = Depends(require_salesperson)) -> dict:
    row = get_customer_row(customer_id)
    context = decode_context(row["context_json"])
    context["handoff_resolved_at"] = now()
    with connect() as db:
        db.execute(
            "UPDATE customers SET sales_stage='engaged', handoff_requested=0, context_json=? WHERE id=?",
            (json.dumps(context), customer_id),
        )
    return {"customer_id": customer_id, "handoff_requested": False, "resolved": True}


@app.get("/api/v1/sales/{customer_id}/next-action")
def next_action(customer_id: str, staff: dict = Depends(require_salesperson)) -> dict:
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
def request_handoff(customer_id: str, reason: str = "Salesperson assistance requested", staff: dict = Depends(require_salesperson)) -> dict:
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
        stamp = now()
        db.execute("INSERT INTO orders(id,customer_id,product_id,quantity,status,payment_status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", (order_id, customer_id, payload.product_id, payload.quantity, "pending_confirmation", "not_paid", stamp, stamp))
    return {"id": order_id, "customer_id": customer_id, "product": product["name"], "quantity": payload.quantity, "status": "pending_confirmation", "payment_status": "not_paid", "message": "Order request recorded for salesperson confirmation. No payment has been taken."}


def cart_snapshot(db, customer_id: str) -> dict:
    rows = db.execute(
        """SELECT c.product_id,c.quantity,p.name,p.description,p.price,p.currency
           FROM cart_items c JOIN products p ON p.id=c.product_id
           WHERE c.customer_id=? AND p.active=1 ORDER BY p.name""", (customer_id,)
    ).fetchall()
    items = [dict(row) | {"line_total": (row["price"] or 0) * row["quantity"]} for row in rows]
    return {"customer_id": customer_id, "items": items,
            "subtotal": sum(item["line_total"] for item in items),
            "currency": items[0]["currency"] if items else "PKR"}


@app.get("/api/v1/carts/{customer_id}")
def get_cart(customer_id: str) -> dict:
    get_customer_row(customer_id)
    with connect() as db:
        return cart_snapshot(db, customer_id)


@app.post("/api/v1/carts/{customer_id}/items")
def add_cart_item(customer_id: str, payload: CartItemCreate) -> dict:
    get_customer_row(customer_id)
    with connect() as db:
        if not db.execute("SELECT id FROM products WHERE id=? AND active=1", (payload.product_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Product not found")
        current = db.execute("SELECT quantity FROM cart_items WHERE customer_id=? AND product_id=?", (customer_id, payload.product_id)).fetchone()
        quantity = (current["quantity"] if current else 0) + payload.quantity
        if quantity > 100:
            raise HTTPException(status_code=422, detail="Cart quantity cannot exceed 100 per product")
        db.execute("INSERT INTO cart_items(customer_id,product_id,quantity,updated_at) VALUES(?,?,?,?) ON CONFLICT(customer_id,product_id) DO UPDATE SET quantity=excluded.quantity,updated_at=excluded.updated_at", (customer_id, payload.product_id, quantity, now()))
        return cart_snapshot(db, customer_id)


@app.patch("/api/v1/carts/{customer_id}/items/{product_id}")
def update_cart_item(customer_id: str, product_id: str, payload: CartItemUpdate) -> dict:
    get_customer_row(customer_id)
    with connect() as db:
        result = db.execute("UPDATE cart_items SET quantity=?,updated_at=? WHERE customer_id=? AND product_id=?", (payload.quantity, now(), customer_id, product_id))
        if not result.rowcount:
            raise HTTPException(status_code=404, detail="Cart item not found")
        return cart_snapshot(db, customer_id)


@app.delete("/api/v1/carts/{customer_id}/items/{product_id}")
def delete_cart_item(customer_id: str, product_id: str) -> dict:
    get_customer_row(customer_id)
    with connect() as db:
        db.execute("DELETE FROM cart_items WHERE customer_id=? AND product_id=?", (customer_id, product_id))
        return cart_snapshot(db, customer_id)


@app.post("/api/v1/carts/{customer_id}/checkout", status_code=201)
def checkout_cart(customer_id: str, payload: CheckoutCreate) -> dict:
    get_customer_row(customer_id)
    checkout_id = str(uuid4())
    stamp = now()
    with connect() as db:
        cart = cart_snapshot(db, customer_id)
        if not cart["items"]:
            raise HTTPException(status_code=400, detail="Your cart is empty")
        for item in cart["items"]:
            db.execute("INSERT INTO orders(id,checkout_id,customer_id,product_id,quantity,status,payment_status,payment_method,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (str(uuid4()), checkout_id, customer_id, item["product_id"], item["quantity"], "pending_confirmation", "not_paid", payload.payment_method, stamp, stamp))
        db.execute("DELETE FROM cart_items WHERE customer_id=?", (customer_id,))
        if payload.payment_method == "bank_transfer":
            customer = db.execute("SELECT context_json FROM customers WHERE id=?", (customer_id,)).fetchone()
            context = decode_context(customer["context_json"])
            context["handoff_reason"] = "Customer requested bank transfer checkout; share verified payment instructions after confirming the order."
            db.execute("UPDATE customers SET handoff_requested=1,sales_stage='human_handoff',context_json=? WHERE id=?", (json.dumps(context), customer_id))
    return {"checkout_id": checkout_id, "customer_id": customer_id, "items": cart["items"], "subtotal": cart["subtotal"], "currency": cart["currency"], "payment_method": payload.payment_method, "payment_status": "not_paid", "status": "pending_confirmation", "message": "Order request recorded. Payment has not been taken. A salesperson will confirm availability and share bank transfer instructions if selected."}


@app.get("/api/v1/admin/summary")
def admin_summary(staff: dict = Depends(require_admin)) -> dict:
    with connect() as db:
        row = db.execute(
            """SELECT COUNT(*) AS total,
                 SUM(CASE WHEN status IN ('pending_approval','pending_confirmation') THEN 1 ELSE 0 END) AS awaiting_confirmation,
                 SUM(CASE WHEN status='confirmed' THEN 1 ELSE 0 END) AS confirmed,
                 SUM(CASE WHEN payment_status='not_paid' THEN 1 ELSE 0 END) AS not_paid,
                 SUM(CASE WHEN payment_status='paid' THEN 1 ELSE 0 END) AS paid,
                 SUM(CASE WHEN fulfillment_status='delivered' THEN 1 ELSE 0 END) AS delivered
               FROM orders"""
        ).fetchone()
        appointments = db.execute(
            "SELECT SUM(CASE WHEN status='requested' THEN 1 ELSE 0 END) AS requested,SUM(CASE WHEN status='confirmed' THEN 1 ELSE 0 END) AS confirmed FROM appointments"
        ).fetchone()
    result = {key: row[key] or 0 for key in ("total", "awaiting_confirmation", "confirmed", "not_paid", "paid", "delivered")}
    result["appointments_requested"] = appointments["requested"] or 0
    result["appointments_confirmed"] = appointments["confirmed"] or 0
    return result


@app.get("/api/v1/admin/orders")
def admin_orders(status: str | None = None, payment_status: str | None = None, fulfillment_status: str | None = None, staff: dict = Depends(require_admin)) -> list[dict]:
    allowed = {
        "status": {"pending_approval", "pending_confirmation", "confirmed", "cancelled"},
        "payment_status": {"not_paid", "paid", "refunded"},
        "fulfillment_status": {"not_started", "processing", "shipped", "delivered", "returned"},
    }
    filters = {"status": status, "payment_status": payment_status, "fulfillment_status": fulfillment_status}
    clauses, values = [], []
    for column, value in filters.items():
        if value:
            if value not in allowed[column]:
                raise HTTPException(status_code=422, detail=f"Invalid {column} filter")
            clauses.append(f"o.{column}=?")
            values.append(value)
    with connect() as db:
        rows = db.execute(
            """SELECT o.*, c.name AS customer_name, c.contact AS customer_contact,
                      p.name AS product_name, p.price AS unit_price, p.currency
               FROM orders o JOIN customers c ON c.id=o.customer_id
               JOIN products p ON p.id=o.product_id""" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY o.created_at DESC",
            values,
        ).fetchall()
    return [dict(row) for row in rows]


@app.patch("/api/v1/admin/orders/{order_id}")
def update_admin_order(order_id: str, payload: AdminOrderUpdate, staff: dict = Depends(require_admin)) -> dict:
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="Provide at least one order field to update")
    if any(changes.get(field) is None for field in ("status", "payment_status", "fulfillment_status") if field in changes):
        raise HTTPException(status_code=422, detail="Order and payment states cannot be null")
    with connect() as db:
        exists = db.execute("SELECT status,payment_status,fulfillment_status,customer_id,product_id FROM orders WHERE id=?", (order_id,)).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="Order not found")
        assignments = []
        values = []
        for field, value in changes.items():
            assignments.append(f"{field}=?")
            values.append(value)
        if changes.get("status") == "confirmed":
            assignments.append("confirmed_at=?")
            values.append(now())
        elif changes.get("status") in {"pending_confirmation", "cancelled"}:
            assignments.append("confirmed_at=NULL")
        if "payment_status" in changes:
            assignments.append("payment_updated_at=?")
            values.append(now())
        assignments.append("updated_by_user_id=?")
        values.append(staff["id"])
        assignments.append("updated_at=?")
        values.extend((now(), order_id))
        db.execute(f"UPDATE orders SET {', '.join(assignments)} WHERE id=?", values)
        updated = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        customer = db.execute("SELECT context_json,sales_stage,handoff_requested FROM customers WHERE id=?", (exists["customer_id"],)).fetchone()
        product = db.execute("SELECT name FROM products WHERE id=?", (exists["product_id"],)).fetchone()
        context = decode_context(customer["context_json"])
        notify = None
        next_stage = customer["sales_stage"]
        if "status" in changes and changes["status"] != exists["status"]:
            context["last_order_status"] = changes["status"]
            if changes["status"] == "confirmed":
                next_stage = "order_confirmed" if not customer["handoff_requested"] else next_stage
                notify = f"Sales has confirmed your order {order_id[:8]} for {product['name']}. Reply here if you need help with payment or delivery."
            elif changes["status"] == "cancelled":
                notify = f"Your order request {order_id[:8]} for {product['name']} was cancelled. Reply here if you need help."
        if "payment_status" in changes and changes["payment_status"] != exists["payment_status"]:
            context["last_order_payment_status"] = changes["payment_status"]
            if changes["payment_status"] == "paid":
                notify = f"Sales has recorded payment as received for order {order_id[:8]} ({product['name']})."
            elif changes["payment_status"] == "refunded":
                notify = f"Sales has recorded a refund for order {order_id[:8]} ({product['name']})."
        if "fulfillment_status" in changes and changes["fulfillment_status"] != exists["fulfillment_status"]:
            context["last_order_fulfillment_status"] = changes["fulfillment_status"]
            fulfillment_messages = {
                "shipped": f"Your order {order_id[:8]} ({product['name']}) has been marked as shipped.",
                "delivered": f"Your order {order_id[:8]} ({product['name']}) has been marked as delivered.",
                "returned": f"Your order {order_id[:8]} ({product['name']}) has been marked as returned.",
            }
            notify = fulfillment_messages.get(changes["fulfillment_status"], notify)
        db.execute("UPDATE customers SET context_json=?,sales_stage=? WHERE id=?", (json.dumps(context), next_stage, exists["customer_id"]))
        if notify:
            db.execute(
                "INSERT INTO messages(id,customer_id,role,content,created_at) VALUES(?,?,?,?,?)",
                (str(uuid4()), exists["customer_id"], "salesperson", notify, now()),
            )
    return dict(updated)


@app.get("/api/v1/admin/appointments")
def admin_appointments(staff: dict = Depends(require_admin)) -> list[dict]:
    with connect() as db:
        rows = db.execute(
            """SELECT a.*,c.name AS customer_name,c.contact AS customer_contact
               FROM appointments a JOIN customers c ON c.id=a.customer_id
               ORDER BY a.created_at DESC"""
        ).fetchall()
    return [dict(row) for row in rows]


@app.patch("/api/v1/admin/appointments/{appointment_id}")
def update_admin_appointment(appointment_id: str, payload: AdminAppointmentUpdate, staff: dict = Depends(require_admin)) -> dict:
    changes = payload.model_dump(exclude_unset=True)
    with connect() as db:
        existing = db.execute("SELECT status,customer_id,requested_time FROM appointments WHERE id=?", (appointment_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Appointment not found")
        if "notes" in changes:
            db.execute("UPDATE appointments SET status=?,notes=?,updated_by_user_id=? WHERE id=?", (changes["status"], changes["notes"], staff["id"], appointment_id))
        else:
            db.execute("UPDATE appointments SET status=?,updated_by_user_id=? WHERE id=?", (changes["status"], staff["id"], appointment_id))
        updated = db.execute("SELECT * FROM appointments WHERE id=?", (appointment_id,)).fetchone()
        if changes["status"] != existing["status"]:
            customer_row = db.execute("SELECT context_json FROM customers WHERE id=?", (existing["customer_id"],)).fetchone()
            context = decode_context(customer_row["context_json"])
            context["last_appointment_status"] = changes["status"]
            db.execute("UPDATE customers SET context_json=? WHERE id=?", (json.dumps(context), existing["customer_id"]))
            if changes["status"] in {"confirmed", "cancelled"}:
                verb = "confirmed" if changes["status"] == "confirmed" else "cancelled"
                db.execute(
                    "INSERT INTO messages(id,customer_id,role,content,created_at) VALUES(?,?,?,?,?)",
                    (str(uuid4()), existing["customer_id"], "salesperson", f"Sales has {verb} your appointment request for {existing['requested_time']}.", now()),
                )
    return dict(updated)


@app.get("/api/v1/sales/workspace")
def sales_workspace(staff: dict = Depends(require_salesperson)) -> dict:
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
    return {"conversations": items, "staff": staff}
