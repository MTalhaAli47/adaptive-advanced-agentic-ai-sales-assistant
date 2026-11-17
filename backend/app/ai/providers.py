from typing import Protocol


class SalesAssistantProvider(Protocol):
    def reply(self, customer_message: str, context: dict, next_action: dict) -> str: ...


class LocalFallbackProvider:
    """Safe deterministic provider for development when no model is configured."""

    def reply(self, customer_message: str, context: dict, next_action: dict) -> str:
        action = next_action.get("action")
        if action == "human_handoff":
            return "I’ll pass this conversation to a salesperson so they can help you directly."
        if action == "qualify":
            return next_action.get("question", "Could you tell me a little more about what you need?")
        if action == "recommend":
            products = next_action.get("products", [])
            if products:
                names = ", ".join(product["name"] for product in products[:3])
                return f"Based on what you shared, these may be relevant: {names}. A salesperson can confirm availability and final terms. Which option would you like to explore?"
            return "Thanks, I have the key details. I’ll use them to find a suitable option; a salesperson can confirm availability and final terms."
        return "Thanks for reaching out. I can help with product information, recommendations, and arranging a conversation with our sales team."


def get_provider() -> SalesAssistantProvider:
    # Provider selection is intentionally isolated. Cloud/local adapters can be added
    # when the provider and credentials are selected; never silently call a service.
    return LocalFallbackProvider()
