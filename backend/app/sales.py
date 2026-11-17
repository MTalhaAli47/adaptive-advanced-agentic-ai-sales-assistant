from typing import Any


STAGES = ("new", "engaged", "qualifying", "qualified", "recommended", "appointment", "order_confirmed", "won", "lost")
QUALIFICATION_FIELDS = ("need", "budget", "timeline")


def extract_context(message: str, context: dict[str, Any]) -> dict[str, Any]:
    """Small transparent extractor; a provider-backed extractor can replace it later."""
    text = message.strip()
    lowered = text.casefold()
    if not context.get("need") and len(text) > 8:
        context["need"] = text[:240]
    if "urgent" in lowered or "asap" in lowered or "today" in lowered:
        context["urgency"] = "high"
    for marker in ("budget", "around", "under", "less than", "up to"):
        at = lowered.find(marker)
        if at >= 0:
            context["budget"] = text[at:at + 100]
            break
    if not context.get("timeline") and any(term in lowered for term in ("today", "tomorrow", "this week", "next week", "this month", "next month", "soon", "month", "week", "year", "asap")):
        context["timeline"] = text[:160]
    if any(word in lowered for word in ("price", "cost", "budget", "how much")):
        context["intent"] = "pricing"
    elif any(word in lowered for word in ("buy", "order", "purchase", "book")):
        context["intent"] = "purchase"
    elif "intent" not in context:
        context["intent"] = "product_inquiry"
    context["last_customer_message"] = text[:500]
    return context


def choose_next_action(context: dict[str, Any], stage: str, message: str) -> dict[str, Any]:
    lowered = message.casefold()
    if any(term in lowered for term in ("human", "agent", "salesperson", "call me")):
        return {"action": "human_handoff", "reason": "Customer requested a person.", "requires_approval": False}
    missing = [field for field in QUALIFICATION_FIELDS if not context.get(field)]
    if missing:
        field = missing[0]
        questions = {"need": "What are you looking to solve or purchase?", "budget": "Do you have a budget range in mind?", "timeline": "When are you hoping to make a decision?"}
        return {"action": "qualify", "reason": f"Qualification is missing {field}.", "question": questions[field], "requires_approval": False}
    if stage in ("qualified", "recommended"):
        return {"action": "recommend", "reason": "Key qualification details are available.", "requires_approval": False}
    return {"action": "answer", "reason": "Respond to the customer and continue qualification.", "requires_approval": False}


def stage_after_message(context: dict[str, Any], current: str) -> str:
    if current in ("new", "engaged"):
        return "qualifying"
    if all(context.get(field) for field in QUALIFICATION_FIELDS):
        return "qualified"
    return "qualifying"
