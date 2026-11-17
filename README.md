# MetaCrux AI — Adaptive AI Sales Assistant

An adaptive sales assistant MVP foundation. The backend keeps customer context and sales state together, chooses a next best action, and exposes a channel-neutral conversation API for future website and WhatsApp integrations.

## Current scope

- FastAPI backend with SQLite persistence for local development
- Customer and conversation management
- Adaptive qualification and sales-state tracking
- Explainable, rule-based next-best-action decisions
- Shared demo product catalogue and keyword search/recommendations
- Website landing page, embedded chat widget, and full-page chat
- Salesperson workspace with a named HandOffs queue and two-way replies to customers
- Appointment requests and approval-gated basic order records
- Pluggable LLM interface, with a deterministic local fallback
- Human handoff signals for uncertain or approval-required actions

This repository is a fresh implementation baseline. Live Jira mappings, credentials, external integrations, and any prior delivery claims remain unverified.

## Run locally

Use Python 3.11 or newer. From the repository root:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
# Edit .env and set LLM_PROVIDER=gemini and GEMINI_API_KEY=your_key to enable Gemini.
uvicorn app.main:app --app-dir backend --reload
```

Open `http://127.0.0.1:8000/docs` for the API interface. SQLite data is stored in `data/aisa.db` by default. Set `LLM_PROVIDER=gemini` and `GEMINI_API_KEY` in `.env` to use Google Gemini; otherwise the no-key local fallback is used.

## Main endpoints

- `GET /health`
- `POST /api/v1/customers`
- `GET /api/v1/customers/{customer_id}`
- `POST /api/v1/conversations/{customer_id}/messages`
- `GET /api/v1/conversations/{customer_id}`
- `GET /api/v1/sales/{customer_id}/next-action`
- `POST /api/v1/sales/{customer_id}/handoff`
- `GET /api/v1/products?q=solar`
- `GET /api/v1/sales/workspace`
- `GET /api/v1/sales/handoffs`
- `POST /api/v1/sales/{customer_id}/messages` (salesperson reply to a requested handoff)
- `POST /api/v1/sales/{customer_id}/handoff/resolve`
- `POST /api/v1/appointments/{customer_id}`
- `POST /api/v1/orders/{customer_id}`

The API is channel-neutral. Do not build separate customer or salesperson backends for each channel.

## Current limits

This is a runnable starter MVP, not a production deployment. WhatsApp transport, authentication and role permissions, appointment confirmation, CRM synchronization, deployment, and operational monitoring still need implementation. Orders are recorded as pending salesperson approval; payment is not processed. The sample catalogue is placeholder data and should be replaced with the business's approved products and prices. See [the architecture notes](docs/architecture.md) for boundaries and risks.

The current catalogue is structured SQL data, so product discovery uses direct database filtering and does not need vector RAG. Add RAG when there are approved product manuals, FAQs, policies, or other long-form sales documents to retrieve from; keep price and stock checks grounded in the live catalogue.

## Run the test suite

Install development checks and run the broad backend/browser cases from the repository root:

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
node --test web/tests/chat.test.js
```
