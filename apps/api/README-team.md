# Team reporting service

This separate FastAPI service stores explicit customer-generated report imports in PostgreSQL. It never runs adapters, receives source archives, fetches report URLs, or turns private metadata into public cases. The synthetic demo service and dependency-free local engine remain separate.

The local team overlay is started from the repository root:

```sh
docker compose -f infra/compose.yaml -f infra/compose.team.yaml up --build --wait
```

Open `http://127.0.0.1:3000/signup` to create a real password account, then use `/projects`. The hostname must match the configured public origin exactly. This overlay explicitly enables development identity on loopback. It uses a development-only PostgreSQL password and proxy secret and is not a public deployment configuration. The team API has no host port. The Next server overwrites the local-proxy header with its server-only secret before forwarding a request.

For a Python development process with PostgreSQL already available:

```sh
python3 -m venv venv-team
venv-team/bin/python -m pip install -r apps/api/requirements-billing.lock
export PYTHONPATH=apps/api:packages/breakroom-core/src
export BREAKROOM_TEAM_ENV=development
export BREAKROOM_TEAM_DEV_LOGIN=1
export BREAKROOM_TEAM_PUBLIC_ORIGIN=http://127.0.0.1:3000
venv-team/bin/python -m uvicorn breakroom_api.team:app --host 127.0.0.1 --port 8001 --no-access-log
```

Set `BREAKROOM_TEAM_DATABASE_URL` to the reviewed PostgreSQL connection URL if it differs from the local test instance on port 54329. The service requires `postgresql+psycopg`; it has no SQLite fallback. `--no-access-log` avoids recording authorization codes in OIDC callback query strings. Database exception responses and request validation errors omit rejected input, connection details and SQL parameters.

[Account setup](../../docs/accounts.md) defines password, SMTP and verification settings. [Workspace endpoints](../../docs/workspaces.md) define insights, catalog and invitations. Configuration is read only on server startup:

| Variable | Meaning |
| --- | --- |
| `BREAKROOM_TEAM_DATABASE_URL` | PostgreSQL URL; store deployment credentials outside source control |
| `BREAKROOM_TEAM_DB_SCHEMA` | Validated schema name, default `public` |
| `BREAKROOM_TEAM_ENV` | `development`, `test`, or `production` |
| `BREAKROOM_TEAM_PUBLIC_ORIGIN` | Exact external origin, HTTPS required in production |
| `BREAKROOM_TEAM_DEV_LOGIN` | Explicit `1` enables local development identity; production startup rejects it |
| `BREAKROOM_TEAM_LOCAL_PROXY_SECRET` | Optional secret of at least 32 characters for the internal development proxy; never send to client JavaScript |
| `BREAKROOM_TEAM_SESSION_TTL` | Session lifetime, 300–604800 seconds, default 86400 |
| `BREAKROOM_TEAM_OIDC_ISSUER` | Configured OIDC issuer |
| `BREAKROOM_TEAM_OIDC_CLIENT_ID` | Registered OIDC client ID |
| `BREAKROOM_TEAM_OIDC_CLIENT_SECRET` | Provider secret when required; never returned to the browser |
| `BREAKROOM_TEAM_OIDC_REDIRECT_URI` | Optional registered callback URL; defaults to the public origin plus `/api/team/auth/callback` |
| `BREAKROOM_TEAM_RATE_LIMIT` | Single-worker requests per peer per minute, default 120 |
| `BREAKROOM_TEAM_RETENTION_SWEEP_SECONDS` | Server-side cleanup interval, default 300 seconds, maximum 3600 |

There is no session signing key: sessions use random opaque secrets, only SHA-256 digests of those secrets are stored. Cookies are HttpOnly, SameSite Lax and Secure for HTTPS/production. Cookie writes require exact Origin and a separate CSRF token. A bearer key is restricted to its project, explicit scopes, expiry and the creator's current role. Member emails require a human session; report scopes do not imply access to private suite metadata.

OIDC uses Authlib 1.8.0 and joserfc 1.7.5 for maintained protocol and cryptographic validation, with authorization code flow, S256 PKCE, RS256 ID tokens, issuer/audience/nonce/time validation and verified email. Identity is issuer plus subject. Different identities are never linked automatically by email. The integration requires discovery metadata advertising S256 and RS256. When OIDC is unconfigured, provider sign-in is unavailable; email/password sign-in works independently. Fixture tests use locally generated RSA keys and a mock identity-provider transport; no live provider or account has been configured or verified.

Migrations 1–4 are defined in `breakroom_api/team_db.py`; migration 2 adds optional billing records, migration 3 invitation records, and migration 4 password credentials, recovery tokens and durable account limits. Startup takes a PostgreSQL advisory transaction lock, creates the versioned schema and constraints, and rejects an unsupported version. Imported report content is protected by a database trigger. Future schema changes require a reviewed versioned migration; this service does not silently reset a database. Project deletion cascades current report records, keys, membership, suite metadata and billing records after any test subscription is confirmed canceled. Export is derived directly from the retained report, so there are no orphaned filesystem exports to clear.

Startup and a periodic worker each sweep at most 1000 expired reports, 1000 expired sessions and 1000 login attempts per interval. Every read also enforces report expiry before a sweep. A large backlog may require multiple intervals; owners can run project cleanup immediately. Existing retention can be shortened, but increasing a project's policy does not extend old records. Backups and database logs are controlled by the operator and must have their own disclosed retention. `/api/team/health` exposes the cleanup worker's retry status.

Imports are capped at 2 MiB; request bodies have a 10-second read deadline and JSON depth/node limits. The beta additionally limits 100 projects per user, 100 private suites and 1000 retained imports per project. Rate limits are in-process and per peer, so the provided single-worker configuration is required; replicas need an explicit shared ingress limit. No CORS middleware grants cross-origin access.

Read [the endpoint contract](../../docs/team-contract.md) and [the customer worker protocol](../../docs/customer-worker.md) for project and CLI integration. Review the minimized file before an explicit upload: automated redaction is imperfect, and uploaded verdicts remain customer-generated claims.

Run the credential-free auth fixtures, request boundary tests and actual PostgreSQL tenant tests:

```sh
PYTHONPATH=apps/api:packages/breakroom-core/src venv-team/bin/python -m unittest discover -s tests/team -v
```

The PostgreSQL tests create unique `test_team_<random>` schemas and remove only their own schemas. `BREAKROOM_TEAM_TEST_DATABASE_URL` can select a dedicated test database. A missing database fails these tests; it does not silently skip them or substitute another database.

Dependency verification (2026-09-07): [SQLAlchemy 2.0 session/transaction documentation](https://docs.sqlalchemy.org/en/20/orm/session_basics.html), [Authlib authorization-code/PKCE documentation](https://docs.authlib.org/en/v1.7.0/oauth2/client/web/index.html), [Authlib OIDC claims](https://docs.authlib.org/en/v1.7.0/oauth2/specs/oidc.html), [joserfc JWT verification](https://jose.authlib.org/en/guide/jwt/) and installed Authlib 1.8.0 source. The pinned HTTPX 0.28 integration currently emits upstream deprecation warnings; the fixture and application tests cover this pinned combination.
