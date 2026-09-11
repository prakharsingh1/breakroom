# Hosted team API contract

Implementation contract for M5. A separate FastAPI service runs on port 8001 under `/api/team`; the website proxies these routes. The fixed demo API remains separate. Reports are customer-generated evidence, not independently certified executions. Report uploading never executes code or follows URLs. The separately configured [sandbox control plane](sandbox.md) handles explicit source deployments, pinned GitHub imports and isolated queued execution.

All IDs are opaque strings. Times are UTC ISO 8601 strings. Lists return `{ "items": [...] }`. Errors use an HTTP status and `{ "detail": "message" }`; input validation may return a safe list of error types/messages without submitted values or field names. Cookie-authenticated writes require the `X-CSRF-Token` returned by `GET /api/team/me` and a same-origin browser request. Project-scoped bearer API keys require no cookie/CSRF token and retain their creator's current membership restrictions.

## Session and identity

- `GET /health`: `{ "status": "ok", "service": "breakroom-team", "database": "ready", "schema_version": 2, "retention": "ready" | "retrying", "billing": { "mode", "state", "worker", "queue": { "pending", "processing", "retrying", "failed" } } }`.
- `GET /me`: `{ "user": null | { "id", "email", "display_name" }, "csrf_token": null | "opaque", "projects": [{ "id", "name", "role", "retention_days", "created_at" }], "auth": { "oidc_available": boolean, "dev_login_available": boolean } }`.
- `GET /auth/login` and `/auth/callback`: configured OIDC login and callback. Unconfigured login returns an explicit unavailable error. Identity is verified issuer plus subject, with a verified email required for membership lookup.
- `POST /auth/dev-login`: `{ "email": "owner@example.invalid", "display_name": "Local owner" }`; only enabled explicitly in a localhost development/test configuration. This is not a production authentication method. Returns a session/user result and sets an HttpOnly session cookie; call `/me` for projects/CSRF.
- `POST /auth/logout`: invalidates the server session and clears its cookie.

## Projects and membership

- `GET /projects`: current user's projects with server-derived roles.
- `POST /projects`: `{ "name": "Support QA", "retention_days": 30 }`; creates the project and owner membership, returning the project object.
- `GET /projects/{project_id}`: project object `{ "id", "name", "role", "retention_days", "created_at" }`.
- `PATCH /projects/{project_id}`: `{ "name"?: "...", "retention_days"?: 7 }`, owner only. Retention is bounded to 1–365 days and changes shorten existing retention where applicable.
- `DELETE /projects/{project_id}`: owner only; returns `{ "deleted": true, "project_id" }`. Deletes memberships, keys, stored reports, private suites and their exportable data. No external artifact storage is used. Backup copies follow operator-controlled backup retention separately.
- `GET /projects/{project_id}/members`: `{ "items": [{ "user_id", "email", "display_name", "role" }] }`; requires a user session, never a report API key.
- `POST /projects/{project_id}/members`: `{ "email": "developer@example.invalid", "role": "viewer" | "developer" | "owner" }`, owner only. Adds an existing verified account; no invitation message is sent. Unknown email returns an explicit error.
- `PATCH /projects/{project_id}/members/{user_id}`: `{ "role": "..." }`; `DELETE` removes membership. The last owner cannot be removed or demoted. Every request rechecks membership, including keys issued before a removal.

Viewer can read project data; developer can upload reports and manage private suite metadata; owner additionally manages project settings, membership and API keys.

## API keys

- `GET /projects/{project_id}/keys`: owner only; `{ "items": [{ "id", "name", "prefix", "scopes", "created_at", "expires_at", "revoked_at" }] }`. Never returns secrets or stored hashes.
- `POST /projects/{project_id}/keys`: `{ "name": "CI report upload", "scopes": ["reports:write"], "expires_days": 30 }`, owner only. Allowed scopes are `reports:read`, `reports:write`, `suites:write`. Returns key metadata plus `secret`, shown once. Only a SHA-256 hash of the high-entropy secret is stored.
- `DELETE /projects/{project_id}/keys/{key_id}`: revokes the key; returns `{ "revoked": true, "id" }`.

Keys are bound to one project and creator. A browser-supplied project ID is only a lookup selector, never authorization. Secret values never appear in URLs.

## Reports, evidence, export and comparison

- `POST /projects/{project_id}/reports`: a strict minimized upload envelope from the local preparation helper, with a stable 64-character lowercase hexadecimal `Idempotency-Key`. Browser clients may hash the exact uploaded JSON text; the CLI hashes canonical JSON. The server separately computes the canonical validated payload hash and rejects reuse of a key for different content. Format: `{ "schema_version": "1.0", "kind": "customer_generated_report", "report": <minimized validated core report>, "privacy": <validated minimization/provenance metadata> }`. Maximum request size is 2 MiB. The API rejects unprepared raw reports and never silently uploads from local runs.
- Successful creation: HTTP 201 `{ "id", "project_id", "created_at", "expires_at", "case_id", "verdict", "provenance": "customer_generated", "privacy", "duplicate": false }`. An identical retry returns HTTP 200 with the same ID and `duplicate: true`; conflicting reuse of an idempotency key returns HTTP 409.
- `GET /projects/{project_id}/reports`: summaries in `items`; expired reports are excluded.
- `GET /projects/{project_id}/reports/{report_id}`: summary plus `upload`, the immutable stored minimized envelope.
- `GET /projects/{project_id}/reports/{report_id}/evidence`: `{ "events", "initial_state", "final_state", "checks", "agent_result" }` from that stored report. Authorization and retention are checked again for this nested path.
- `GET /projects/{project_id}/reports/{report_id}/export`: JSON download of the stored minimized envelope. Export does not fetch attachments, create runnable customer code, or bypass project ownership/retention.
- `POST /projects/{project_id}/compare`: `{ "baseline_id", "candidate_id" }`; returns the core compatible-comparison result plus `provenance: "customer_generated"`. Both reports must belong to the authorized project and remain unexpired.

Imported records are immutable. Original hashes in privacy metadata describe customer-supplied provenance, not proof of honest execution. Deterministic minimization may produce new manifest/fixture hashes; those are independently validated as the uploaded representation. Automated redaction is imperfect: operators must review the prepared file before an explicit upload.

## Private suite metadata and retention

- `GET /projects/{project_id}/suites`: `{ "items": [{ "id", "project_id", "name", "created_at", "updated_at", "cases" }] }`; requires a user session or the explicit `suites:write` key scope.
- `POST /projects/{project_id}/suites`: `{ "name": "Private refund regressions", "cases": [{ "case_id", "case_version", "manifest_hash" }] }`, developer/owner. Stores bounded private metadata only, with at most 128 case descriptors. No source, commands, filesystem paths or executable assertions are accepted.
- `DELETE /projects/{project_id}/suites/{suite_id}`: developer/owner; returns `{ "deleted": true, "id" }`.
- `POST /projects/{project_id}/retention/cleanup`: owner only; deletes expired reports and returns `{ "deleted_reports": integer }`. Read routes enforce expiry even before cleanup runs. Startup and a periodic bounded sweep also remove expired reports, sessions and login attempts. Project deletion removes all current database-owned records through foreign-key cascades.

Request/response details are covered by the team API tests. Hosted availability and live OIDC provider configuration remain distinct from a local development demonstration. See [service configuration and test commands](../apps/api/README-team.md).

## Optional test billing

All project billing routes require an owner session. Report API keys never authorize billing. Browser writes require Origin and CSRF as above. Billing is disabled by default and preserves the M5 local workflow under explicit limits.

- `GET /projects/{project_id}/billing`: `{ "mode": "disabled" | "test", "configured": boolean, "provider": "stripe", "amount_minor": 4900, "currency": "USD", "interval": "month", "status", "access": "local_development" | "paid" | "read_only", "sync_state": "ready" | "pending" | "retrying" | "unknown", "cancel_at_period_end": boolean, "current_period_end": null | "ISO8601", "paid_through": null | "ISO8601", "can_cancel": boolean, "checkout_available": boolean, "limits": { "seats", "reports", "storage_bytes" }, "usage": { "seats", "reports", "storage_bytes" }, "notice": "..." }`.
- `POST /projects/{project_id}/billing/checkout`: `{ "plan": "team" }` plus stable 64-hex `Idempotency-Key`. Returns `{ "checkout_id", "url", "expires_at": "ISO8601", "test_mode": true, "reused": boolean }`. Unknown price/amount/return-URL fields are rejected. A 503 may mean an uncertain result; preserve the key. A 410 means that known Checkout expired. Success-page navigation grants no access.
- `POST /projects/{project_id}/billing/cancel`: `{ "at_period_end": true | false }` plus stable `Idempotency-Key`. Returns the billing view after confirmation. `false` means immediate cancellation. Same-key retries are bound to the original subscription.
- `POST /projects/{project_id}/billing/reconcile`: no body. Retrieves current provider state only for this project's known objects and returns the billing view. A fresh configured project with no operations returns its current view without provider calls. An uncertain Checkout without a known provider ID requires retrying its original request.
- `POST /billing/webhook`: raw signed Stripe test-event JSON, bounded to 256 KiB. Uses provider signature authentication rather than cookie/CSRF. Returns `{ "accepted": true, "duplicate": boolean, "status" }`, or `{ "accepted": false, "reason" }` for unused/unrelated events. It queues current-state reconciliation; acceptance alone does not grant an entitlement.

Disabled provider mutations return 503. Limits return 409; unverified paid-test access returns 402 for new imports/member additions. Authorized retained reports remain readable. Open/uncertain Checkout and nonterminal subscriptions block project deletion until reconciled or immediately canceled. See [billing configuration, lifecycle and limits](billing.md).
