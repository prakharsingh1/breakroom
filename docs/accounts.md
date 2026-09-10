# Customer accounts

Breakroom supports durable email/password accounts backed by PostgreSQL. Sign up at `/signup`, sign in at `/signin`, then create a workspace and upload minimized reports from your own runner. This is real account and workspace storage; the separate public crash-test demo remains a simulated reference example. Customer code continues to run on customer-owned machines.

The account flow includes email verification, password recovery, password changes, session rotation and server-side sign-out. Production workspace access requires a verified email. Project invitations also require verified email ownership; an unverified local account cannot claim somebody else's invitation. The explicit local development identity endpoint is retained only for tests and is rejected in production.

## Local setup

The local Docker Compose team service enables email/password accounts by default. Accounts persist in the PostgreSQL volume across application restarts. Without SMTP, local accounts can use workspaces and are visibly marked **unverified**. The API reports `mail_available: false`; it never invents email delivery or returns a recovery token. To exercise verification/recovery locally, configure a real TLS SMTP server that you control. Tests use an offline capturing mailer and send no email.

Do not use local development mode to serve real customers. It allows unverified workspace access and loopback HTTP for development. The public repository contains example configuration, not running public hosting.

## Production mail and authentication configuration

Configure private environment variables on the server; do not place credentials in source, browser environment variables, GitHub, or client-visible responses.

| Setting | Meaning |
|---|---|
| `BREAKROOM_TEAM_ENV=production` | Requires HTTPS, secure cookies and verified password accounts before workspace access. |
| `BREAKROOM_TEAM_PUBLIC_ORIGIN` | Exact HTTPS origin, such as `https://your-domain.example`; no path/query/trailing slash. |
| `BREAKROOM_PASSWORD_ENABLED=1` | Default; `0` disables password endpoints and existing password account credentials. |
| `BREAKROOM_TEAM_DEV_LOGIN=0` | Required for production. |
| `BREAKROOM_SMTP_HOST` | Operator-configured SMTP hostname. |
| `BREAKROOM_SMTP_PORT` | Default `465`; commonly `587` for STARTTLS. |
| `BREAKROOM_SMTP_MODE` | `ssl` (default) or `starttls`. Both require certificate-verified TLS. Plaintext mode is unsupported. |
| `BREAKROOM_SMTP_FROM` | Single sender email address verified with your mail provider. |
| `BREAKROOM_SMTP_USERNAME`, `BREAKROOM_SMTP_PASSWORD` | Paired SMTP credentials. Both may be omitted for an operator-controlled TLS relay. |
| `BREAKROOM_TEAM_SESSION_TTL` | Session lifetime in seconds, default 86400; supported 300–604800. |

Production startup fails if password accounts are enabled without SMTP configuration. Configuration alone does not prove delivery: validate your sender, DNS records and actual verification/reset delivery in the intended environment. SMTP uses a certificate-verified TLS connection with an eight-second operation timeout and never logs provider error text. The app sends account emails only in response to signup or an explicit recovery/verification request. Failed signup email leaves the durable account available for resend; delivery is not represented as successful. Recovery returns a generic response before SMTP work to avoid exposing account existence through SMTP latency. Links can be requested again after transient mail failure.

OIDC remains an optional separately configured identity method. Password signup will not silently link to an existing OIDC or development identity just because its email matches. Account linking requires an explicitly designed migration; it is intentionally absent.

Public user responses include `auth_method` (`password`, `oidc` or `development`) so account settings can offer only supported actions. `/api/team/health` exposes aggregate mail configuration and status (`disabled`, `ready` or `retry_required`). SMTP failure marks a retry as required; a later successful dispatch resets readiness. These fields contain no email addresses, message content, tokens or provider errors. Readiness describes configuration/recent dispatch, not guaranteed inbox delivery.

## Password and session behavior

- Passwords are 12–128 Unicode characters. Spaces and case are significant; passwords are never trimmed or truncated. Email addresses are case-normalized and must use the supported ASCII mailbox/domain form, max 254 characters. Internationalized addresses are not yet supported.
- Hashes use Argon2id through pinned `argon2-cffi==25.1.0` and `argon2-cffi-bindings==26.1.0`, using the RFC 9106 low-memory profile: 64 MiB, three iterations, parallelism four, random 16-byte salts. At most two hashes run concurrently per process; saturation returns a retry response. Parameters are stored in the encoded hash and checked for rehash at login. See the [maintainer API documentation](https://argon2-cffi.readthedocs.io/en/stable/api.html) and [bindings package](https://pypi.org/project/argon2-cffi-bindings/), checked 2026-09-10.
- Missing accounts and incorrect passwords get the same authentication error, and missing accounts still perform dummy Argon2 verification. Registration conflict and field-validation responses are distinct; this is not a claim that all account operations are indistinguishable.
- Sessions use random opaque tokens. PostgreSQL stores only their SHA-256 hashes; cookies are HttpOnly, SameSite=Lax, scoped to `/api/team`, and Secure over HTTPS. Login rotates the current browser session. Logout revokes it on the server.
- Every public account POST requires the exact configured Origin. Authenticated account changes additionally require the session's CSRF token. No credentials are accepted through URL query strings.
- Password reset and password change revoke every existing session and outstanding account link. Change requires the current password plus CSRF and creates a fresh session for that browser. Reset leaves the user signed out. Password reset alone does not mark an address verified; request a new verification link if needed. Existing scoped API keys must be separately reviewed/revoked in project settings.
- Login, reset and change lock the credential row when issuing sessions or replacing a hash. An in-flight old-password login cannot survive a completed reset through a session-creation race.

## Verification, recovery and abuse limits

Account links contain 256 bits of randomness and are stored only as SHA-256 hashes. Verification links expire after one hour and reset links after 30 minutes. Resending invalidates earlier links of the same purpose. Tokens are purpose-bound and consumed transactionally; concurrent use succeeds at most once. They are delivered through `/verify-email#token=...` and `/reset-password#token=...` fragments, then POSTed by the first-party page. Fragments are not sent as HTTP request paths, query parameters or Referer URLs.

Persistent PostgreSQL account/IP budgets are reserved atomically before expensive work and survive restarts and multiple API workers. They store hashed bucket identifiers, not raw peer addresses. Budgets per account / peer are: login 8 / 80 attempts in 15 minutes; registration 5 / 20 in one hour; combined verification/recovery email requests 3 / 20 in 15 minutes; link consumption 12 / 60 in 15 minutes; password changes 8 / 80 in 15 minutes. Expired token and budget rows are swept in bounded batches. HTTP request limits remain an additional layer.

Untrusted forwarded-IP headers are ignored. Deployments behind a shared reverse proxy therefore share a peer budget unless a separately reviewed edge identity design is implemented. Size capacity and edge rate limits accordingly. The initial implementation does not include MFA, passkeys, a breached-password lookup service, automated dormant-account purging, or email-address changes. The password minimum is a local policy; it is not a certification or a claim of complete account security.

## API contract and verification

All paths below have prefix `/api/team/auth`.

| Method and path | Body and result |
|---|---|
| `GET /config` | Password/OIDC availability, mail availability and whether verification is required. |
| `POST /register` | `{email, display_name, password}` → 201, public user, CSRF, verification/mail state and session cookie. |
| `POST /password-login` | `{email, password}` → public user, CSRF and rotated session cookie. |
| `POST /request-verification` | Authenticated Origin + CSRF → 202, honest mail/dispatch state. |
| `POST /verify-email` | `{token}` → verified address; invalid, expired or reused link returns 400. |
| `POST /request-password-reset` | `{email}` → generic 202 response, regardless of account existence. |
| `POST /reset-password` | `{token, password}` → revokes sessions and links, then requires sign-in. |
| `POST /change-password` | `{current_password, password}` + session/CSRF → replaces password and rotates session. |
| `POST /logout` | Existing session/CSRF → server-side session revocation. |

Schema migration 4 adds password credentials, account link digests and durable abuse budgets. Existing users, projects and reports remain intact; migration does not convert development identities into verified customer identities.

Run real PostgreSQL lifecycle/security checks with:

```sh
PYTHONPATH=apps/api:packages/breakroom-core/src venv-team/bin/python -m unittest discover -s tests/team -p test_password_auth.py -v
```

Each database test creates and removes only its generated temporary schema. Tests verify real hashes, account persistence, concurrent token use, reset/login serialization, session revocation, production verification gates, durable throttling, exact Origin/CSRF, bounded validation, SMTP TLS ordering and truthful mail failures. No real provider credentials or messages are used. Test counts and final integrated results are recorded in [STATUS.md](../STATUS.md).
