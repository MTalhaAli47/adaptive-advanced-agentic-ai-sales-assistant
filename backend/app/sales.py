import re
from typing import Any


STAGES = ("new", "engaged", "qualifying", "qualified", "recommended", "appointment", "order_confirmed", "won", "lost")
AMOUNT_RE = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(million|millions|m|lakh|lakhs|lac|lacs|k|thousand|thousands)?\b", re.IGNORECASE)


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
    request = re.search(
        r"\b(?:i need|i want|looking for|search for|do you have|do you sell|sell me|show me|recommend(?: me)?|looking to buy|buy)\s+(?:a|an|some|any|the)?\s*([a-z0-9][a-z0-9 -]{1,48})",
        text,
    )
    if request:
        interest = request.group(1).strip(" .?!,")
        interest = re.sub(r"\b(please|for me|today|now)\b", "", interest).strip()
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
        context.pop("gpu_model", None)
        context.pop("minimum_ram_gb", None)
        context.pop("minimum_vram_gb", None)
        context.pop("result_limit", None)
        context.pop("price_order", None)

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
    elif any(term in lowered for term in ("office", "email", "word processing", "small tasks")):
        context["use_case"] = "office and everyday tasks"

    budget_text = re.sub(r"\b\d+\s*gb\s*(?:ram|memory|vram)\b", " ", text, flags=re.IGNORECASE)
    budget_text = re.sub(r"\brtx\s*(?:5060|5070\s*ti|5070|5080|5090)\b", " ", budget_text, flags=re.IGNORECASE)
    budget_text = re.sub(
        r"\b(?:show|give|list)\s+(?:me\s+)?(?:only\s+)?(?:\d{1,2}|one|two|three|four|five)\s+(?:\w+\s+)?(?:products?|items?|laptops?|options?|results?)\b",
        " ", budget_text, flags=re.IGNORECASE,
    )
    amounts = [(_amount(match.group(1), match.group(2)), match.group(0)) for match in AMOUNT_RE.finditer(budget_text)]
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

    if any(word in lowered for word in ("human", "agent", "salesperson", "call me")):
        context["intent"] = "human_handoff"
    elif any(word in lowered for word in ("buy", "order", "purchase", "book")):
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
    if any(term in lowered for term in ("human", "agent", "salesperson", "call me")):
        return {"action": "human_handoff", "reason": "Customer requested a person.", "requires_approval": False}

    category = context.get("product_category")
    if not category:
        if re.search(r"\b(hi|hello|hey)\b", lowered):
            return {"action": "discover", "reason": "Start by identifying the product category."}
        return {
            "action": "discover",
            "reason": "No product category has been identified yet.",
            "question": "What product or service are you looking for? I’ll check it against the current catalogue.",
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
            None if context.get("budget_max_pkr") or context.get("budget_flexible") else
            "Do you have a budget range in mind?"
        ),
    }


def stage_after_message(context: dict[str, Any], current: str) -> str:
    if current in ("new", "engaged"):
        return "engaged"
    if context.get("product_category"):
        return "recommended"
    return "qualifying"
