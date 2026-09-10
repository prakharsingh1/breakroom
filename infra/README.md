# Local containers

Run these commands from the repository root with Docker Desktop/Engine and Compose v2 available. Builds fetch the pinned images and dependency locks. The core Python CLI remains usable without Docker, an account or an API key.

## Synthetic public demo

```sh
docker compose -f infra/compose.yaml config --quiet
docker compose -f infra/compose.yaml up -d --build --wait
```

Open `http://127.0.0.1:3000`. This configuration starts the website and fixed built-in demo API. It requires no database or model/payment credentials. Anonymous reports live in API memory for 30 minutes and disappear on restart. Only five allowlisted scenarios and two scripted agents execute through the public demo.

```sh
docker compose -f infra/compose.yaml down
```

## Explicit local team development

```sh
docker compose -f infra/compose.yaml -f infra/compose.team.yaml config --quiet
docker compose -f infra/compose.yaml -f infra/compose.team.yaml up -d --build --wait
docker compose -f infra/compose.yaml -f infra/compose.team.yaml ps
```

Open `http://127.0.0.1:3000/projects`, using that exact hostname. The overlay deliberately enables development identity on loopback and includes a development-only database password and internal proxy secret. It is a local test configuration, not a production deployment template.

| Surface | Address and persistence |
| --- | --- |
| Website, public demo and team proxy | Host loopback `127.0.0.1:3000`; `/api/team` forwards to the team API |
| PostgreSQL 18.6 | Host loopback `127.0.0.1:54329`; data in the `team-postgres` named Compose volume |
| Demo API | Internal port 8000; no host binding; anonymous in-memory evidence |
| Team API | Internal port 8001; no host binding; private report metadata/evidence in PostgreSQL |

The Next server adds the internal local-proxy credential; the browser never receives it. `127.0.0.1` and `localhost` are different origins for cookie/CSRF checks. Port 8001 in the [manual Python service guide](../apps/api/README-team.md) applies only when an operator separately starts that host process.

Health and smoke checks:

```sh
curl --fail --silent --show-error http://127.0.0.1:3000/api/team/health
venv/bin/python tests/api/smoke_team_upload_http.py
venv-team/bin/python scripts/backup-restore-smoke.py
```

The HTTP smoke prepares and explicitly uploads synthetic reference reports, verifies comparison and key revocation, then deletes its own project. The backup smoke creates two unique disposable databases, migrates/seeds only those databases, runs PostgreSQL's real dump and restore utilities, verifies restored data and constraints, then removes those databases and the temporary archive. It never backs up or replaces your application database. Python setup and exact recovery procedures are in [operations.md](../docs/operations.md).

Stop the overlay with the same two files:

```sh
docker compose -f infra/compose.yaml -f infra/compose.team.yaml down
```

This removes the containers and networks while retaining the PostgreSQL named volume. Do not add `--volumes` to a routine stop; removing the volume deletes local team history. A Docker volume is persistence, not a backup. Restart with the same overlay command to reuse it.

## Runtime scope

The API containers have read-only root filesystems, bounded temporary filesystems, dropped capabilities, process/memory limits and internal Docker networks. The team API runs one worker with bounded concurrency; the demo API also has one bounded worker. These are deployment constraints, not a security sandbox for arbitrary Python code. Both hosted APIs reject uploaded code and source archives; customer adapters execute on customer-owned machines.

Rate limits and demo evidence are per process. Do not increase workers or replicas without first implementing a shared ingress/rate policy and reviewing storage/worker coordination. Service health, retries, migrations, logging, backup retention and production prerequisites are documented in [operations.md](../docs/operations.md). Billing remains disabled unless its separate supported test configuration is explicitly selected; consult [the billing guide](../docs/billing.md).

Local Docker build, browser, team isolation and restoration results are tracked in root `STATUS.md` and `artifacts/operations/`. No cloud backup, public service, live identity provider or merchant account is provisioned by these files.
