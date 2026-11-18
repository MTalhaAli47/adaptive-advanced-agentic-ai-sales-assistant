# AISA reliability audit and implementation notes

## Baseline findings

The application is a FastAPI service with one SQLite database, a deterministic sales/context module, a provider abstraction for response text, and static chat, store, admin, and salesperson pages. Product retrieval and related-product scoring already use the existing catalogue; those components were retained.

Observed root causes in the baseline:

- Message interpretation and business actions were interleaved in one large `/messages` handler, so pending fields and handoff state could dominate a new customer intent.
- Selection accepted plain numeric positions but not common ordinal phrases/typos. Budget parsing could interpret storage capacities as prices and did not handle common amount phrases.
- Chat order intent immediately inserted an order. There was no persistent customer review token or explicit confirmation gate.
- Cart checkout also created orders immediately, and cart routes trusted a customer ID without checking the customer's private token.
- The salesperson “Conversations” list was labelled as leads, but there was no persistent qualification record, priority rationale, status, or follow-up date.
- Admin order updates validated payload shapes but did not enforce legal order, payment, and fulfillment transitions.
- There was little structured diagnostic logging for routing and catalogue results.

## Preserved architecture

- Existing customer, conversation, product, cart, order, appointment, and product-relationship tables remain the system of record.
- Existing `extract_context`, `choose_next_action`, catalogue APIs, recommendation scorer, FastAPI app, and salesperson workspace are extended; no second catalogue or conversational service is introduced.
- The LLM remains a language response provider. Catalogue, price, ownership, review, and state decisions remain backend responsibilities.

## Changes implemented in this reliability pass

- Added persistent `order_reviews` and `leads` tables, created during the normal database initialization/migration path.
- Chat and cart checkout now create an order summary first. A customer must explicitly confirm the same review before order rows are created. Confirmation checks customer-token ownership, price, active availability, and—on cart reviews—the unchanged cart snapshot. Repeated confirmation returns existing order IDs instead of creating duplicates.
- The cart page displays its review and requires a separate confirmation click. Cart APIs now require the profile's private customer token. Changes to a cart invalidate its pending review.
- Direct order creation API now returns a review instead of creating an order and requires the customer token.
- Added deterministic ordinal selection (including `4rth`), common budget phrase/typo parsing, and separate storage-specification context/filtering.
- Added one lead per customer, fact-based score/status/priority with an explanation and missing-information notes, salesperson lead search/filtering, status updates, and follow-up scheduling in the existing workspace.
- Added backend validation for order, payment, and fulfillment transitions.
- Added structured routing/catalogue logs without logging message text or credentials.
- Added regression tests for confirmation ownership/idempotency, stale price review, lead scoring/management, budget phrases, storage parsing, and ordinal product resolution.

## Risks and remaining work

- SQLite schema creation currently serves as the migration mechanism; production deployments should back up the database before upgrades.
- The LLM-assisted semantic interpretation is still bounded by the current sales rules and provider; this pass does not add a separate semantic orchestration service.
- Lead assignment and team-level access control are not present in the current single-salesperson identity model. The new `assigned_to` field is editable, but multiple staff accounts and per-lead authorization need a real staff table before they should be enabled for a team.
- Appointment availability/business-hours validation and reschedule/cancel customer workflows remain limited to the current requested/confirmed admin process.
- Cross-channel WhatsApp transport/identity verification is not implemented in this repository, although the same backend customer and conversation APIs can be reused by a future adapter.
- Backend tests require a working Python installation. This environment currently resolves `python` to the disabled Windows Store alias, so the backend suite has not been executed here.

## Verification status

The browser-side Node tests, JavaScript syntax checks, and `git diff --check` have been run in this environment. Backend regression tests were added but still need to be executed with Python. This audit is a focused reliability pass; it is not a claim that every requirement in the full overhaul specification is complete.
