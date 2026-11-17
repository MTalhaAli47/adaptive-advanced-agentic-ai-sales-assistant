from app.recommendations import recommend_related_products


def product(product_id, name, category, price, **metadata):
    return {"id": product_id, "name": name, "category": category, "price": price, "currency": "PKR", "active": 1, **metadata}


def related(main, items, context=None, links=None):
    return recommend_related_products(main, items, context or {}, links or [])


def test_premium_gaming_laptop_recommends_premium_gaming_mouse():
    main = product("legion", "Gaming laptop", "Computers", 1_400_000, use_cases=["gaming"], price_tier="premium")
    mouse = product("mouse-pro", "Gaming mouse", "Accessories", 60_000, tags=["accessory", "gaming"], use_cases=["gaming"], price_tier="premium", attributes={"complements_categories": ["computers"]})
    result = related(main, [main, mouse], {"use_case": "gaming"})
    assert result[0]["related_product"]["id"] == "mouse-pro"
    assert result[0]["relevance_score"] > 0


def test_budget_office_laptop_recommends_budget_office_mouse():
    main = product("office", "Office laptop", "Computers", 70_000, use_cases=["office"], price_tier="budget")
    mouse = product("office-mouse", "Wireless mouse", "Accessories", 5_000, use_cases=["office"], price_tier="budget", attributes={"complements_categories": ["computers"]})
    result = related(main, [main, mouse], {"use_case": "office", "budget_max_pkr": 80_000})
    assert result and result[0]["related_product"]["id"] == "office-mouse"


def test_heavy_workload_context_recommends_use_case_cooling_pad():
    main = product("workstation", "Workstation", "Computers", 900_000, use_cases=["rendering", "heavy workloads"])
    pad = product("cooler", "Cooling pad", "Accessories", 12_000, use_cases=["heavy workloads"], attributes={"complements_categories": ["computers"]})
    assert related(main, [main, pad], {"use_case": "heavy workloads"})[0]["related_product"]["id"] == "cooler"


def test_camera_recommends_compatible_memory_card():
    camera = product("camera", "Travel camera", "Cameras", 300_000, use_cases=["travel"], attributes={"slot": "sdxc"})
    card = product("card", "SDXC memory card", "Storage", 18_000, use_cases=["travel"], compatibility={"requires_main_attributes": {"slot": "sdxc"}})
    result = related(camera, [camera, card], {"use_case": "travel"})
    assert result[0]["related_product"]["id"] == "card"
    assert result[0]["compatibility_status"] == "verified"


def test_explicitly_incompatible_item_is_excluded():
    main = product("a", "Camera", "Cameras", 100_000, tags=["sony-e-mount"])
    item = product("b", "Lens", "Accessories", 40_000, compatibility={"requires_main_tags": ["canon-rf"]})
    assert related(main, [main, item]) == []


def test_unavailable_product_is_excluded():
    main = product("a", "Laptop", "Computers", 100_000)
    item = product("b", "Mouse", "Accessories", 5_000, active=0, attributes={"complements_categories": ["computers"]})
    assert related(main, [main, item]) == []


def test_missing_budget_does_not_assume_budget_or_premium():
    main = product("a", "Laptop", "Computers", 500_000, use_cases=["office"])
    budget = product("budget", "Budget office mouse", "Accessories", 3_000, use_cases=["office"], attributes={"complements_categories": ["computers"]}, price_tier="budget")
    premium = product("premium", "Premium office mouse", "Accessories", 20_000, use_cases=["office"], attributes={"complements_categories": ["computers"]}, price_tier="premium")
    result = related(main, [main, budget, premium], {"use_case": "office"})
    assert {item["related_product"]["id"] for item in result} == {"budget", "premium"}
    assert all(item["matching_attributes"]["budget_fit"] is None for item in result)


def test_explicit_relationship_is_included_and_explained():
    main = product("a", "Printer", "Printers", 80_000)
    ink = product("b", "Ink refill", "Consumables", 6_000)
    link = {"related_product_id": "b", "relationship_type": "consumable", "weight": 0.9, "note": "Fits this printer model"}
    result = related(main, [main, ink], links=[link])
    assert result[0]["relationship_type"] == "consumable"
    assert "Fits this printer model" in result[0]["reason"]


def test_attribute_relationship_works_without_manual_link():
    main = product("a", "Laptop", "Computers", 500_000, use_cases=["travel"])
    bag = product("b", "Travel bag", "Accessories", 8_000, use_cases=["travel"], attributes={"complements_categories": ["computers"]})
    assert related(main, [main, bag], {"use_case": "travel"})[0]["related_product"]["id"] == "b"


def test_irrelevant_same_category_item_is_not_recommended():
    main = product("a", "Camera", "Cameras", 100_000)
    unrelated = product("b", "Another camera", "Cameras", 90_000)
    assert related(main, [main, unrelated]) == []


def test_empty_or_insufficient_matches_return_empty_list():
    main = product("a", "Laptop", "Computers", 100_000)
    assert related(main, []) == []
    assert related(main, [main, product("b", "Unrelated sofa", "Furniture", 20_000)]) == []


def test_output_uses_actual_database_record_price_and_structured_fields():
    main = product("a", "Phone", "Phones", 100_000)
    case = product("b", "Protective case", "Accessories", 2_500, attributes={"complements_categories": ["phones"]}, features=["shock resistant"])
    result = related(main, [main, case])[0]
    assert result["related_product"]["price"] == 2_500
    assert result["related_product"]["features"] == ["shock resistant"]
    assert result["availability"] == "available"
