# Breakroom built-in demo API

This API executes two scripted reference agents against five allowlisted synthetic Fire Drills. Every run calls the same Python runner, SQLite world and independent evaluator used by the CLI. This is the anonymous demo portion of Cut A. Hosted authentication, customer report imports, team storage and billing are not implemented here.

From the repository root, with Python 3.12:

```sh
python3.12 -m venv venv
venv/bin/python -m pip install -r apps/api/requirements.lock
venv/bin/python -m pip install -e ./packages/breakroom-core
venv/bin/python -m uvicorn breakroom_api.main:app --app-dir apps/api --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers --limit-concurrency 32 --timeout-keep-alive 5
```

Run tests from the repository root:

```sh
PYTHONPATH=apps/api venv/bin/python -m pytest tests/api -q
```

The implementation session uses `.venv-api` for an independent API environment; the commands above intentionally allow one local environment for the full application.

| Endpoint | Contract |
|---|---|
| `GET /health` | API mode and report schema version |
| `GET /api/drills` | `{drills: [...]}` from 24 reviewed versioned manifests, each with `demo_available` |
| `GET /api/drills/{case_id}` | One reviewed manifest and its browser execution availability |
| `POST /api/runs` | Exactly `{agent: "faulty" \| "corrected", case_id: "refund-response-lost"}`; returns HTTP 201 and a newly executed report envelope |
| `GET /api/runs/{id}` | Retrieve a saved run envelope; does not re-execute it |
| `GET /api/runs/{id}/export` | ZIP containing the core's runnable case, regression test, README and requirements |
| `POST /api/compare` | Exactly `{baseline_id, candidate_id}`; compatible core comparison, or HTTP 409 with incompatibilities |

Run envelopes contain `id`, `created_at`, `expires_at`, `source: "live_builtin"`, `agent`, and the complete versioned core `report`. The ID is an opaque random capability. Evidence is synthetic and publicly accessible to anyone with that capability; this is not private team storage. There is no report-list endpoint and no account or authentication cookie. Reports expire after 30 minutes, are swept at least once per minute, and disappear on restart. Reading a saved envelope remains evidence replay, not a new execution.

Browser-executable case IDs: `normal-refund`, `refund-response-lost`, `refund-before-commit`, `ticket-write-failure`, `similar-customers`. The other 19 reviewed drills have library/details and local CLI commands. Adding files to a pack does not expand either server-owned allowlist.

The service admits one trial at a time, enforces a five-second worker deadline, rejects requests beyond 4 KiB (including chunked bodies), permits at most 12 run requests and 120 API requests per client per minute, limits reports to 1 MB and exports to 2 MB, and stores at most 200 reports. The rate-client map is also bounded. HTTP 429 indicates a rate limit; 503 means shared worker/storage capacity; 502 means invalid worker output; 404 means unknown or expired evidence; 422 rejects invalid inputs. An executed trial may itself have an error, timeout or failure verdict; its evidence still uses HTTP 201.

Run one Uvicorn process. Multiple processes would create separate capacity/rate limits and report stores. `--no-proxy-headers` deliberately ignores caller-controlled forwarding headers. Behind the included Next.js proxy the API's client bucket is shared, a conservative demo limit. A future public deployment needs owner-reviewed proxy identity and operational limits. CORS only permits the two explicit localhost port-3000 origins; the website uses a same-origin proxy.

There are no API fields for source, commands, imports, filesystem paths, custom manifests, provider configuration or network targets. Extra fields are rejected. Reference workers receive a minimal environment without inherited payment/provider secrets. Ordinary subprocess execution is not a security sandbox; only audited local built-ins are admitted here. The local customer adapter remains executable trusted code on its owner's machine. No production credentials or services are needed.

Dependency APIs were checked on 2026-09-07 against the official [FastAPI release notes](https://fastapi.tiangolo.com/release-notes/), [FastAPI testing guide](https://fastapi.tiangolo.com/tutorial/testing/), [Pydantic strict-validation documentation](https://pydantic.dev/docs/validation/latest/concepts/strict_mode/), and [Uvicorn 0.41.0 release](https://github.com/Kludex/uvicorn/releases/tag/0.41.0). Tested versions and transitive dependency pins are recorded in `requirements.lock`; `requirements.in` records direct choices.
