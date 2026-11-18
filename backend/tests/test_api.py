import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./test.db")
    monkeypatch.setenv("LLM_PROVIDER", "local")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("ADMIN_LOGIN_PASSWORD", "")
    monkeypatch.setenv("SALESPERSON_LOGIN_PASSWORD", "")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    get_settings.cache_clear()
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()


def test_catalogue_categories_and_salesperson_page_are_available(client):
    categories = {product["category"] for product in client.get("/api/v1/products").json()}
    assert {"Gaming Laptops", "Office Laptops", "Solar", "Software"} <= categories
    page = client.get("/sales")
    assert page.status_code == 200
    assert "Sales workspace" in page.text
    assert "HandOffs" in page.text
    assert "/api/v1/sales/handoffs" in page.text
    home = client.get("/")
    assert 'id="widget-new-chat"' in home.text
    assert "/assets/chat.js?v=" in home.text
    admin = client.get("/admin")
    assert admin.status_code == 200
    assert "Orders &amp; payments" in admin.text
    assert "/api/v1/admin/orders" in admin.text
    assert "Appointment requests" in admin.text


def test_greeting_offers_live_catalogue_categories(client):
    customer = client.post("/api/v1/customers", json={"name": "Test", "channel": "web"}).json()
    result = client.post(f"/api/v1/conversations/{customer['id']}/messages", json={"message": "hi"}).json()
    assert result["next_action"]["action"] == "discover"
    assert "Gaming Laptops" in result["reply"]
    assert "Solar" in result["reply"]


def test_chat_page_has_new_chat_button_and_versioned_script(client):
    page = client.get("/chat")
    assert page.status_code == 200
    assert 'id="new-chat"' in page.text
    assert "/assets/chat.js?v=" in page.text


def test_chat_stays_in_category_and_recovers_from_budget_no_match(client):
    customer = client.post("/api/v1/customers", json={"name": "Test", "channel": "web"}).json()
    customer_id = customer["id"]

    result = client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "I need a gaming laptop"}).json()
    assert result["next_action"]["action"] == "recommend"
    assert all("Lenovo Legion" in product["name"] for product in result["next_action"]["products"])

    result = client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "100,000 - 500000 PKR"}).json()
    assert result["next_action"]["action"] == "no_match"
    assert "PKR 680,000" in result["reply"]

    result = client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "show them"}).json()
    assert result["next_action"]["action"] == "recommend"
    assert len(result["next_action"]["products"]) == 5


@pytest.mark.parametrize("budget", ["6 to 7 lakh", "6-7 lakg"])
def test_lakh_budget_range_filters_to_the_requested_price_band(client, budget):
    customer = client.post("/api/v1/customers", json={"name": "Budget buyer", "channel": "web"}).json()
    customer_id = customer["id"]
    broad = client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "I need a laptop"}).json()
    assert broad["next_action"]["action"] == "clarify_use"
    assert "office" in broad["reply"].lower() and "gaming" in broad["reply"].lower()
    unclear = client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "a"}).json()
    assert unclear["next_action"]["action"] == "clarify_use"
    assert "catalogue currently includes" not in unclear["reply"].lower()
    result = client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": budget}).json()
    products = result["next_action"]["products"]
    assert (result["context"]["budget_min_pkr"], result["context"]["budget_max_pkr"]) == (600_000, 700_000)
    assert len(products) == 1
    assert products[0]["price"] == 680_000


def test_office_laptop_preferences_and_price_order_stay_in_context(client):
    customer = client.post("/api/v1/customers", json={"name": "Office buyer", "channel": "web"}).json()
    customer_id = customer["id"]
    result = client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "I need an office laptop"}).json()
    assert len(result["next_action"]["products"]) == 3
    assert all("office" in product["description"].casefold() for product in result["next_action"]["products"])

    cheapest = client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "cheapest one"}).json()
    assert cheapest["next_action"]["products"][0]["price"] == 58_500
    assert cheapest["next_action"].get("question") is None
    expensive = client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "i mean most expensive"}).json()
    assert expensive["next_action"]["products"][0]["price"] == 78_000


def test_invalid_budget_reply_keeps_the_current_question_and_category(client):
    customer = client.post("/api/v1/customers", json={"name": "Budget reply", "channel": "web"}).json()
    customer_id = customer["id"]
    client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "I need a gaming laptop"})
    result = client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "a"}).json()
    assert result["next_action"]["action"] == "clarify_budget"
    assert result["context"]["product_category"] == "laptops"
    assert "600,000" in result["reply"]


def test_legacy_no_match_conversation_can_recover(client):
    customer = client.post("/api/v1/customers", json={"name": "Test", "channel": "web"}).json()
    customer_id = customer["id"]
    client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "I need a gaming laptop"})
    client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "100,000 - 500000 PKR"})

    with sqlite3.connect("test.db") as db:
        stored = json.loads(db.execute("SELECT context_json FROM customers WHERE id=?", (customer_id,)).fetchone()[0])
        stored.pop("budget_no_match", None)  # Simulate context written by the previous app version.
        db.execute("UPDATE customers SET context_json=? WHERE id=?", (json.dumps(stored), customer_id))

    result = client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "show"}).json()
    assert result["next_action"]["action"] == "recommend"
    assert len(result["next_action"]["products"]) == 5


def test_generic_unknown_product_and_car_requests_do_not_invent_catalogue_items(client):
    customer = client.post("/api/v1/customers", json={"name": "Test", "channel": "web"}).json()
    for message in ("I need an electric car", "I need a blender"):
        result = client.post(f"/api/v1/conversations/{customer['id']}/messages", json={"message": message}).json()
        assert result["next_action"]["action"] == "no_match"
        assert result["next_action"]["products"] == []
        assert "current catalogue" in result["reply"].lower() or "product catalogue" in result["reply"].lower()


def test_product_limit_gpu_and_budget_filters(client):
    customer = client.post("/api/v1/customers", json={"name": "Test", "channel": "web"}).json()
    customer_id = customer["id"]
    result = client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "show only 1 laptop, RTX 5080, 16GB VRAM"}).json()
    assert len(result["next_action"]["products"]) == 1
    assert result["next_action"]["products"][0]["gpu_model"] == "RTX 5080"
    assert result["next_action"]["products"][0]["vram_gb"] == 16


def test_common_typo_resolves_to_catalogue_product_category(client):
    customer = client.post("/api/v1/customers", json={"name": "Test", "channel": "web"}).json()
    result = client.post(f"/api/v1/conversations/{customer['id']}/messages", json={"message": "show me the latop"}).json()
    assert result["next_action"]["action"] == "clarify_use"
    assert result["context"]["product_category"] == "laptops"
    assert "office laptop" in result["reply"].lower()


def test_cheapest_and_most_expensive_respect_requested_count(client):
    customer = client.post("/api/v1/customers", json={"name": "Test", "channel": "web"}).json()
    customer_id = customer["id"]
    client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "I need a gaming laptop"})

    cheapest = client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "show cheapest laptop"}).json()
    assert len(cheapest["next_action"]["products"]) == 1
    assert cheapest["next_action"]["products"][0]["gpu_model"] == "RTX 5060"

    expensive = client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "show most expensive laptop"}).json()
    assert len(expensive["next_action"]["products"]) == 1
    assert expensive["next_action"]["products"][0]["gpu_model"] == "RTX 5090"


def test_new_search_resets_prior_category_budget(client):
    customer = client.post("/api/v1/customers", json={"name": "Test", "channel": "web"}).json()
    customer_id = customer["id"]
    client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "I need a gaming laptop under 500k"})
    result = client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "I need a laptop"}).json()
    assert result["next_action"]["action"] == "clarify_use"
    result = client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "office work"}).json()
    assert result["next_action"]["action"] == "recommend"
    assert len(result["next_action"]["products"]) > 0
    assert result["context"].get("budget_max_pkr") is None


def test_handoff_appointment_and_order_boundaries(client):
    customer = client.post("/api/v1/customers", json={"name": "Test", "channel": "web"}).json()
    customer_id = customer["id"]
    handoff = client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "I need a salesperson"}).json()
    assert handoff["handoff_required"] is True
    appointment = client.post(f"/api/v1/appointments/{customer_id}", json={"requested_time": "Friday 3pm"}).json()
    assert appointment["status"] == "requested"
    order = client.post(f"/api/v1/orders/{customer_id}", headers={"X-Customer-Token": customer["customer_token"]}, json={"product_id": "lenovo-rtx5060-32gb", "quantity": 1}).json()
    assert order["status"] == "pending"
    assert "explicitly confirm" in order["message"].lower()
    summary = client.get("/api/v1/dashboard/summary").json()
    assert summary["handoffs"] == 1
    assert summary["appointments_to_confirm"] == 1
    assert summary["orders_pending_approval"] == 0


def test_handoff_appears_by_customer_name_and_salesperson_can_reply(client):
    customer = client.post("/api/v1/customers", json={"name": "Amina Khan", "channel": "web"}).json()
    customer_id = customer["id"]
    response = client.post(
        f"/api/v1/conversations/{customer_id}/messages",
        json={"message": "I wanna talk to a human"},
    ).json()
    assert response["handoff_required"] is True

    follow_up = client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "thank u"}).json()
    assert follow_up["next_action"]["action"] == "awaiting_salesperson"
    assert "salesperson will reply" in follow_up["reply"].lower()
    assert follow_up["handoff_required"] is True

    handoffs = client.get("/api/v1/sales/handoffs").json()["handoffs"]
    assert len(handoffs) == 1
    assert handoffs[0]["customer"]["name"] == "Amina Khan"
    assert handoffs[0]["customer"]["id"] == customer_id

    reply = client.post(
        f"/api/v1/sales/{customer_id}/messages",
        json={"message": "Hi Amina, I’m a salesperson. How can I help?"},
    )
    assert reply.status_code == 200
    assert reply.json()["role"] == "salesperson"
    history = client.get(f"/api/v1/conversations/{customer_id}").json()["messages"]
    assert history[-1]["role"] == "salesperson"
    assert history[-1]["content"].startswith("Hi Amina")

    after_sales_reply = client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "I need a laptop"}).json()
    assert after_sales_reply["next_action"]["action"] == "awaiting_salesperson"
    assert len(client.get("/api/v1/sales/handoffs").json()["handoffs"]) == 1
    resolved = client.post(f"/api/v1/sales/{customer_id}/handoff/resolve").json()
    assert resolved["resolved"] is True
    assert client.get("/api/v1/sales/handoffs").json()["handoffs"] == []


def test_salesperson_cannot_reply_to_an_unhanded_conversation(client):
    customer = client.post("/api/v1/customers", json={"name": "Customer", "channel": "web"}).json()
    response = client.post(
        f"/api/v1/sales/{customer['id']}/messages", json={"message": "Can I help?"}
    )
    assert response.status_code == 409


def test_order_request_creates_pending_order_and_admin_can_confirm_and_record_payment(client):
    customer = client.post("/api/v1/customers", json={"name": "Order customer", "channel": "web"}).json()
    customer_id = customer["id"]
    recommended = client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "I need a gaming laptop RTX 5060"}).json()
    assert recommended["next_action"]["action"] == "recommend"
    headers = {"X-Customer-Token": customer["customer_token"]}
    order_reply = client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "place my order"}).json()
    assert order_reply["next_action"]["action"] == "order_review"
    assert "please review" in order_reply["reply"].lower()
    assert "no order or payment has been created" in order_reply["reply"].lower()
    duplicate = client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "place my order"}).json()
    assert duplicate["next_action"]["action"] == "order_review_pending"
    assert client.get("/api/v1/admin/orders").json() == []
    confirmed_request = client.post(f"/api/v1/conversations/{customer_id}/messages", headers=headers, json={"message": "confirm order"}).json()
    assert "awaiting salesperson confirmation" in confirmed_request["reply"]
    assert "no payment has been taken" in confirmed_request["reply"].lower()

    orders = client.get("/api/v1/admin/orders").json()
    assert len(orders) == 1
    order_id = orders[0]["id"]
    assert orders[0]["status"] == "pending_confirmation"
    assert orders[0]["payment_status"] == "not_paid"

    confirmed = client.patch(f"/api/v1/admin/orders/{order_id}", json={"status": "confirmed"})
    assert confirmed.status_code == 200
    assert confirmed.json()["confirmed_at"] is not None
    assert confirmed.json()["updated_by_user_id"] == "admin-001"
    assert client.get(f"/api/v1/conversations/{customer_id}").json()["messages"][-1]["role"] == "salesperson"
    paid = client.patch(f"/api/v1/admin/orders/{order_id}", json={"payment_status": "paid", "payment_method": "bank transfer", "payment_reference": "verified-ref-1"})
    assert paid.json()["payment_status"] == "paid"
    assert "recorded payment as received" in client.get(f"/api/v1/conversations/{customer_id}").json()["messages"][-1]["content"].lower()
    summary = client.get("/api/v1/admin/summary").json()
    assert summary["confirmed"] == 1
    assert summary["paid"] == 1


def test_admin_rejects_invalid_or_null_order_states(client):
    customer = client.post("/api/v1/customers", json={"name": "Validation customer", "channel": "web"}).json()
    review = client.post("/api/v1/orders/" + customer["id"], headers={"X-Customer-Token": customer["customer_token"]}, json={"product_id": "lenovo-rtx5060-32gb"}).json()
    created = client.post(f"/api/v1/order-reviews/{review['review_id']}/confirm?customer_id={customer['id']}", headers={"X-Customer-Token": customer["customer_token"]}).json()
    order_id = created["order_ids"][0]
    invalid = client.patch(f"/api/v1/admin/orders/{order_id}", json={"status": "maybe"})
    null_status = client.patch(f"/api/v1/admin/orders/{order_id}", json={"status": None})
    assert invalid.status_code == 422
    assert null_status.status_code == 422


def test_ambiguous_order_requires_customer_to_choose_an_offered_product(client):
    customer = client.post("/api/v1/customers", json={"name": "Choosing customer", "channel": "web"}).json()
    customer_id = customer["id"]
    client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "I need a gaming laptop"})
    choice = client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "place my order"}).json()
    assert choice["next_action"]["action"] == "select_product"
    assert "Reply with the model name" in choice["reply"]
    placed = client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "1"}).json()
    assert placed["next_action"]["action"] == "order_review"
    assert placed["next_action"]["items"][0]["product_id"] == "lenovo-rtx5060-32gb"
    assert len(client.get("/api/v1/admin/orders").json()) == 0


def test_misspelled_ordinal_resolves_against_persisted_product_list(client):
    customer = client.post("/api/v1/customers", json={"name": "Ordinal buyer", "channel": "web"}).json()
    cid = customer["id"]
    client.post(f"/api/v1/conversations/{cid}/messages", json={"message": "I need a gaming laptop"})
    client.post(f"/api/v1/conversations/{cid}/messages", json={"message": "place my order"})
    selected = client.post(f"/api/v1/conversations/{cid}/messages", json={"message": "5th"}).json()
    assert selected["next_action"]["action"] == "order_review"
    assert selected["next_action"]["items"][0]["product_id"] == "lenovo-rtx5090-32gb"


def test_existing_orders_migrate_into_admin_payment_fields(client):
    customer = client.post("/api/v1/customers", json={"name": "Legacy order", "channel": "web"}).json()
    with sqlite3.connect("test.db") as db:
        db.execute("DROP TABLE orders")
        db.execute("""CREATE TABLE orders (
            id TEXT PRIMARY KEY, customer_id TEXT NOT NULL REFERENCES customers(id),
            product_id TEXT NOT NULL REFERENCES products(id), quantity INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending_approval', created_at TEXT NOT NULL)""")
        db.execute("INSERT INTO orders VALUES('legacy-order',?,'lenovo-rtx5060-32gb',1,'pending_approval','2026-01-01T00:00:00Z')", (customer["id"],))

    from app.db import initialize_database
    initialize_database()
    with sqlite3.connect("test.db") as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(orders)")}
        migrated = db.execute("SELECT status,payment_status FROM orders WHERE id='legacy-order'").fetchone()
    assert {"payment_status", "payment_reference", "confirmed_at", "fulfillment_status", "updated_at"} <= columns
    assert migrated == ("pending_approval", "not_paid")


def test_payment_request_and_frustration_never_turn_into_fake_products_or_budget_prompts(client):
    customer = client.post("/api/v1/customers", json={"name": "Support customer", "channel": "web"}).json()
    customer_id = customer["id"]
    client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "I need a gaming laptop RTX 5060"})
    payment = client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "I want to make payment"}).json()
    assert payment["next_action"]["action"] == "payment_help"
    assert payment["handoff_required"] is True
    assert "payment" in payment["reply"].lower()

    follow_up = client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "half a million"}).json()
    assert follow_up["next_action"]["action"] == "awaiting_salesperson"
    assert "budget range" not in follow_up["reply"].lower()


def test_human_ambiguity_confirmation_and_frustration_routes_are_clear(client):
    customer = client.post("/api/v1/customers", json={"name": "Customer", "channel": "web"}).json()
    customer_id = customer["id"]
    client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "I need a gaming laptop"})
    for message in ("let me talk to him", "plz confirm"):
        result = client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": message}).json()
        assert result["handoff_required"] is True
    client.post(f"/api/v1/sales/{customer_id}/handoff/resolve")
    apology = client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "are you mad?"}).json()
    assert apology["next_action"]["action"] == "apology"
    assert "sorry" in apology["reply"].lower()
    assert "budget range" not in apology["reply"].lower()


def test_customer_can_request_appointment_and_admin_can_confirm_it(client):
    customer = client.post("/api/v1/customers", json={"name": "Appointment customer", "channel": "web"}).json()
    customer_id = customer["id"]
    prompt = client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "book a demo"}).json()
    assert prompt["next_action"]["action"] == "clarify_appointment"
    assert "what day and time" in prompt["reply"].lower()
    created = client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "Friday at 3pm"}).json()
    assert created["next_action"]["action"] == "appointment_created"
    assert "waiting for the sales team to confirm" in created["reply"]
    records = client.get("/api/v1/admin/appointments").json()
    assert len(records) == 1
    appointment_id = records[0]["id"]
    confirmed = client.patch(f"/api/v1/admin/appointments/{appointment_id}", json={"status": "confirmed"})
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"
    assert confirmed.json()["updated_by_user_id"] == "admin-001"
    history = client.get(f"/api/v1/conversations/{customer_id}").json()["messages"]
    assert history[-1]["role"] == "salesperson"
    assert "confirmed your appointment" in history[-1]["content"]


def test_duplicate_appointment_request_reuses_existing_record(client):
    customer = client.post("/api/v1/customers", json={"name": "Appointment buyer", "channel": "web"}).json()
    cid = customer["id"]
    first = client.post(f"/api/v1/appointments/{cid}", json={"requested_time": "Friday 3pm"}).json()
    second = client.post(f"/api/v1/appointments/{cid}", json={"requested_time": "Friday 3pm"}).json()
    assert second["id"] == first["id"]
    assert second["duplicate"] is True
    assert len([item for item in client.get("/api/v1/admin/appointments").json() if item["customer_id"] == cid]) == 1


def test_customer_can_finish_order_during_a_salesperson_handoff(client):
    customer = client.post("/api/v1/customers", json={"name": "Handed off buyer", "channel": "web"}).json()
    customer_id = customer["id"]
    client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "I need a gaming laptop RTX 5060"})
    client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "I want to talk to human"})
    sales_reply = client.post(f"/api/v1/sales/{customer_id}/messages", json={"message": "I can help with the order."})
    assert sales_reply.status_code == 200
    placed = client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "place my order"}).json()
    assert placed["next_action"]["action"] == "order_review"
    assert placed["handoff_required"] is True
    assert len(client.get("/api/v1/sales/handoffs").json()["handoffs"]) == 1


def test_legacy_handoff_bot_product_category_is_repaired(client):
    customer = client.post("/api/v1/customers", json={"name": "Legacy handoff", "channel": "web"}).json()
    customer_id = customer["id"]
    with sqlite3.connect("test.db") as db:
        db.execute("UPDATE customers SET context_json=?,handoff_requested=0 WHERE id=?", (json.dumps({"product_category": "to talk to human", "last_customer_message": "I want to talk to human"}), customer_id))
    result = client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "thank u"}).json()
    assert result["next_action"]["action"] == "smalltalk"
    assert "product_category" not in result["context"]


def test_cart_add_update_remove_and_checkout_creates_unpaid_orders(client):
    customer = client.post("/api/v1/customers", json={"name": "Cart buyer", "channel": "web"}).json()
    customer_id = customer["id"]
    headers = {"X-Customer-Token": customer["customer_token"]}
    added = client.post(f"/api/v1/carts/{customer_id}/items", headers=headers, json={"product_id": "lenovo-rtx5060-32gb", "quantity": 1})
    assert added.status_code == 200
    added = client.post(f"/api/v1/carts/{customer_id}/items", headers=headers, json={"product_id": "lenovo-rtx5060-32gb", "quantity": 2}).json()
    assert added["items"][0]["quantity"] == 3
    assert added["subtotal"] == 2_040_000
    updated = client.patch(f"/api/v1/carts/{customer_id}/items/lenovo-rtx5060-32gb", headers=headers, json={"quantity": 1}).json()
    assert updated["items"][0]["quantity"] == 1
    client.post(f"/api/v1/carts/{customer_id}/items", headers=headers, json={"product_id": "hp-840g5-i5-8350u", "quantity": 1})
    checkout = client.post(f"/api/v1/carts/{customer_id}/checkout", headers=headers, json={"payment_method": "cash_on_delivery"})
    assert checkout.status_code == 201
    body = checkout.json()
    assert body["status"] == "pending"
    assert body["message"].startswith("Review this summary")
    assert len(body["items"]) == 2
    assert len(client.get(f"/api/v1/carts/{customer_id}", headers=headers).json()["items"]) == 2
    assert client.get("/api/v1/admin/orders").json() == []
    confirmed = client.post(f"/api/v1/order-reviews/{body['review_id']}/confirm?customer_id={customer_id}", headers=headers).json()
    assert confirmed["payment_status"] == "not_paid"
    assert client.get(f"/api/v1/carts/{customer_id}", headers=headers).json()["items"] == []
    orders = client.get("/api/v1/admin/orders").json()
    created = [order for order in orders if order["id"] in confirmed["order_ids"]]
    assert len(created) == 2
    assert all(order["status"] == "pending_confirmation" and order["payment_status"] == "not_paid" for order in created)


def test_bank_transfer_checkout_requests_sales_help_and_empty_checkout_fails(client):
    customer = client.post("/api/v1/customers", json={"name": "Transfer buyer", "channel": "web"}).json()
    customer_id = customer["id"]
    headers = {"X-Customer-Token": customer["customer_token"]}
    empty = client.post(f"/api/v1/carts/{customer_id}/checkout", headers=headers, json={"payment_method": "bank_transfer"})
    assert empty.status_code == 400
    client.post(f"/api/v1/carts/{customer_id}/items", headers=headers, json={"product_id": "hp-840g5-i5-8350u"})
    result = client.post(f"/api/v1/carts/{customer_id}/checkout", headers=headers, json={"payment_method": "bank_transfer"}).json()
    assert result["status"] == "pending"
    assert client.get("/api/v1/sales/handoffs").json()["handoffs"] == []
    client.post(f"/api/v1/order-reviews/{result['review_id']}/confirm?customer_id={customer_id}", headers=headers)
    handoffs = client.get("/api/v1/sales/handoffs").json()["handoffs"]
    assert any(item["customer"]["id"] == customer_id for item in handoffs)


def test_cart_and_checkout_pages_are_available(client):
    response = client.get("/cart")
    assert response.status_code == 200
    assert 'id="checkout-button"' in response.text
    assert "/assets/store.js" in response.text
    assert 'href="/cart"' in client.get("/").text


def test_customer_order_history_is_limited_to_its_private_customer_token(client):
    first = client.post("/api/v1/customers", json={"name": "First", "channel": "web"}).json()
    second = client.post("/api/v1/customers", json={"name": "Second", "channel": "web"}).json()
    review = client.post(f"/api/v1/orders/{first['id']}", headers={"X-Customer-Token": first["customer_token"]}, json={"product_id": "lenovo-rtx5060-32gb"}).json()
    confirmed = client.post(f"/api/v1/order-reviews/{review['review_id']}/confirm?customer_id={first['id']}", headers={"X-Customer-Token": first["customer_token"]}).json()
    unauthenticated = client.get(f"/api/v1/customers/{first['id']}/orders")
    assert unauthenticated.status_code == 401
    cross_account = client.get(f"/api/v1/customers/{first['id']}/orders", headers={"X-Customer-Token": second["customer_token"]})
    assert cross_account.status_code == 401
    own_orders = client.get(f"/api/v1/customers/{first['id']}/orders", headers={"X-Customer-Token": first["customer_token"]})
    assert [order["id"] for order in own_orders.json()] == confirmed["order_ids"]
    assert first["user_id"] == first["id"]


def test_chat_order_tracking_requires_private_token_and_lists_only_owned_orders(client):
    customer = client.post("/api/v1/customers", json={"name": "Order tracker", "channel": "web"}).json()
    review = client.post(f"/api/v1/orders/{customer['id']}", headers={"X-Customer-Token": customer["customer_token"]}, json={"product_id": "lenovo-rtx5060-32gb"}).json()
    order = client.post(f"/api/v1/order-reviews/{review['review_id']}/confirm?customer_id={customer['id']}", headers={"X-Customer-Token": customer["customer_token"]}).json()
    denied = client.post(f"/api/v1/conversations/{customer['id']}/messages", json={"message": "track my order"})
    assert denied.status_code == 401
    response = client.post(f"/api/v1/conversations/{customer['id']}/messages", headers={"X-Customer-Token": customer["customer_token"]}, json={"message": "track my order"}).json()
    assert order["order_ids"][0][:8] in response["reply"]
    assert "pending confirmation" in response["reply"]


def test_live_handoff_does_not_block_new_product_search_or_appointment(client):
    customer = client.post("/api/v1/customers", json={"name": "Handoff recovery", "channel": "web"}).json()
    customer_id = customer["id"]
    client.post(f"/api/v1/sales/{customer_id}/handoff")
    product = client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "I need a gaming laptop"}).json()
    assert product["next_action"]["action"] == "recommend"
    assert product["handoff_required"] is True
    appointment = client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "I need an appointment Monday at 3pm"}).json()
    assert appointment["next_action"]["action"] == "appointment_created"
    records = client.get("/api/v1/admin/appointments").json()
    assert any(item["customer_id"] == customer_id and item["requested_time"] == "Monday at 3pm" for item in records)


def test_chat_can_add_last_recommended_product_to_cart_while_requesting_staff(client):
    customer = client.post("/api/v1/customers", json={"name": "Cart and staff", "channel": "web"}).json()
    customer_id = customer["id"]
    client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "gaming laptop within 6 to 7 lakh"})
    response = client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "add it to cart and ask salesperson to confirm availability"}).json()
    assert response["handoff_required"] is True
    assert "Added" in response["reply"] and "salesperson" in response["reply"]
    cart = client.get(f"/api/v1/carts/{customer_id}", headers={"X-Customer-Token": customer["customer_token"]}).json()
    assert len(cart["items"]) == 1


def test_customer_can_explicitly_start_a_second_order_for_the_same_product(client):
    customer = client.post("/api/v1/customers", json={"name": "Repeat buyer", "channel": "web"}).json()
    customer_id = customer["id"]
    client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "I need a gaming laptop"})
    first_prompt = client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "place my order"}).json()
    assert first_prompt["next_action"]["action"] == "select_product"
    first = client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "1"}).json()
    assert first["next_action"]["action"] == "order_review"
    headers = {"X-Customer-Token": customer["customer_token"]}
    client.post(f"/api/v1/conversations/{customer_id}/messages", headers=headers, json={"message": "confirm order"})
    second_prompt = client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "place another order"}).json()
    assert second_prompt["next_action"]["action"] == "select_product"
    second = client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "1"}).json()
    assert second["next_action"]["action"] == "order_review"
    assert second["next_action"]["review_id"] != first["next_action"]["review_id"]


def test_order_requires_review_owner_confirmation_and_is_idempotent(client):
    customer = client.post("/api/v1/customers", json={"name": "Review buyer", "channel": "web"}).json()
    other = client.post("/api/v1/customers", json={"name": "Other buyer", "channel": "web"}).json()
    headers = {"X-Customer-Token": customer["customer_token"]}
    review = client.post(f"/api/v1/orders/{customer['id']}", headers=headers, json={"product_id": "lenovo-rtx5060-32gb", "quantity": 2}).json()
    assert review["status"] == "pending"
    assert review["subtotal"] == 1_360_000
    assert client.get("/api/v1/admin/orders").json() == []
    denied = client.post(f"/api/v1/order-reviews/{review['review_id']}/confirm?customer_id={customer['id']}", headers={"X-Customer-Token": other["customer_token"]})
    assert denied.status_code == 401
    confirmed = client.post(f"/api/v1/order-reviews/{review['review_id']}/confirm?customer_id={customer['id']}", headers=headers).json()
    retry = client.post(f"/api/v1/order-reviews/{review['review_id']}/confirm?customer_id={customer['id']}", headers=headers).json()
    assert retry["already_confirmed"] is True
    assert retry["order_ids"] == confirmed["order_ids"]
    assert len(client.get("/api/v1/admin/orders").json()) == 1


def test_cart_edits_invalidate_order_review_and_private_cart_routes_require_token(client):
    customer = client.post("/api/v1/customers", json={"name": "Cart review", "channel": "web"}).json()
    cid = customer["id"]
    headers = {"X-Customer-Token": customer["customer_token"]}
    assert client.get(f"/api/v1/carts/{cid}").status_code == 401
    client.post(f"/api/v1/carts/{cid}/items", headers=headers, json={"product_id": "lenovo-rtx5060-32gb"})
    review = client.post(f"/api/v1/carts/{cid}/checkout", headers=headers, json={"payment_method": "cash_on_delivery"}).json()
    client.post(f"/api/v1/carts/{cid}/items", headers=headers, json={"product_id": "hp-840g5-i5-8350u"})
    confirm = client.post(f"/api/v1/order-reviews/{review['review_id']}/confirm?customer_id={cid}", headers=headers)
    assert confirm.status_code == 409
    assert client.get("/api/v1/admin/orders").json() == []


def test_customer_price_change_invalidates_review_before_order_creation(client):
    customer = client.post("/api/v1/customers", json={"name": "Price changed", "channel": "web"}).json()
    headers = {"X-Customer-Token": customer["customer_token"]}
    review = client.post(f"/api/v1/orders/{customer['id']}", headers=headers, json={"product_id": "lenovo-rtx5060-32gb"}).json()
    with sqlite3.connect("test.db") as db:
        db.execute("UPDATE products SET price=690000 WHERE id='lenovo-rtx5060-32gb'")
    result = client.post(f"/api/v1/order-reviews/{review['review_id']}/confirm?customer_id={customer['id']}", headers=headers)
    assert result.status_code == 409
    assert client.get("/api/v1/admin/orders").json() == []


def test_order_payment_and_fulfillment_transitions_require_confirmation(client):
    customer = client.post("/api/v1/customers", json={"name": "State guard", "channel": "web"}).json()
    headers = {"X-Customer-Token": customer["customer_token"]}
    review = client.post(f"/api/v1/orders/{customer['id']}", headers=headers, json={"product_id": "lenovo-rtx5060-32gb"}).json()
    created = client.post(f"/api/v1/order-reviews/{review['review_id']}/confirm?customer_id={customer['id']}", headers=headers).json()
    order_id = created["order_ids"][0]
    assert client.patch(f"/api/v1/admin/orders/{order_id}", json={"payment_status": "paid"}).status_code == 409
    assert client.patch(f"/api/v1/admin/orders/{order_id}", json={"fulfillment_status": "shipped"}).status_code == 409
    assert client.patch(f"/api/v1/admin/orders/{order_id}", json={"status": "confirmed"}).status_code == 200
    assert client.patch(f"/api/v1/admin/orders/{order_id}", json={"payment_status": "paid"}).status_code == 200
    assert client.patch(f"/api/v1/admin/orders/{order_id}", json={"payment_status": "not_paid"}).status_code == 409


def test_leads_are_qualified_from_stated_facts_and_salesperson_can_manage_them(client):
    customer = client.post("/api/v1/customers", json={"name": "Ready buyer", "channel": "web"}).json()
    client.post(f"/api/v1/conversations/{customer['id']}/messages", json={"message": "I want to buy a gaming laptop for 3D work within 7 lakh today"})
    leads = client.get("/api/v1/sales/leads").json()
    assert len(leads) == 1
    lead = leads[0]
    assert lead["customer_id"] == customer["id"]
    assert lead["status"] == "qualified"
    assert lead["priority"] == "high"
    assert "budget" in lead["qualification_reason"]
    updated = client.patch(f"/api/v1/sales/leads/{lead['id']}", json={"status": "nurture", "follow_up_at": "2026-11-20T15:00"}).json()
    assert updated["status"] == "nurture"
    assert updated["follow_up_at"] == "2026-11-20T15:00"
    assert updated["updated_by_user_id"] == "sales-001"
    client.post(f"/api/v1/conversations/{customer['id']}/messages", json={"message": "Actually office laptop"})
    assert client.get("/api/v1/sales/leads?status=nurture").json()[0]["status"] == "nurture"
    assert client.get("/api/v1/sales/leads?status=nurture&priority=high").json()[0]["id"] == lead["id"]


def test_better_option_respects_budget_and_can_expand_results_on_request(client):
    customer = client.post("/api/v1/customers", json={"name": "Alternative buyer", "channel": "web"}).json()
    customer_id = customer["id"]
    first = client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "gaming laptop within 6 to 7 lakh"}).json()
    assert len(first["next_action"]["products"]) == 1
    better = client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "any better option?"}).json()
    assert better["next_action"]["action"] == "related_recommendations"
    assert "only match" in better["reply"]
    assert "929,999" in better["reply"]
    expanded = client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "show higher options"}).json()
    assert expanded["next_action"]["action"] == "recommend"
    assert len(expanded["next_action"]["products"]) > 1


def test_admin_can_manage_products_and_explicit_relationship_recommendations(client):
    mouse = client.post("/api/v1/admin/products", json={
        "name": "Premium gaming mouse", "category": "Accessories", "subcategory": "Mouse", "price": 60000,
        "price_tier": "premium", "tags": ["gaming", "accessory"], "use_cases": ["gaming"],
        "attributes": {"complements_categories": ["Gaming Laptops"]}, "features": ["high polling rate"],
    })
    assert mouse.status_code == 201
    link = client.post("/api/v1/admin/product-relationships", json={
        "main_product_id": "lenovo-rtx5090-32gb", "related_product_id": mouse.json()["id"],
        "relationship_type": "accessory", "weight": 0.9, "note": "Premium gaming setup companion",
    })
    assert link.status_code == 201
    result = client.get("/api/v1/products/lenovo-rtx5090-32gb/related").json()
    assert result["recommendations"][0]["related_product"]["price"] == 60000
    assert result["recommendations"][0]["relationship_type"] == "accessory"
    removed = client.delete(f"/api/v1/admin/products/{mouse.json()['id']}")
    assert removed.status_code == 200
    assert mouse.json()["id"] not in {item["id"] for item in client.get("/api/v1/products").json()}


def test_admin_and_sales_workspaces_use_distinct_login_roles(client, monkeypatch):
    from app.main import settings
    monkeypatch.setattr(settings, "admin_login_password", "admin-secret")
    monkeypatch.setattr(settings, "salesperson_login_password", "sales-secret")
    assert client.get("/api/v1/admin/identity").status_code == 401
    assert client.get("/api/v1/admin/identity", auth=("admin", "wrong")).status_code == 401
    assert client.get("/api/v1/admin/identity", auth=("admin", "admin-secret")).json()["id"] == "admin-001"
    assert client.get("/api/v1/sales/identity", auth=("sales", "sales-secret")).json()["id"] == "sales-001"
    assert client.get("/api/v1/admin/identity", auth=("sales", "sales-secret")).status_code == 401


def test_existing_database_migrates_messages_for_salesperson_replies(client):
    customer = client.post("/api/v1/customers", json={"name": "Legacy customer", "channel": "web"}).json()
    with sqlite3.connect("test.db") as db:
        db.execute("DROP TABLE messages")
        db.execute("""CREATE TABLE messages (
            id TEXT PRIMARY KEY, customer_id TEXT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
            role TEXT NOT NULL CHECK(role IN ('customer','assistant','system')),
            content TEXT NOT NULL, created_at TEXT NOT NULL)""")
        db.execute("INSERT INTO messages VALUES('legacy-message',?,'customer','Existing message','2026-01-01T00:00:00Z')", (customer["id"],))

    from app.db import initialize_database
    initialize_database()

    with sqlite3.connect("test.db") as db:
        schema = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='messages'").fetchone()[0]
        db.execute("INSERT INTO messages VALUES('sales-message',?,'salesperson','Sales reply','2026-01-02T00:00:00Z')", (customer["id"],))
        records = db.execute("SELECT role,content FROM messages ORDER BY created_at").fetchall()
    assert "'salesperson'" in schema
    assert records == [("customer", "Existing message"), ("salesperson", "Sales reply")]


def test_conversation_history_persists_across_requests(client):
    customer = client.post("/api/v1/customers", json={"name": "Test", "channel": "web"}).json()
    customer_id = customer["id"]
    client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "I need an office laptop"})
    history = client.get(f"/api/v1/conversations/{customer_id}").json()
    assert [message["role"] for message in history["messages"]] == ["customer", "assistant"]
