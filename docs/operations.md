# Operations and recovery

This repository supplies a local application and tested recovery primitives. It does not provision a public service, managed database, cloud backup destination, monitoring account, identity provider or merchant account. The owner must configure and operate those systems before making availability or data-retention promises.

## Local startup and shutdown

From the repository root, start only the synthetic demo with `docker compose -f infra/compose.yaml up -d --build --wait`. To include private team reports and PostgreSQL:

```sh
docker compose -f infra/compose.yaml -f infra/compose.team.yaml config --quiet
docker compose -f infra/compose.yaml -f infra/compose.team.yaml up -d --build --wait
docker compose -f infra/compose.yaml -f infra/compose.team.yaml ps
curl --fail --silent --show-error http://127.0.0.1:3000/api/team/health
```

The website listens on loopback port 3000; PostgreSQL is exposed only on loopback port 54329 for local development/testing. Neither API has a host port. `/api/team` uses the website proxy to internal port 8001. Use the exact `http://127.0.0.1:3000` origin for the team browser journey. The overlay explicitly enables development login and has development-only database/proxy credentials. Do not expose it publicly or reuse those credentials elsewhere.

```sh
docker compose -f infra/compose.yaml -f infra/compose.team.yaml down
```

Routine shutdown retains the named PostgreSQL volume. Do not use `down --volumes` for a stop or upgrade. Anonymous demo reports are intentionally ephemeral and disappear when the demo API restarts; private team data persists in PostgreSQL. Docker persistence alone does not provide recovery from volume loss.

For the host-side restoration smoke, use Python 3.12–3.14 and the pinned team requirements:

```sh
python3 -m venv venv-team
venv-team/bin/python -m pip install -r apps/api/requirements-billing.lock
venv-team/bin/python -m unittest discover -s tests/operations -v
venv-team/bin/python scripts/backup-restore-smoke.py
```

The script imports the checked-out engine/API source directly. It requires the existing local Compose PostgreSQL service on 54329 and the local `breakroom` role. It rejects remote database targets and all URL query options that could override connection settings. `BREAKROOM_BACKUP_TEST_DATABASE_URL` may supply a changed local password through the environment; it is never printed or passed to subprocess arguments. Before migration, the script checks that `current_database()` equals its own generated source database. Docker's matching PostgreSQL 18.6 tools run inside the existing database container.

## What the restore smoke proves

The script creates unpredictable `br_backup_smoke_<uuid>_source` and `_restore` databases and marks ownership before using them. It seeds two synthetic users, owner/viewer memberships, a private project, scoped-key hash, session/login-attempt data, two actual minimized FAIL/PASS reports, private suite metadata and synthetic billing account/operation/inbox rows. The billing rows test database fidelity only; they do not represent a provider-verified webhook or real subscription. It invokes `pg_dump --format=custom`, hashes the archive, and restores into the empty second database using `pg_restore --exit-on-error --single-transaction --no-owner --no-acl`.

It compares canonical bytes and hashes for every table, validates both core report envelopes/checksums, compares restored constraints, attempts an orphan insertion and invalid values, and confirms that the report immutability trigger rejects a content change. PostgreSQL may rewrite a text-array cast into equivalent per-element casts; the checker recognizes only those exact finite-enum forms and preserves every column/value. Other constraint definitions must match. Finally, it replays a synthetic project deletion made after the backup and verifies cascading removal of private data. The result records schema versions, tool versions, byte size, hashes, checks and cleanup. Only databases created and marked by that invocation can be dropped; the script uses no forced drop, existing application DB, broad deletion pattern or volume removal.

The archive is temporary and deleted after the test. `artifacts/operations/backup-restore.json` contains non-secret evidence, not a recoverable application backup. This is a same-major-version, small-fixture logical restore test. It does not measure recovery time or capacity for a production-sized database, test cross-region recovery, or establish a point-in-time recovery service.

## Operator backup policy

An owner-configurable starting policy is one encrypted daily backup for seven days plus four weekly copies, with no copy older than 28 days. Keep access restricted, maintain an integrity inventory, and test a restore at least monthly and after schema/database upgrades. Treat a 24-hour recovery-point objective and a four-hour recovery-time objective as planning targets until real data-size recovery drills measure them. These schedules, retention jobs and targets are proposals; none is enabled by this repository.

Choose storage and key management outside the application volume. Restrict backup access because copies include identity emails, private evidence, API-key hashes, sessions, suite metadata and billing records. Do not upload archives to a ticket, public artifact store or source repository. Record the backup start/end time, schema/application versions, checksum, encryption-key reference and expiration without logging payloads or credential values. Investigate a missing backup, nonzero tool exit, warning or failed restore check; do not mark a backup healthy merely because a file exists.

PostgreSQL `pg_dump` produces a consistent single-database export and the custom format is read by `pg_restore`; it does not include cluster roles or tablespaces. Keep the deployment's reviewed role/extension configuration separately. For larger production installations or a tighter recovery point, select and rehearse PostgreSQL's physical backup/WAL recovery approach with the operator rather than claiming that this logical smoke supplies it. See the official [pg_dump](https://www.postgresql.org/docs/18/app-pgdump.html), [pg_restore](https://www.postgresql.org/docs/18/app-pgrestore.html) and [backup methods](https://www.postgresql.org/docs/18/backup.html) documentation, checked 2026-09-07.

This manual example backs up the **local** application database. It is documented for an owner to run deliberately; the smoke script never invokes it against that database:

```sh
(
  set -eu
  set -C
  umask 077
  BREAKROOM_BACKUP_DIR="$HOME/private-breakroom-backups"
  mkdir -p "$BREAKROOM_BACKUP_DIR"
  BREAKROOM_BACKUP_FILE="$BREAKROOM_BACKUP_DIR/breakroom-$(python3 -c 'import uuid; print(uuid.uuid4().hex)').dump"
  docker compose -f infra/compose.yaml -f infra/compose.team.yaml exec -T postgres \
    pg_dump --username=breakroom --format=custom --no-acl --dbname=breakroom > "$BREAKROOM_BACKUP_FILE"
  shasum -a 256 "$BREAKROOM_BACKUP_FILE" > "$BREAKROOM_BACKUP_FILE.sha256"
  printf 'Backup and checksum created: %s\n' "$BREAKROOM_BACKUP_FILE"
)
```

Check both command exits before retaining the backup; a failed shell redirection can leave a partial file. The file above is plaintext storage despite its compressed archive format. Apply your configured encryption/access controls before transporting or retaining it. A checksum detects accidental changes against a trusted inventory; it does not certify the source or protect against an attacker replacing both files. The sample omits any cloud destination and does not install a cron job.

## Restore without resurrecting deleted access or data

Restore a trusted operator-generated backup into a new, empty database on an isolated recovery instance. Never point `pg_restore --clean` at the serving database. A PostgreSQL archive contains executable database definitions; this operator recovery path must never accept a customer report upload or an untrusted archive. For a local rehearsal:

```sh
(
  set -eu
  BREAKROOM_BACKUP_FILE="/absolute/path/to/the/reviewed-backup.dump"
  BREAKROOM_RESTORE_DB="br_restore_review_$(python3 -c 'import uuid; print(uuid.uuid4().hex)')"
  python3 - "$BREAKROOM_BACKUP_FILE" <<'PY'
import hashlib, pathlib, re, sys
backup = pathlib.Path(sys.argv[1])
expected = pathlib.Path(str(backup) + ".sha256").read_text().split()[0]
with backup.open("rb") as source:
    actual = hashlib.file_digest(source, "sha256").hexdigest()
if not re.fullmatch(r"[0-9a-f]{64}", expected) or actual != expected:
    raise SystemExit("Selected backup checksum does not match the trusted inventory")
PY
  docker compose -f infra/compose.yaml -f infra/compose.team.yaml exec -T postgres \
    createdb --username=breakroom --template=template0 "$BREAKROOM_RESTORE_DB"
  printf 'New isolated restore target: %s\n' "$BREAKROOM_RESTORE_DB"
  docker compose -f infra/compose.yaml -f infra/compose.team.yaml exec -T postgres \
    pg_restore --username=breakroom --exit-on-error --single-transaction --no-owner --no-acl \
    --dbname="$BREAKROOM_RESTORE_DB" < "$BREAKROOM_BACKUP_FILE"
)
```

Use the recorded exact database name throughout validation. Require successful restore exit, expected schema versions, expected tables/counts, report checksums, foreign keys, immutability and tenant authorization tests before cutover. Reapply the reviewed target-role grants because `--no-owner --no-acl` deliberately does not restore them. Run `ANALYZE` on the recovered database before evaluating realistic performance. Keep the original database/volume intact while the owner reviews recovery; this guide does not automatically switch traffic or delete the original.

A backup predates later deletions and revocations. Maintain a restricted deletion/access-change ledger outside the database snapshot, containing the necessary opaque project/member/key identifiers and completion timestamps, not report content. The application does not create that external ledger automatically. The operator must record and replay project deletions, member removals/demotions, key revocations and shortened retention policies made after the chosen backup before the recovered service handles requests. The smoke demonstrates this requirement with an actual post-backup project deletion.

In the recovered database, invalidate all restored sessions and unfinished login attempts and revoke restored API keys before issuing replacement credentials. Apply deletion-ledger operations using parameterized statements in reviewed transactions; project deletion cascades its report, key, membership and suite rows. Run expiry cleanup, and confirm the recovered read APIs cannot expose expired/deleted records. Review restored billing operations against the provider before enabling that worker; a replayed local state must not silently repeat an external action.

Backups remain capable of containing deleted data until their disclosed expiration. The suggested 28-day maximum must cover secondary copies and snapshots too. Restrict those copies, expire them under the configured policy, and replay the ledger on every restore. Do not claim immediate erasure from all backups when the service only deleted current database records.

## Migrations and release procedure

Review the exact schema change and pinned application image before startup. `TeamStore.migrate()` takes a PostgreSQL advisory transaction lock and records supported schema versions. Migration 1 creates the reporting/auth schema and report immutability trigger; migration 2 adds billing tables. An unknown version fails startup. Startup is a migration action, so first rehearse the new version against an isolated restored copy and rerun tenant, upload, billing and backup tests.

For an owner-managed release, pause incoming writes/workers, take and verify a backup, record the current schema/image, then start one new team API worker. Check migration completion, database readiness, background-worker health and authorization before restoring traffic. Current Compose rate limits are per process; do not add replicas or workers without a shared ingress limit and reviewed coordination. Preserve an old image for diagnosis, but do not assume it can read a newer schema. Roll forward or use the rehearsed isolated recovery procedure; restoring an older backup can lose writes and requires the deletion/access reconciliation above. No automatic rollback, public deployment or production migration is triggered by this document.

## Health, retries and private logs

`/api/team/health` checks the database with a bounded query and reports schema and retention-worker state. PostgreSQL connections have a five-second connect timeout and ten-second statement timeout. The retention sweep processes at most 1000 expired reports, 1000 sessions and 1000 login attempts per interval (300 seconds by default); a failure reports `retrying` and is attempted at a later interval. Read paths enforce report expiry even before cleanup. Investigate persistent `retrying`, a growing backlog, repeated DB errors or failed health checks; an HTTP 200 alone does not mean every worker is healthy. The owner can run the authenticated per-project retention cleanup for a backlog.

The team container uses one Uvicorn worker, concurrency limit 32, five-second keepalive, memory/process limits and disabled access logs. The public demo's runs are capped at five seconds and its evidence expires after 30 minutes. Uploads are at most 2 MiB and request-body reads have a ten-second deadline. The CLI's ten-second network timeout applies per blocking operation, not as a total wall deadline against a continuously streaming peer. Preserve honest unavailable/expired/rate-limited responses instead of retrying unboundedly.

Keep request bodies, report text, authorization/cookie headers, OIDC authorization codes, API keys, connection strings and provider secrets out of logs. Use counts, safe error categories, state transitions and opaque request/project references. Default API access logging is disabled to avoid callback query strings. Inspect bounded logs with `docker compose -f infra/compose.yaml -f infra/compose.team.yaml logs --tail=100 team-api`; never enable credential-bearing debug dumps to investigate a failure. Configure your hosting ingress and database logs with equivalent privacy limits and a disclosed retention period. None of those external log policies is configured here.

The health response also includes `billing.mode`, `billing.state`, aggregate `billing.queue` counts and `billing.worker`. `disabled` is expected when test billing is unconfigured. When enabled, the worker processes at most four inbox items per wake-up (two seconds by default), with 120-second leases and 30-second retry delays. An event gets at most five attempts before `failed`; exceptions leave the worker marked `retrying` while later work is attempted. A `ready` worker can still have failed inbox items, so inspect both fields. Investigate failed/stuck queues and use the authenticated owner's billing reconciliation action after checking provider state. Do not reset attempt counters, delete receipts, forge paid-through timestamps or create a new idempotency key to conceal an uncertain operation. This recovery test verifies billing rows survive a backup; the separate billing tests cover provider fixtures and worker behavior.

## Requirements before an external service

The owner still needs a reviewed deployment configuration, HTTPS origin/certificate, private database networking and credentials, encrypted backup storage, monitoring, recovery responsibility and a configured OIDC provider. Production must disable development login, reject development proxy credentials and enforce exact origins, Secure cookies and membership/scope checks. Test the chosen provider's real issuer/client/callback and account lifecycle; local OIDC fixtures are not a live identity-provider validation. Keep merchant onboarding, billing-provider credentials and webhook/network setup separate; billing tests do not establish merchant approval or authorize real charges. Consult [the billing guide](billing.md) for its supported modes and limitations.

## Recorded local verification

On 2026-09-07, after migration 2's billing schema froze, the smoke passed on PostgreSQL/pg_dump/pg_restore 18.6. The independent root rerun also passed: 12 restored tables, matching canonical row bytes and column types, two report checksums, 11 foreign-key definitions plus enforcement, CHECK and report-immutability rejection, post-backup deletion replay and cascades, and removal of the temporary archive and both owned databases. Its 36,321-byte archive and 1.288-second small-fixture duration are recorded in `artifacts/operations/backup-restore-final.json`; they are not production capacity or recovery-time promises. All three read-only database-target guard tests passed. The earlier successful script run remains in `artifacts/operations/backup-restore.json`.
