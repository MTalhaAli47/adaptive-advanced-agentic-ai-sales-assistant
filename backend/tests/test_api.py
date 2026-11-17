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
    home = client.get("/")
    assert 'id="widget-new-chat"' in home.text
    assert "/assets/chat.js?v=" in home.text


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
    assert result["next_action"]["action"] == "recommend"
    assert result["context"]["product_category"] == "laptops"
    assert result["next_action"]["products"]


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
    order = client.post(f"/api/v1/orders/{customer_id}", json={"product_id": "lenovo-rtx5060-32gb", "quantity": 1}).json()
    assert order["status"] == "pending_approval"
    assert "no payment" in order["message"].lower()
    summary = client.get("/api/v1/dashboard/summary").json()
    assert summary["handoffs"] == 1
    assert summary["appointments_to_confirm"] == 1
    assert summary["orders_pending_approval"] == 1


def test_conversation_history_persists_across_requests(client):
    customer = client.post("/api/v1/customers", json={"name": "Test", "channel": "web"}).json()
    customer_id = customer["id"]
    client.post(f"/api/v1/conversations/{customer_id}/messages", json={"message": "I need an office laptop"})
    history = client.get(f"/api/v1/conversations/{customer_id}").json()
    assert [message["role"] for message in history["messages"]] == ["customer", "assistant"]
