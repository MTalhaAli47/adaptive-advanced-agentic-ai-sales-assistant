from typing import Any, Literal

from pydantic import BaseModel, Field


class CustomerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    contact: str | None = Field(default=None, max_length=200)
    channel: Literal["web", "whatsapp", "salesperson"] = "web"


class CustomerOut(BaseModel):
    id: str
    user_id: str | None = None
    name: str
    contact: str | None
    channel: str
    context: dict[str, Any]
    sales_stage: str
    handoff_requested: bool
    created_at: str
    customer_token: str | None = None


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


class CartItemCreate(BaseModel):
    product_id: str = Field(min_length=1, max_length=120)
    quantity: int = Field(default=1, ge=1, le=100)


class CartItemUpdate(BaseModel):
    quantity: int = Field(ge=1, le=100)


class CheckoutCreate(BaseModel):
    payment_method: Literal["cash_on_delivery", "bank_transfer"]


class LeadUpdate(BaseModel):
    status: Literal["new", "qualifying", "qualified", "nurture", "converted", "lost"] | None = None
    priority: Literal["low", "medium", "high"] | None = None
    assigned_to: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=4000)
    follow_up_at: str | None = Field(default=None, max_length=120)


class ProductCreate(BaseModel):
    id: str | None = Field(default=None, min_length=2, max_length=120)
    name: str = Field(min_length=2, max_length=200)
    description: str = Field(default="", max_length=4000)
    category: str = Field(min_length=2, max_length=100)
    subcategory: str | None = Field(default=None, max_length=100)
    price: float | None = Field(default=None, ge=0)
    currency: str = Field(default="PKR", min_length=3, max_length=3)
    tags: list[str] = Field(default_factory=list, max_length=40)
    attributes: dict[str, Any] = Field(default_factory=dict)
    price_tier: Literal["budget", "mid_range", "premium"] | None = None
    use_cases: list[str] = Field(default_factory=list, max_length=30)
    performance_level: str | None = Field(default=None, max_length=60)
    compatibility: dict[str, Any] = Field(default_factory=dict)
    features: list[str] = Field(default_factory=list, max_length=50)
    suitable_profiles: list[str] = Field(default_factory=list, max_length=30)
    active: bool = True


class ProductRelationshipCreate(BaseModel):
    main_product_id: str
    related_product_id: str
    relationship_type: Literal["accessory", "complementary", "frequently_bought_together", "upgrade", "alternative", "essential_companion", "consumable", "use_case"]
    weight: float = Field(default=1.0, ge=0, le=1)
    note: str | None = Field(default=None, max_length=500)


class AdminOrderUpdate(BaseModel):
    status: Literal["pending_confirmation", "confirmed", "cancelled"] | None = None
    payment_status: Literal["not_paid", "paid", "refunded"] | None = None
    fulfillment_status: Literal["not_started", "processing", "shipped", "delivered", "returned"] | None = None
    payment_method: str | None = Field(default=None, max_length=80)
    payment_reference: str | None = Field(default=None, max_length=160)
    admin_note: str | None = Field(default=None, max_length=1000)
