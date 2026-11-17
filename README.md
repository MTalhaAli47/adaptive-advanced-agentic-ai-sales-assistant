# MetaCrux AI — Adaptive AI Sales Assistant

An adaptive sales assistant MVP foundation. The backend keeps customer context and sales state together, chooses a next best action, and exposes a channel-neutral conversation API for future website and WhatsApp integrations.

## Current scope

- FastAPI backend with SQLite persistence for local development
- Customer and conversation management
- Adaptive qualification and sales-state tracking
- Explainable, rule-based next-best-action decisions
- Shared demo product catalogue and keyword search/recommendations
- Website landing page, embedded chat widget, and full-page chat
- Unified salesperson workspace with lead context and handoff controls
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
uvicorn app.main:app --app-dir backend --reload
```

Open `http://127.0.0.1:8000/docs` for the API interface. SQLite data is stored in `data/aisa.db` by default. No LLM key is required for the local fallback.

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
- `POST /api/v1/appointments/{customer_id}`
- `POST /api/v1/orders/{customer_id}`

The API is channel-neutral. Do not build separate customer or salesperson backends for each channel.

## Current limits

This is a runnable starter MVP, not a production deployment. WhatsApp transport, Gemini/Ollama adapters, authentication and role permissions, appointment confirmation, CRM synchronization, deployment, and operational monitoring still need implementation. Orders are recorded as pending salesperson approval; payment is not processed. The sample catalogue is placeholder data and should be replaced with the business's approved products and prices. See [the architecture notes](docs/architecture.md) for boundaries and risks.
