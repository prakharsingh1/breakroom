# Customer workspaces

The signed-in workspace reads the customer's retained, explicitly uploaded reports. It does not run customer code on the server or add sample runs to an empty account. Password accounts have real stored credentials and sessions; see [account setup](accounts.md) for email verification and recovery configuration.

`GET /api/team/workspace` requires a human session and returns only projects in the current user's memberships. Project/report counts and the ten most recent uploads exclude expired reports even before the cleanup worker deletes them. Counts are grouped in PostgreSQL; the endpoint does not load every report to build the dashboard. The returned times describe when reports were uploaded, not when a customer claims an execution occurred.

`GET /api/team/catalog` supplies all 24 bundled manifests with their actual case versions and SHA-256 hashes. A suite stores the chosen metadata through the existing project suite endpoint. Private suite metadata remains private; selecting a bundled case does not publish a customer report.

`GET /api/team/projects/{project_id}/insights` derives check failures, missing coverage and next-step suggestions from retained reports. The query projects report metadata and checks without loading event timelines or business-state snapshots. Suggestions are explicit rules associated with recorded checks, such as reconciling an ambiguous refund with the same operation key, retrying a failed ticket step independently, preserving customer/order ownership, or waiting for terminal refund evidence. They neither diagnose arbitrary agent source nor automatically repair it.

A suite snapshot selects the latest matching retained upload for each required case, using case ID, version and original manifest hash. Redacted uploads retain original source hashes, allowing generated variations to match their original catalog entry. This is customer-supplied provenance; the service does not certify its authenticity. A known failed check takes precedence. Missing cases, mismatched versions/hashes, unsupported cases, incomplete execution, unknown checks and untriggered required faults cannot produce a passing snapshot.

Different selected reports may come from different agent builds or seeds. A passing snapshot is not release approval or a statistical claim. Use the local runner's required-case checks and compatible baseline/candidate comparisons to evaluate a specific release. The original reports and individual check references remain available for review.

## Copyable project invitations

An owner can create an invitation for a specific email and either a viewer or developer role. Inviting another owner is not supported; existing owner controls remain responsible for role changes. Invitations expire in one to seven days. Creating a link does not send email or reserve a seat.

The creation response contains the link once, as `/invite#token=...`. The server stores only its SHA-256 hash; listing invitations never returns the secret. The fragment keeps the token out of ordinary URL requests and server access logs. The application submits it in the JSON body of `POST /api/team/invitations/accept`, using the signed-in person's session, exact origin and CSRF token. It must not be put in query parameters or third-party URLs.

Acceptance requires a verified, matching email. The explicitly enabled localhost development identity may exercise this flow in development/test mode; password accounts must verify even there. A copied link is insufficient for an unrelated account. The server rechecks expiry, revocation, current inviter ownership and seat capacity under a project lock. Reissuing a link for the same email revokes earlier unaccepted links. Concurrent acceptance consumes a link once. Acceptance preserves an existing member's role rather than silently changing it.

Schema migration 3 adds project-owned invitations with cascading deletion. Migration 4 adds account credentials and tokens. The periodic bounded cleanup includes expired invitations and account tokens. Live production operation still requires configured HTTPS, email delivery and database/backup operations. No invitation email is sent automatically by this feature.
