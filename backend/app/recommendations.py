"""Generic, explainable product relationship and recommendation scoring."""

import json
import os
import re
from typing import Any


DEFAULT_WEIGHTS = {
    "explicit_relationship": 0.40,
    "use_case_match": 0.18,
    "compatibility_evidence": 0.20,
    "customer_preference": 0.08,
    "customer_profile_match": 0.05,
    "performance_match": 0.04,
    "budget_fit": 0.05,
}


def configured_weights() -> dict[str, float]:
    try:
        configured = json.loads(os.getenv("AISA_RECOMMENDATION_WEIGHTS", "{}"))
        if not isinstance(configured, dict):
            return {}
        return {key: float(value) for key, value in configured.items() if key in DEFAULT_WEIGHTS and 0 <= float(value) <= 1}
    except (TypeError, ValueError):
        return {}


def _json(value: Any, default: Any):
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return default


def _words(value: Any) -> set[str]:
    if isinstance(value, (list, tuple, set)):
        value = " ".join(str(part) for part in value)
    return {word for word in re.findall(r"[a-z0-9]+", str(value or "").casefold()) if len(word) > 2}


def _array(product: dict, field: str) -> list[str]:
    camel = f"{field}_json"
    raw = product.get(field, product.get(camel, []))
    parsed = _json(raw, [])
    return [str(value) for value in parsed] if isinstance(parsed, list) else []


def _object(product: dict, field: str) -> dict:
    raw = product.get(field, product.get(f"{field}_json", {}))
    parsed = _json(raw, {})
    return parsed if isinstance(parsed, dict) else {}


def _normalized(product: dict) -> dict:
    result = dict(product)
    for field in ("tags", "use_cases", "features", "suitable_profiles"):
        result[field] = _array(product, field)
    for field in ("attributes", "compatibility"):
        result[field] = _object(product, field)
    return result


def recommend_related_products(
    main_product: dict,
    products: list[dict],
    context: dict | None = None,
    relationships: list[dict] | None = None,
    weights: dict[str, float] | None = None,
    limit: int = 4,
) -> list[dict]:
    """Score active related products; no price preference is assumed when absent."""
    context = context or {}
    weights = {**DEFAULT_WEIGHTS, **configured_weights(), **(weights or {})}
    main = _normalized(main_product)
    links = {(row.get("related_product_id"), row.get("relationship_type")): row for row in (relationships or []) if row.get("active", 1)}
    main_use = _words(main["use_cases"])
    intent_use = _words(context.get("use_case") or context.get("intended_use"))
    context_preferences = _words([context.get("preferences"), context.get("need"), context.get("gpu_model"),
                                  f"{context.get('minimum_ram_gb', '')} gb ram",
                                  f"{context.get('minimum_vram_gb', '')} gb vram"])
    related_budget_max = context.get("related_budget_max_pkr")
    related_budget_min = context.get("related_budget_min_pkr")
    explicit_budget = related_budget_max is not None or related_budget_min is not None
    budget_max = related_budget_max
    output = []

    for raw_candidate in products:
        if raw_candidate.get("id") == main.get("id") or not raw_candidate.get("active", 1):
            continue
        candidate = _normalized(raw_candidate)
        link = next((value for (product_id, _), value in links.items() if product_id == candidate.get("id")), None)
        cand_attributes = candidate["attributes"]
        main_attributes = {**main["attributes"], **{key: main.get(key) for key in ("brand", "ram_gb", "gpu_model", "vram_gb", "storage_gb", "price_tier", "performance_level", "condition", "category", "subcategory") if main.get(key) is not None}}
        compatible_ids = set(map(str, cand_attributes.get("compatible_with_product_ids", candidate["compatibility"].get("compatible_with_product_ids", []))))
        complements = _words([cand_attributes.get("complements_categories", []), candidate["compatibility"].get("complements_categories", [])])
        main_terms = _words([main.get("category"), main.get("subcategory"), main.get("name"), *main["tags"]])
        same_category = (main.get("category") or "").casefold() == (candidate.get("category") or "").casefold()
        compatibility_evidence = candidate.get("id") in compatible_ids or bool(complements & main_terms)
        required_tags = _words(candidate["compatibility"].get("requires_main_tags", []))
        if required_tags and not required_tags.issubset(_words(main["tags"])):
            continue
        required_attributes = candidate["compatibility"].get("requires_main_attributes", {})
        if any(main_attributes.get(key) != value for key, value in required_attributes.items()):
            continue
        compatibility_evidence = compatibility_evidence or bool(required_tags or required_attributes)

        candidate_tags = _words([*candidate["tags"], *candidate["features"], candidate.get("name"), candidate.get("subcategory"), candidate.get("performance_level"), candidate["attributes"]])
        use_overlap = bool((main_use | intent_use) & _words(candidate["use_cases"]))
        preference_overlap = bool(context_preferences & candidate_tags)
        profile_match = bool(_words(context.get("customer_profile") or context.get("profile")) & _words(candidate["suitable_profiles"]))
        requested_performance = _words(context.get("performance_level") or context.get("minimum_performance_level"))
        performance_match = bool(requested_performance & _words(candidate.get("performance_level")))
        attribute_relationship = cand_attributes.get("relationship_type")
        allowed_relationships = {"accessory", "complementary", "frequently_bought_together", "upgrade", "alternative", "essential_companion", "consumable", "use_case"}
        if attribute_relationship not in allowed_relationships:
            attribute_relationship = None
        rel_type = link.get("relationship_type") if link else attribute_relationship or ("alternative" if same_category and use_overlap else None)
        if same_category and use_overlap and context.get("budget_max_pkr") is not None and candidate.get("price") is not None and candidate["price"] > context["budget_max_pkr"]:
            continue
        explicit_score = min(1.0, max(0.0, float(link.get("weight", 1.0)))) if link else 0.0
        # Attribute-based matching needs positive cross-category/use-case or compatibility evidence.
        if not link and not (compatibility_evidence or use_overlap or preference_overlap):
            continue
        budget_fit = 0.0
        if explicit_budget and candidate.get("price") is not None:
            within_max = budget_max is None or candidate["price"] <= budget_max
            minimum = related_budget_min
            above_min = minimum is None or candidate["price"] >= minimum
            if not within_max or not above_min:
                continue
            budget_fit = 1.0 if within_max and above_min else (0.0 if budget_max is not None and candidate["price"] > budget_max * 1.25 else 0.25)
        elif explicit_budget:
            continue
        elif context.get("budget_max_pkr") is not None and main.get("price_tier"):
            # A laptop budget is a soft cross-sell preference, not an accessory price band.
            budget_fit = 1.0 if candidate.get("price_tier") == main.get("price_tier") else 0.5

        score_parts = {
            "explicit_relationship": explicit_score,
            "use_case_match": 1.0 if use_overlap else 0.0,
            "compatibility_evidence": 1.0 if compatibility_evidence else 0.0,
            "customer_preference": 1.0 if preference_overlap else 0.0,
            "customer_profile_match": 1.0 if profile_match else 0.0,
            "performance_match": 1.0 if performance_match else 0.0,
            "budget_fit": budget_fit if explicit_budget or context.get("budget_max_pkr") is not None else 0.0,
        }
        divisor = sum(weights[key] for key, score in score_parts.items() if key != "budget_fit" or explicit_budget or context.get("budget_max_pkr") is not None)
        if divisor <= 0:
            continue
        score = sum(weights[key] * value for key, value in score_parts.items()) / divisor
        if score < 0.2:
            continue
        evidence = []
        if link:
            evidence.append(link.get("note") or f"Explicitly linked as {str(rel_type).replace('_', ' ')}")
        if compatibility_evidence:
            evidence.append("Product metadata marks it as compatible or complementary")
        if use_overlap:
            matching_use = sorted((main_use | intent_use) & _words(candidate["use_cases"]))
            evidence.append(f"Matches use case: {', '.join(matching_use)}")
        if preference_overlap:
            evidence.append("Matches a stated customer preference")
        if profile_match:
            evidence.append("Suitable for the stated customer profile")
        if performance_match:
            evidence.append("Matches the requested performance level")
        if not evidence:
            evidence.append("Related through the configured product relationship")
        if explicit_budget and budget_fit == 1:
            evidence.append("Fits the stated related-product budget")
        elif not explicit_budget and context.get("budget_max_pkr") is not None and main.get("price_tier") and budget_fit == 1:
            evidence.append("Matches the price tier of the selected main product")
        elif explicit_budget and budget_fit == 0:
            evidence.append("Outside the stated budget; consider only if the customer is flexible")
        output.append({
            "main_product": {"id": main.get("id"), "name": main.get("name")},
            "related_product": candidate,
            "relationship_type": rel_type or "use_case",
            "relevance_score": round(score, 4),
            "reason": "; ".join(evidence),
            "compatibility_status": "verified" if compatibility_evidence or link else "context_match_only",
            "availability": "available" if candidate.get("active", 1) else "unavailable",
            "matching_attributes": {"use_case_match": use_overlap, "budget_fit": budget_fit if explicit_budget or context.get("budget_max_pkr") is not None else None, "preference_match": preference_overlap, "customer_profile_match": profile_match, "performance_match": performance_match},
        })
    output.sort(key=lambda item: (-item["relevance_score"], item["related_product"].get("price") or 0))
    return output[:max(0, limit)]
