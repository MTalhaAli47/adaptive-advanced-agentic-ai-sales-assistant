import re
from typing import Any


STAGES = ("new", "engaged", "qualifying", "qualified", "recommended", "appointment", "order_confirmed", "won", "lost")
AMOUNT_RE = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(million|millions|m|lakh|lakhs|lac|lacs|k|thousand|thousands)?\b", re.IGNORECASE)
HANDOFF_RE = re.compile(r"\b(?:human|person|agent|sales\s*person|salesperson|sales\s*rep|representative)\b|\bcall me\b", re.IGNORECASE)
CONFIRM_RE = re.compile(r"\b(?:confirm|verify)\b.*\b(?:it|this|that|stock|availability|order|payment)\b|\b(?:is it|are they)\s+(?:available|in stock)\b", re.IGNORECASE)
PAYMENT_RE = re.compile(r"\b(?:payment|pay|paying|checkout|bank transfer|transfer money)\b", re.IGNORECASE)
ORDER_RE = re.compile(r"\b(?:place|put in|make)\s+(?:(?:my|an?|the)\s+)?orders?\b|\b(?:order|buy|purchase)\s+(?:it|this|that|the\s+[\w-]+)\b|\bi(?:'ll| will) take (?:it|this|that)\b", re.IGNORECASE)
FRUSTRATION_RE = re.compile(r"\b(?:are you mad|mad|silly|stupid|useless|you lost|lost context|not listening|frustrated|annoyed)\b|\b(?:uff+|ugh+)\b", re.IGNORECASE)
THANKS_RE = re.compile(r"\b(?:thanks|thank you|thank u|thx)\b", re.IGNORECASE)
APPOINTMENT_RE = re.compile(r"\b(?:appointment|meeting|demo)\b|\bbook\s+(?:a\s+)?call\b|\b(?:book|schedule|arrange)\b.{0,30}\b(?:call|session)\b", re.IGNORECASE)


def _requests_human(text: str) -> bool:
    return bool(
        HANDOFF_RE.search(text)
        or re.search(r"\b(?:talk|speak|chat|connect|call|reach)\b.{0,30}\b(?:employee|staff|him|her|them|someone)\b", text, re.IGNORECASE)
    )


def _requests_salesperson(text: str) -> bool:
    return _requests_human(text) or bool(CONFIRM_RE.search(text)) or bool(re.search(r"\b(?:confirm|verify)\b", text, re.IGNORECASE))


def _requests_payment(text: str) -> bool:
    return bool(PAYMENT_RE.search(text))


def _requests_order(text: str) -> bool:
    return bool(ORDER_RE.search(text))


def _requests_appointment(text: str) -> bool:
    return bool(APPOINTMENT_RE.search(text))


def _amount(value: str, unit: str | None) -> int:
    number = float(value.replace(",", ""))
    multiplier = {
        "k": 1_000, "thousand": 1_000, "thousands": 1_000,
        "lakh": 100_000, "lakhs": 100_000, "lac": 100_000, "lacs": 100_000,
        "m": 1_000_000, "million": 1_000_000, "millions": 1_000_000,
    }.get((unit or "").casefold(), 1)
    return int(number * multiplier)


def _category(text: str) -> str | None:
    if re.search(r"\b(car|cars|vehicle|vehicles|automobile|automobiles|ev|electric car)\b", text):
        return "cars"
    if re.search(r"\b(laptop|laptops|notebook|notebooks|gaming laptop|computer)\b", text):
        return "laptops"
    if re.search(r"\bsolar\b", text):
        return "solar"
    if re.search(r"\bsoftware|crm|sales plan\b", text):
        return "software"
    if _requests_human(text):
        return None
    if _requests_appointment(text):
        return None
    request = re.search(
        r"\b(?:i need|i want|looking for|search for|do you have|do you sell|sell me|show me|recommend(?: me)?|looking to buy|buy)\s+(?:a|an|some|any|the)?\s*([a-z0-9][a-z0-9 -]{1,48})",
        text,
    )
    if request:
        interest = request.group(1).strip(" .?!,")
        interest = re.sub(r"\b(please|for me|today|now)\b", "", interest).strip()
        if re.search(r"\b(payment|pay|order|purchase|human|person|agent|representative|confirm|verify|availability)\b", interest):
            return None
        if interest and interest not in {"something", "anything", "one", "some"}:
            return interest
    return None


def extract_context(message: str, context: dict[str, Any]) -> dict[str, Any]:
    """Extract explicit facts and keep the active product category across turns."""
    text = message.strip()
    lowered = text.casefold()
    prior_category = context.get("product_category")
    detected_category = _category(lowered)
    starts_new_search = bool(re.search(r"\b(i need|i want|looking for|search for|start over|new search)\b", lowered))
    if detected_category and (detected_category != prior_category or starts_new_search):
        context["product_category"] = detected_category
        context.pop("use_case", None)
        context.pop("budget_min_pkr", None)
        context.pop("budget_max_pkr", None)
        context.pop("budget_text", None)
        context.pop("budget_flexible", None)
        context.pop("budget_needs_clarification", None)
        context.pop("budget_clarification_count", None)
        context.pop("budget_prompt_skipped", None)
        context.pop("budget_no_match", None)
        context.pop("awaiting_field", None)
        context.pop("gpu_model", None)
        context.pop("minimum_ram_gb", None)
        context.pop("minimum_vram_gb", None)
        context.pop("result_limit", None)
        context.pop("price_order", None)

    appointment_requested = _requests_appointment(lowered)
    if appointment_requested:
        time_match = re.search(r"\b(?:on|at|for)\s+(.+)$", text, re.IGNORECASE)
        if time_match and len(time_match.group(1).strip()) >= 3:
            context["appointment_requested_time"] = time_match.group(1).strip()[:120]
            context["pending_appointment"] = True
            context.pop("awaiting_field", None)
        else:
            context["pending_appointment"] = True
            context["awaiting_field"] = "appointment_time"
    elif context.get("awaiting_field") == "appointment_time" and len(text) >= 3:
        context["appointment_requested_time"] = text[:120]
        context["pending_appointment"] = True
        context.pop("awaiting_field", None)

    count_names = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
    count_match = re.search(r"\b(?:show|give|list)\s+(?:me\s+)?(?:only\s+)?(\d{1,2}|one|two|three|four|five)\s+(?:\w+\s+)?(?:products?|items?|laptops?|options?|results?)\b", lowered)
    if count_match:
        value = count_match.group(1)
        context["result_limit"] = int(value) if value.isdigit() else count_names[value]
    elif any(term in lowered for term in ("cheapest", "lowest price", "least expensive")):
        context["price_order"] = "ASC"
        context["result_limit"] = 1
    elif any(term in lowered for term in ("most expensive", "highest price")):
        context["price_order"] = "DESC"
        context["result_limit"] = 1
    elif any(term in lowered for term in ("show all", "all options", "show more")):
        context.pop("result_limit", None)
        context["price_order"] = "ASC"
    if context.get("awaiting_field") == "budget" and (context.get("result_limit") or context.get("price_order")):
        context.pop("awaiting_field", None)
        context["budget_flexible"] = True

    gpu = re.search(r"\brtx\s*(5070\s*ti|5060|5070|5080|5090)\b", lowered)
    if gpu:
        normalized = re.sub(r"\s+", " ", gpu.group(1)).upper().replace("5070 TI", "5070 Ti")
        context["gpu_model"] = f"RTX {normalized}"

    ram = re.search(r"\b(?:at least|min(?:imum)?)?\s*(\d{1,2})\s*gb\s*(?:ram|memory)\b", lowered)
    if ram:
        context["minimum_ram_gb"] = int(ram.group(1))
    vram = re.search(r"\b(?:at least|min(?:imum)?)?\s*(\d{1,2})\s*gb\s*vram\b", lowered)
    if vram:
        context["minimum_vram_gb"] = int(vram.group(1))

    if any(term in lowered for term in ("gaming", "game", "3d", "render", "ai work", "machine learning")):
        context["use_case"] = "gaming and 3D/AI workloads"
        if context.get("awaiting_field") == "use_case":
            context.pop("awaiting_field", None)
    elif any(term in lowered for term in ("office", "email", "word processing", "small tasks")):
        context["use_case"] = "office and everyday tasks"
        if context.get("awaiting_field") == "use_case":
            context.pop("awaiting_field", None)

    budget_text = re.sub(r"\blakg\b", "lakh", text, flags=re.IGNORECASE)
    budget_text = re.sub(r"\b\d+\s*gb\s*(?:ram|memory|vram)\b", " ", budget_text, flags=re.IGNORECASE)
    budget_text = re.sub(r"\brtx\s*(?:5060|5070\s*ti|5070|5080|5090)\b", " ", budget_text, flags=re.IGNORECASE)
    budget_text = re.sub(
        r"\b(?:show|give|list)\s+(?:me\s+)?(?:only\s+)?(?:\d{1,2}|one|two|three|four|five)\s+(?:\w+\s+)?(?:products?|items?|laptops?|options?|results?)\b",
        " ", budget_text, flags=re.IGNORECASE,
    )
    amount_matches = list(AMOUNT_RE.finditer(budget_text))
    inferred_unit = next((match.group(2) for match in amount_matches if match.group(2)), None) if len(amount_matches) == 2 else None
    amounts = [
        (_amount(match.group(1), match.group(2) or inferred_unit), match.group(0))
        for match in amount_matches
    ]
    budget_requested = context.get("awaiting_field") == "budget"
    budget_no_match = context.get("budget_no_match", False)
    has_budget_marker = any(term in lowered for term in ("budget", "under", "below", "less than", "up to", "within", "around", "between", "price", "cost", "pkr", "rupee", "rs."))
    has_explicit_unit = any(match.group(2) for match in AMOUNT_RE.finditer(budget_text))
    gpu_query = bool(re.search(r"\b(rtx|gpu|vram|graphics card)\b", lowered))
    if amounts and (has_budget_marker or has_explicit_unit or len(amounts) > 1 or (budget_requested and not gpu_query)):
        values = [item[0] for item in amounts]
        context["budget_min_pkr"] = min(values) if len(values) > 1 else None
        context["budget_max_pkr"] = max(values)
        context["budget_text"] = text[:160]
        context["budget_flexible"] = False
        context.pop("budget_needs_clarification", None)
        context.pop("budget_clarification_count", None)
        context.pop("budget_no_match", None)
        context.pop("awaiting_field", None)
    elif budget_requested and re.search(r"\b(no|any|flexible|no limit)\b", lowered):
        context["budget_flexible"] = True
        context["budget_text"] = "No fixed budget"
        context.pop("budget_needs_clarification", None)
        context.pop("budget_clarification_count", None)
        context.pop("awaiting_field", None)
    elif budget_requested and re.search(r"\b(skip|skip it|don't know|do not know)\b", lowered):
        context["budget_flexible"] = True
        context["budget_text"] = "Budget skipped"
        context.pop("budget_needs_clarification", None)
        context.pop("budget_clarification_count", None)
        context.pop("awaiting_field", None)
    elif budget_no_match and re.search(r"\b(show|see|yes|sure|options|them)\b", lowered):
        context.pop("budget_min_pkr", None)
        context.pop("budget_max_pkr", None)
        context["budget_flexible"] = True
        context["budget_prompt_skipped"] = True
        context.pop("budget_no_match", None)
        context.pop("awaiting_field", None)
    elif budget_requested and re.fullmatch(r"\s*(yes|yeah|yep|ok|okay)\s*[.!]?\s*", lowered):
        clarification_count = context.get("budget_clarification_count", 0) + 1
        if clarification_count >= 2:
            context["budget_flexible"] = True
            context["budget_prompt_skipped"] = True
            context.pop("awaiting_field", None)
            context.pop("budget_needs_clarification", None)
            context.pop("budget_clarification_count", None)
        else:
            context["budget_needs_clarification"] = True
            context["budget_clarification_count"] = clarification_count

    if "urgent" in lowered or "asap" in lowered or "today" in lowered:
        context["urgency"] = "high"
    if any(term in lowered for term in ("tomorrow", "this week", "next week", "this month", "next month", "soon", "within")):
        context["timeline"] = text[:160]

    if _requests_salesperson(lowered):
        context["intent"] = "human_handoff"
    elif _requests_payment(lowered):
        context["intent"] = "payment_help"
    elif _requests_appointment(lowered):
        context["intent"] = "appointment_request"
    elif _requests_order(lowered):
        context["intent"] = "purchase"
    elif any(word in lowered for word in ("buy", "order", "purchase")):
        context["intent"] = "purchase"
    elif any(word in lowered for word in ("price", "cost", "budget", "how much")):
        context["intent"] = "pricing"
    elif detected_category:
        context["intent"] = "product_inquiry"

    if len(text) > 2 and lowered not in {"yes", "no", "ok", "okay", "hi", "hello"}:
        context["need"] = text[:240]
    context["last_customer_message"] = text[:500]
    return context


def choose_next_action(context: dict[str, Any], stage: str, message: str) -> dict[str, Any]:
    lowered = message.casefold()
    if _requests_human(lowered):
        return {"action": "human_handoff", "reason": "Customer requested a person.", "requires_approval": False}
    if CONFIRM_RE.search(lowered) or re.search(r"\b(?:confirm|verify)\b", lowered):
        return {"action": "human_handoff", "reason": "Customer asked a salesperson to confirm availability or order details.", "requires_approval": False}
    if _requests_payment(lowered):
        return {"action": "payment_help", "reason": "Customer needs help arranging payment; no payment is taken in chat."}
    if _requests_order(lowered):
        return {"action": "place_order", "reason": "Customer explicitly asked to place an order."}
    if _requests_appointment(lowered) or context.get("pending_appointment"):
        if context.get("appointment_requested_time"):
            return {"action": "create_appointment", "reason": "Customer asked to schedule a meeting and provided a requested time."}
        context["awaiting_field"] = "appointment_time"
        return {
            "action": "clarify_appointment",
            "reason": "The appointment request needs a date or time before it can be recorded.",
            "question": "I can pass that along. What day and time should I request?",
        }
    if FRUSTRATION_RE.search(lowered):
        category = context.get("product_category")
        focus = f"I’m helping you with {category}. " if category else ""
        return {
            "action": "apology",
            "reason": "Customer is frustrated; acknowledge it and offer a clear next step.",
            "reply": f"I’m sorry—I lost track of what you asked. {focus}Would you like me to show options, ask a salesperson to confirm availability, or help place an order?",
        }
    if THANKS_RE.search(lowered):
        return {"action": "smalltalk", "reason": "Acknowledge the customer and keep their order context."}

    category = context.get("product_category")
    if context.get("awaiting_field") == "product_selection":
        if re.fullmatch(r"\s*(no|cancel|not now|never mind)[.\s]*", lowered):
            return {"action": "cancel_order_request", "reason": "Customer cancelled product selection."}
        return {"action": "place_order", "reason": "Customer is selecting one of the products just offered."}
    if not category:
        if re.search(r"\b(hi|hello|hey)\b", lowered):
            return {"action": "discover", "reason": "Start by identifying the product category."}
        return {
            "action": "discover",
            "reason": "No product category has been identified yet.",
            "question": "What product or service are you looking for? I’ll check it against the current catalogue.",
        }

    if context.get("awaiting_field") == "use_case" and not context.get("use_case"):
        return {
            "action": "clarify_use",
            "reason": "The customer needs to choose between the catalogue's office and gaming laptop types.",
            "product_category": category,
            "question": "Are you looking for an office laptop for everyday work, or a gaming laptop for gaming, 3D, and AI? You can also tell me a different use.",
        }

    if re.fullmatch(r"\s*(hi|hello|hey|good morning|good afternoon|good evening)[!.\s]*", lowered):
        return {"action": "greet", "reason": "Greet the customer while retaining the active product category.", "product_category": category}

    if context.get("awaiting_field") == "budget" and context.get("budget_needs_clarification"):
        attempt = context.get("budget_clarification_count", 1)
        prompt = (
            "I still need a number to filter by budget. Type a range like 300,000–500,000, say ‘no fixed budget’, or type ‘skip’."
            if attempt > 1 else
            "Please give an approximate amount in PKR, such as 100,000–300,000, or say ‘no fixed budget’."
        )
        return {
            "action": "clarify_budget",
            "reason": "The customer agreed to share a budget but did not give an amount.",
            "question": prompt,
        }

    if context.get("awaiting_field") == "budget":
        return {
            "action": "clarify_budget",
            "reason": "The last message did not contain a usable budget amount.",
            "question": "I didn’t catch a budget amount. You can give a range like 600,000–700,000 PKR, say ‘no fixed budget’, or skip it.",
        }

    if (category == "laptops" and not context.get("use_case") and not context.get("budget_max_pkr")
            and not context.get("budget_flexible") and not context.get("result_limit") and not context.get("price_order")):
        context["awaiting_field"] = "use_case"
        return {
            "action": "clarify_use",
            "reason": "The catalogue has distinct office and gaming laptops; ask which fits the customer.",
            "product_category": category,
            "question": "Are you looking for an office laptop for everyday work, or a gaming laptop for gaming, 3D, and AI? You can also tell me a different use.",
        }

    factual_question = (
        "?" in message
        and not any(term in lowered for term in ("do you have", "can i buy", "show me", "recommend", "price", "cost", "budget"))
    )
    return {
        "action": "answer" if factual_question else "recommend",
        "reason": f"Find catalogue items for the active category: {category}.",
        "product_category": category,
        "use_case": context.get("use_case"),
        "budget_min_pkr": context.get("budget_min_pkr"),
        "budget_max_pkr": context.get("budget_max_pkr"),
        "question": (
            "I’ll show options without a price filter. Share a maximum budget anytime to narrow the list."
            if context.get("budget_prompt_skipped") else
            None if context.get("budget_max_pkr") or context.get("budget_flexible") or context.get("result_limit") or context.get("price_order") else
            "Do you have a budget range in mind?"
        ),
    }


def stage_after_message(context: dict[str, Any], current: str) -> str:
    if current in ("new", "engaged"):
        return "engaged"
    if context.get("product_category"):
        return "recommended"
    return "qualifying"
