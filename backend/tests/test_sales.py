import pytest

from app.sales import choose_next_action, extract_context


@pytest.mark.parametrize(
    ("message", "category"),
    [
        ("I need a car", "cars"),
        ("Do you have laptops?", "laptops"),
        ("I'm interested in solar", "solar"),
        ("I need a blender", "blender"),
    ],
)
def test_extracts_known_and_open_ended_categories(message, category):
    assert extract_context(message, {}).get("product_category") == category


@pytest.mark.parametrize(
    ("message", "minimum", "maximum"),
    [
        ("50k-100k", 50_000, 100_000),
        ("1 lakh to 5 lakh", 100_000, 500_000),
        ("within 3 million", None, 3_000_000),
        ("100,000 - 500000 PKR", 100_000, 500_000),
        ("6 to 7 lakh", 600_000, 700_000),
        ("6-7 lakg", 600_000, 700_000),
        ("600-700k", 600_000, 700_000),
    ],
)
def test_budget_formats(message, minimum, maximum):
    context = {"product_category": "laptops", "awaiting_field": "budget"}
    extract_context(message, context)
    assert context.get("budget_min_pkr") == minimum
    assert context.get("budget_max_pkr") == maximum
    assert "awaiting_field" not in context


def test_gpu_and_memory_specs_are_not_parsed_as_budget():
    context = {"product_category": "laptops"}
    extract_context("RTX 5070 Ti, 32GB RAM, 12GB VRAM", context)
    assert context["gpu_model"] == "RTX 5070 Ti"
    assert context["minimum_ram_gb"] == 32
    assert context["minimum_vram_gb"] == 12
    assert "budget_max_pkr" not in context


def test_new_search_clears_stale_budget_and_product_filters():
    context = {
        "product_category": "laptops", "budget_min_pkr": 100_000,
        "budget_max_pkr": 500_000, "gpu_model": "RTX 5070", "use_case": "gaming",
        "budget_no_match": True,
    }
    extract_context("I need a laptop", context)
    for key in ("budget_min_pkr", "budget_max_pkr", "gpu_model", "budget_no_match", "use_case"):
        assert key not in context
    assert context["product_category"] == "laptops"


def test_no_match_yes_or_show_options_clears_failed_budget():
    for message in ("yes", "show", "show me", "show them", "show options", "I would like to see"):
        context = {
            "product_category": "laptops", "budget_min_pkr": 100_000,
            "budget_max_pkr": 500_000, "budget_no_match": True,
        }
        extract_context(message, context)
        assert "budget_max_pkr" not in context, message
        assert context["budget_flexible"] is True
        assert "budget_no_match" not in context


def test_repeated_budget_yes_stops_repeating_the_same_question():
    context = {"product_category": "laptops", "awaiting_field": "budget"}
    extract_context("yes", context)
    assert context["budget_needs_clarification"] is True
    extract_context("yes", context)
    assert context["budget_flexible"] is True
    assert "awaiting_field" not in context
    assert "budget_needs_clarification" not in context


@pytest.mark.parametrize(
    ("message", "limit", "order"),
    [
        ("show only 1 laptop", 1, None),
        ("show only two products", 2, None),
        ("show cheapest laptop", 1, "ASC"),
        ("show most expensive laptop", 1, "DESC"),
    ],
)
def test_result_count_and_price_order(message, limit, order):
    context = {"product_category": "laptops"}
    extract_context(message, context)
    assert context["result_limit"] == limit
    if order:
        assert context["price_order"] == order


def test_explicit_handoff_is_a_current_turn_action():
    context = {"product_category": "laptops", "intent": "human_handoff"}
    assert choose_next_action(context, "engaged", "show options")["action"] == "recommend"
    assert choose_next_action(context, "engaged", "salesperson please")["action"] == "human_handoff"


def test_unknown_requested_category_is_preserved_for_catalogue_search():
    context = extract_context("Do you have a camera?", {})
    assert context["product_category"] == "camera"
    action = choose_next_action(context, "new", "Do you have a camera?")
    assert action["product_category"] == "camera"


def test_result_count_and_gpu_digits_do_not_become_budget_values():
    context = {"product_category": "laptops"}
    extract_context("show only 1 laptop, RTX 5080, 16GB VRAM", context)
    assert context["result_limit"] == 1
    assert context["gpu_model"] == "RTX 5080"
    assert context["minimum_vram_gb"] == 16
    assert "budget_max_pkr" not in context


def test_category_switch_resets_old_budget_but_keeps_followup_questions_scoped():
    context = {"product_category": "cars", "budget_max_pkr": 3_000_000}
    extract_context("Do you have laptops?", context)
    action = choose_next_action(context, "engaged", "Do you have laptops?")
    assert context["product_category"] == "laptops"
    assert "budget_max_pkr" not in context
    assert action["product_category"] == "laptops"


@pytest.mark.parametrize("message", [
    "I want to talk to a human", "Can I speak with an agent?", "Connect me to a sales rep",
])
def test_human_request_is_never_treated_as_a_product_category(message):
    context = extract_context(message, {})
    assert "product_category" not in context
    assert choose_next_action(context, "engaged", message)["action"] == "human_handoff"


def test_cheapest_or_most_expensive_answer_does_not_repeat_budget_prompt():
    for message in ("cheapest one", "i mean most expensive"):
        context = {"product_category": "laptops", "result_limit": 1, "price_order": "ASC"}
        action = choose_next_action(context, "engaged", message)
        assert action["question"] is None


def test_order_payment_confirmation_and_human_phrases_are_distinct_intents():
    context = {"product_category": "laptops", "last_recommended_product_ids": ["lenovo-rtx5060-32gb"]}
    assert choose_next_action(context, "engaged", "place my order")["action"] == "place_order"
    assert choose_next_action(context, "engaged", "I want to make payment")["action"] == "payment_help"
    assert choose_next_action(context, "engaged", "please confirm it")["action"] == "human_handoff"
    assert choose_next_action(context, "engaged", "let me talk to him")["action"] == "human_handoff"


def test_payment_language_is_not_mistaken_for_a_product_category():
    context = extract_context("I want to make payment", {})
    assert "product_category" not in context
    assert context["intent"] == "payment_help"


def test_budget_and_laptop_use_clarifications_do_not_fall_back_to_recommendations():
    budget_context = {"product_category": "laptops", "awaiting_field": "budget"}
    assert choose_next_action(budget_context, "engaged", "a")["action"] == "clarify_budget"
    use_context = {"product_category": "laptops", "awaiting_field": "use_case"}
    assert choose_next_action(use_context, "engaged", "a")["action"] == "clarify_use"


def test_thanks_is_smalltalk_instead_of_repeating_product_results():
    assert choose_next_action({"product_category": "laptops"}, "engaged", "thank u")["action"] == "smalltalk"

