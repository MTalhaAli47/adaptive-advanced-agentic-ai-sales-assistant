from typing import Any, Literal

from pydantic import BaseModel, Field


class CustomerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    contact: str | None = Field(default=None, max_length=200)
    channel: Literal["web", "whatsapp", "salesperson"] = "web"


class CustomerOut(BaseModel):
    id: str
    name: str
    contact: str | None
    channel: str
    context: dict[str, Any]
    sales_stage: str
    handoff_requested: bool
    created_at: str


class MessageIn(BaseModel):
    message: str = Field(min_length=1, max_length=8000)


class MessageOut(BaseModel):
    id: str
    customer_id: str
    role: str
    content: str
    created_at: str


class ConversationReply(BaseModel):
    customer_id: str
    reply: str
    sales_stage: str
    context: dict[str, Any]
    next_action: dict[str, Any]
    handoff_required: bool = False


class AppointmentCreate(BaseModel):
    requested_time: str = Field(min_length=3, max_length=120)
    notes: str | None = Field(default=None, max_length=1000)


class AdminAppointmentUpdate(BaseModel):
    status: Literal["requested", "confirmed", "cancelled", "completed"]
    notes: str | None = Field(default=None, max_length=1000)


class OrderCreate(BaseModel):
    product_id: str
    quantity: int = Field(default=1, ge=1, le=100)


class AdminOrderUpdate(BaseModel):
    status: Literal["pending_confirmation", "confirmed", "cancelled"] | None = None
    payment_status: Literal["not_paid", "paid", "refunded"] | None = None
    fulfillment_status: Literal["not_started", "processing", "shipped", "delivered", "returned"] | None = None
    payment_method: str | None = Field(default=None, max_length=80)
    payment_reference: str | None = Field(default=None, max_length=160)
    admin_note: str | None = Field(default=None, max_length=1000)
