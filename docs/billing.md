# Optional Stripe test billing

Billing is disabled by default. The free local engine, CLI and synthetic demo do not import the billing SDK or require an account. The separate team service keeps its M5 workflow available with explicit local limits when billing is disabled: 25 project members, 1000 retained reports and 256 MiB of logical report storage per project. There is no live checkout in this release.

The implemented adapter uses `stripe==15.6.1`, with API version `2026-08-26.dahlia` pinned explicitly. The SDK and lifecycle tests use an offline transport and signed synthetic fixtures. They do not demonstrate a working merchant account, a completed Stripe sandbox purchase, tax configuration, or production readiness. Stripe account availability and onboarding for the owner remain external prerequisites. No account, price, webhook destination or charge was created during implementation.

## Supported contract

The server selects one configured monthly USD price, with quantity one and card payment. The illustrative amount is 4900 USD cents, or USD 49 per month; this is configurable test pricing, not evidence of willingness to pay. Trials, coupons, account credits, prorations, taxes, usage billing, multiple subscription items, Connect and live mode are outside this adapter's supported billing contract. A paid invoice must match the configured price, quantity, integer amount, customer, subscription, subscription item and exact paid period. Unsupported or malformed snapshots cannot grant new access.

The API creates a hosted Checkout Session through the maintained SDK. Browser inputs cannot choose a price, amount, currency, customer ID or return URL. The only allowed plan selector is `team`. Return URLs are fixed to the configured project page. A return query string, a successful browser redirect or a Checkout Session alone never unlocks a plan. Stripe documents Checkout parameters and subscription mode in its [Checkout API reference](https://docs.stripe.com/api/checkout/sessions/create?lang=python).

## Server configuration

The default `BREAKROOM_BILLING_MODE=disabled` creates no provider client and makes checkout/webhook endpoints return an explicit unavailable response. To configure a later owner-authorized Stripe sandbox, supply all of:

| Variable | Meaning |
| --- | --- |
| `BREAKROOM_BILLING_MODE` | `test`; every other enabled mode, including `live`, is rejected |
| `BREAKROOM_STRIPE_SECRET_KEY` | Test secret or restricted key, beginning `sk_test_` or `rk_test_` |
| `BREAKROOM_STRIPE_WEBHOOK_SECRET` | Test endpoint signing secret, beginning `whsec_` |
| `BREAKROOM_STRIPE_PRICE_ID` | Existing recurring USD test price matching the configured amount |
| `BREAKROOM_BILLING_AMOUNT_MINOR` | Integer USD cents, default `4900` |
| `BREAKROOM_BILLING_CURRENCY` | Only `usd` is supported |
| `BREAKROOM_BILLING_SEAT_LIMIT` | Members per paid test project, including owners; default `10` |
| `BREAKROOM_BILLING_REPORT_LIMIT` | Retained reports per paid test project; default `1000`, maximum `1000` |
| `BREAKROOM_BILLING_STORAGE_LIMIT` | Logical retained report bytes; default `67108864` (64 MiB) |

The team image installs `apps/api/requirements-billing.lock`. Core packaging remains independent. The test billing mode requires complete configuration and rejects live keys, live events and mismatched event API versions. Do not add credentials to the local Compose overlay or commit them. Configuring an externally reachable webhook and performing actual sandbox transactions require separate owner authorization and setup.

The SDK uses an eight-second HTTP operation timeout and at most one retry. Application operation IDs remain stable across those retries. These are SDK network-operation bounds, not a total wall-clock guarantee against a continuously streaming peer. The adapter permits only Stripe's fixed API endpoint, verifies TLS, and does not follow response redirects. The [SDK release metadata](https://pypi.org/project/stripe/) and [tagged API-version source](https://raw.githubusercontent.com/stripe/stripe-python/v15.6.1/stripe/_api_version.py) were verified on 2026-09-07.

## Durable lifecycle and access

Migration 2 adds project-bound billing accounts, idempotent operations and a signed-event inbox. No card data, provider tokens or raw invoice/customer payloads are retained. The inbox stores event/object IDs, a payload checksum, state, attempts and lease metadata. A project deletion cascades these records after cancellation/Checkout reconciliation prevents orphaned recurring charges.

| Current verified state | Access to new imports and additional seats |
| --- | --- |
| Billing disabled | Explicit local development limits |
| Pending Checkout, incomplete or unverified payment | Read only |
| Active, supported subscription with a verified paid period still in the future | Paid test limits |
| Trialing, past due, unpaid, paused, canceled, unsupported or unknown | Read only |
| Provider temporarily unavailable | Existing verified access may continue only through its already recorded paid period; no extension is inferred |

Retained authorized reports stay readable and exportable until retention expires. A billing downgrade does not delete customer reports. Each new import and member addition checks access and usage inside the same project-locked database transaction as its write. Duplicate imports return their existing record without using another slot. Storage usage measures the UTF-8 size of PostgreSQL's retained JSON representation, including expired records awaiting cleanup; it is a logical quota, not a claim about compressed disk consumption. Private metadata retains its separate bounded M5 limits.

Current subscription periods are read from `items.data` and paid invoice relationships from `parent.subscription_details.subscription`. The paid line also identifies the same subscription item and price. These follow the current [Subscription object](https://docs.stripe.com/api/subscriptions/object) and [Invoice Line Item object](https://docs.stripe.com/api/invoice-line-item/object) contracts.

## Webhook delivery and reconciliation

`POST /api/team/billing/webhook` authenticates the exact raw request bytes with the Stripe SDK, the `Stripe-Signature` header and the configured test endpoint secret. It checks timestamp tolerance, schema bounds, test mode and the pinned API version. The body is limited to 256 KiB. This route uses provider authentication rather than session CSRF. All browser billing mutations still require an owner session, exact Origin and CSRF token; API keys cannot manage billing.

Verified event IDs are deduplicated transactionally. Reusing an event ID with different content is rejected. Only known customer/Checkout relationships are accepted into the project inbox. The worker retrieves current provider state; webhook snapshots and their timestamps do not select the plan. Stripe explicitly warns that delivery order is not guaranteed and multiple events can share the same second. Its [webhook documentation](https://docs.stripe.com/webhooks) also requires raw-byte signature verification and duplicate handling.

The worker claims a bounded inbox row using PostgreSQL `FOR UPDATE SKIP LOCKED`, with a 120-second recovery lease. It then holds both the project and event row locks while reconciling and committing, so another worker cannot steal the lease and commit conflicting account state. Recoverable errors retry at 30-second intervals, at most five attempts. Failed or unsupported events remain visible for review. Health exposes bounded queue counts and worker status without customer or event IDs. Operators can reconcile a known project directly after a missing webhook or transient outage; no arbitrary provider target is accepted.

## Retries, cancellation and recovery

Checkout and cancellation require a stable 64-character hexadecimal `Idempotency-Key`. The service first persists the reservation, then uses a stable provider operation key. If a response is lost, retry the same request. A fresh key cannot bypass an uncertain Checkout. The browser preserves only these non-secret request identifiers in tab session storage across reloads; those identifiers grant no authorization.

Uncertain operations older than 23 hours require operator reconciliation rather than reissuing a provider write whose idempotency record may have expired. Stripe describes the provider retention window and changed-parameter behavior in its [idempotent request reference](https://docs.stripe.com/api/idempotent_requests). A completed Checkout is not considered safely replaceable merely because its recorded expiry timestamp passed: its current provider state must show it expired without a subscription, or its known subscription must already be terminal.

Canceling with `at_period_end=true` keeps the already paid period and schedules the end of renewal. `false` requests immediate cancellation and revokes new-write access after provider confirmation. Cancellation retries retain the original subscription ID, so they cannot cancel a replacement subscription. The underlying operations follow Stripe's [cancellation documentation](https://docs.stripe.com/billing/subscriptions/cancel).

Project deletion is blocked while a subscription remains nonterminal or a Checkout is open/uncertain. Confirm immediate cancellation, or reconcile a provider-confirmed expired Checkout, before deletion. A failed event can be inspected through health and project state; repair the provider/configuration issue, then use owner reconciliation. The system does not silently retry an exhausted event indefinitely. Existing provider invoices and merchant records follow the provider's own retention, outside application database deletion.

## Verification and external prerequisites

```sh
PYTHONPATH=apps/api:packages/breakroom-core/src venv-team/bin/python -m unittest discover -s tests/billing -v
PYTHONPATH=apps/api:packages/breakroom-core/src venv-team/bin/python -m unittest discover -s tests/team -v
```

Lifecycle tests use unique disposable PostgreSQL schemas and never substitute SQLite. They exercise actual HTTP route authorization, independent invoice checks, concurrent reservations/quotas, duplicate and reordered signed events, failed provider retrieval, recovered leases and cancellation races. SDK tests inspect requests through the real pinned Stripe client with a transport that cannot access the network.

The owner must separately establish merchant eligibility, sandbox credentials and price, the pinned-version test webhook, HTTPS routing, supported invoice settings, operational alerting, backup policy and recovery procedures before an external test. Live billing remains unsupported and disabled. See [operations and backup/restore](operations.md) and the [team endpoint contract](team-contract.md).
