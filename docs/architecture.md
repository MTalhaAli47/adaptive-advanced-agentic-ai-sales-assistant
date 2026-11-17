# Initial implementation architecture

## Request flow

Web widget, full-page chat, and WhatsApp adapters are planned to use one channel-neutral conversation API. The API loads the shared customer record and conversation history, updates context and sales stage, selects an explainable next action, and returns a response. Human handoff is represented on the same customer record.

```text
Web widget ─┐
Web chat ───┼─> Channel adapters (planned) ─> FastAPI conversation API
WhatsApp ───┘                                  ├─> Customer context + sales state (SQLite local baseline)
                                               ├─> NBA rules
                                               ├─> LLM provider interface (local fallback now)
                                               └─> Unified salesperson workspace (planned)
```

## Current implementation boundary

- SQLite is a local development persistence choice, not a confirmed production database decision.
- Context extraction and NBA use transparent rules for active product category, budget ranges, GPU/RAM filters, follow-up questions, and no-match handling. The LLM does not select catalogue rows or change the active product category.
- `SalesAssistantProvider` isolates response generation. A Gemini adapter uses the official Google GenAI SDK when `LLM_PROVIDER=gemini` and a key is set; deterministic local fallback remains available. Ollama is not wired.
- A shared sample product catalogue with keyword matching, customer website/chat surfaces, a basic salesperson workspace, appointment requests, and approval-gated order records are implemented.
- WhatsApp transport, full RAG ingestion/vector retrieval, appointment confirmation, CRM synchronization, authentication/authorization, audit controls, and deployment are not implemented yet.
- The API has no authentication in this starter. Keep it on a trusted local development environment until access controls are added.

## Data model

`customers` stores identity, channel, JSON context, stage, and handoff flag. `messages` stores ordered customer and assistant messages with a foreign key to the customer. This compact baseline can be migrated to a production relational schema as requirements and deployment are confirmed.

`products` is the shared catalogue used by the website and recommendation lookup. It includes structured brand, RAM, GPU, VRAM, storage, and condition fields for the laptop listings. `appointments` stores requests awaiting confirmation. `orders` stores basic order intents as `pending_approval`; there is no payment processing.
