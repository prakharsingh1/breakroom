#!/usr/bin/env python3
"""Test PostgreSQL backup/restore using only databases created by this invocation.

Needs the local Compose PostgreSQL service and requirements-billing.lock. This never
dumps, restores over, drops, or seeds the user's breakroom database. Archives and
synthetic credentials are temporary; only bounded non-secret results are saved.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "apps/api"), str(ROOT / "packages/breakroom-core/src")]

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from sqlalchemy import insert, text
from sqlalchemy.engine import URL, make_url

from breakroom.agents import corrected, faulty
from breakroom.evaluator import run_in_process
from breakroom.scenarios import load_case
from breakroom.uploads import prepare_upload, upload_digest, validate_upload_envelope
from breakroom_api.team_config import TeamSettings
from breakroom_api.team_db import (TeamStore, api_keys, login_attempts, memberships,
                                  projects, reports, sessions, suites, users, invitations)


def canonical(value):
    def scalar(item):
        if isinstance(item, datetime):
            return item.isoformat()
        raise TypeError("Unsupported fixture value")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False, default=scalar).encode("utf-8")


def local_database_location(value):
    location = make_url(value)
    if (location.drivername != "postgresql+psycopg" or location.host not in {"127.0.0.1", "localhost", "::1"}
            or location.port != 54329 or location.username != "breakroom" or location.query):
        raise ValueError("This smoke requires a query-free URL for the local Compose breakroom role on loopback port 54329")
    # Rebuild from validated components. Driver query arguments (notably dbname)
    # must never override a disposable target after URL.set(database=...).
    return URL.create("postgresql+psycopg", username="breakroom", password=location.password,
                      host=location.host, port=54329, database=location.database)


def snapshot(connection):
    names = [row["tablename"] for row in connection.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")]
    if not names or any(not re.fullmatch(r"team_[a-z_]+", name) for name in names):
        raise ValueError("Unexpected disposable database tables")
    content = {}
    for name in names:
        rows = list(connection.execute(sql.SQL("SELECT * FROM {}").format(sql.Identifier(name))))
        content[name] = sorted(rows, key=canonical)
    return content


def column_schema(connection):
    return list(connection.execute("""SELECT table_name, column_name, data_type, is_nullable,
        character_maximum_length, numeric_precision, numeric_scale FROM information_schema.columns
        WHERE table_schema='public' ORDER BY table_name, ordinal_position"""))


def constraints(connection):
    rows = list(connection.execute("""SELECT c.relname AS table_name, con.conname AS name,
        con.contype AS type, pg_get_constraintdef(con.oid) AS definition
        FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'
        ORDER BY c.relname, con.conname"""))
    for row in rows:
        # pg_restore reparses a varchar[] -> text[] cast as one text cast per
        # literal. Recognize only these two exact finite-enum forms. Preserve
        # every value/column and leave any other constraint SQL unchanged.
        match = re.fullmatch(r"CHECK \(\(\(([a-z_]+)\)::text = ANY \((.+)\)\)\)", row["definition"])
        if row["type"] != "c" or not match:
            continue
        column, expression = match.groups()
        whole = re.fullmatch(r"\(ARRAY\[(.+)\]\)::text\[\]", expression)
        elements = re.fullmatch(r"ARRAY\[(.+)\]", expression)
        if not (whole or elements):
            continue
        pattern = r"'([A-Za-z_]+)'::character varying" if whole else r"\('([A-Za-z_]+)'::character varying\)::text"
        values = [re.fullmatch(pattern, item) for item in (whole or elements).group(1).split(", ")]
        if values and all(values):
            row["definition"] = {"finite_text_enum_column": column, "values": [item.group(1) for item in values]}
    return rows


def seed(store):
    from breakroom_api.billing_db import accounts, operations, events
    from breakroom_api.password_auth import hash_password, verify_password
    from breakroom_api.password_db import PASSWORD_ISSUER, credentials, account_tokens, auth_limits
    now = datetime.now(timezone.utc)
    owner, viewer, project = (str(uuid.uuid4()) for _ in range(3))
    # These values exist only in this process. The database/archive retains
    # hashes; the printed result never includes a password, token or mail body.
    password = secrets.token_urlsafe(32)
    password_hash = hash_password(password)
    if not verify_password(password_hash, password) or verify_password(password_hash, password + "wrong"):
        raise ValueError("Synthetic Argon2 credential verification failed")
    invite_token, account_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    redaction_key = secrets.token_bytes(32)
    prepared = [prepare_upload(run_in_process(load_case("refund-response-lost"), agent), redaction_key=redaction_key)
                for agent in (faulty, corrected)]
    if [item["report"]["verdict"] for item in prepared] != ["FAIL", "PASS"]:
        raise ValueError("Synthetic controls did not produce expected actual outcomes")
    with store.engine.begin() as con:
        con.execute(insert(users), [
            {"id": owner, "issuer": PASSWORD_ISSUER, "subject": owner,
             "email": "owner@example.invalid", "display_name": "Synthetic backup owner", "created_at": now},
            {"id": viewer, "issuer": "breakroom:backup-smoke", "subject": viewer,
             "email": "viewer@example.invalid", "display_name": "Synthetic backup viewer", "created_at": now}])
        con.execute(insert(projects).values(id=project, name="Synthetic private restore drill", retention_days=7, created_at=now))
        con.execute(insert(memberships), [{"project_id": project, "user_id": owner, "role": "owner"},
                                        {"project_id": project, "user_id": viewer, "role": "viewer"}])
        con.execute(insert(api_keys).values(id=str(uuid.uuid4()), project_id=project, user_id=owner,
            name="Synthetic scoped restore key", prefix="brk_synthetic", token_hash=hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
            scopes=["reports:read", "reports:write"], created_at=now, expires_at=now + timedelta(days=7), revoked_at=None))
        con.execute(insert(sessions).values(token_hash=hashlib.sha256(secrets.token_bytes(32)).hexdigest(), user_id=owner,
            csrf_token=secrets.token_urlsafe(32), expires_at=now + timedelta(hours=1)))
        con.execute(insert(login_attempts).values(state_hash=hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
            nonce=secrets.token_urlsafe(32), code_verifier=secrets.token_urlsafe(32), expires_at=now + timedelta(minutes=5)))
        con.execute(insert(invitations).values(id=str(uuid.uuid4()), project_id=project, invited_by=owner,
            email="recipient@example.invalid", role="developer", token_hash=hashlib.sha256(invite_token.encode()).hexdigest(),
            created_at=now, expires_at=now + timedelta(days=2), revoked_at=None, accepted_at=None))
        con.execute(insert(credentials).values(user_id=owner, password_hash=password_hash, verified_at=now,
            changed_at=now))
        con.execute(insert(account_tokens).values(token_hash=hashlib.sha256(account_token.encode()).hexdigest(),
            user_id=owner, purpose="reset", expires_at=now + timedelta(minutes=30)))
        con.execute(insert(auth_limits).values(bucket_hash=hashlib.sha256(b"synthetic-restoration-auth-bucket").hexdigest(),
            attempts=3, expires_at=now + timedelta(minutes=15)))
        for index, upload in enumerate(prepared):
            digest = upload_digest(upload)
            con.execute(insert(reports).values(id=str(uuid.uuid4()), project_id=project, submitted_by=owner,
                idempotency_key=digest, payload_hash=digest, case_id=upload["report"]["case"]["case_id"],
                verdict=upload["report"]["verdict"], upload=upload, created_at=now - timedelta(days=index * 2),
                expires_at=now + timedelta(days=7 if index == 0 else -1)))
        con.execute(insert(suites).values(id=str(uuid.uuid4()), project_id=project, name="Synthetic private suite",
            cases=[{key: prepared[0]["report"]["case"][key] for key in ("case_id", "case_version", "manifest_hash")}],
            created_at=now, updated_at=now))
        # Database fidelity fixtures only: no provider request, signed webhook
        # claim, merchant account or external charge is involved in this drill.
        con.execute(insert(accounts).values(project_id=project, customer_id="cus_synthetic_backup", subscription_id="sub_synthetic_backup",
            status="active", sync_state="ready", cancel_at_period_end=False, current_period_end=now + timedelta(days=7),
            paid_through=now + timedelta(days=7), updated_at=now))
        operation_hash = hashlib.sha256(canonical({"fixture": "synthetic_unknown_operation"})).hexdigest()
        con.execute(insert(operations).values(id=str(uuid.uuid4()), project_id=project, kind="checkout", idempotency_key=operation_hash,
            request_hash=operation_hash, status="unknown", result={"synthetic": True, "reconciliation_required": True}, created_at=now, updated_at=now))
        con.execute(insert(events).values(event_id="evt_synthetic_backup", project_id=project,
            payload_hash=hashlib.sha256(canonical({"fixture": "synthetic_inbox_record"})).hexdigest(), event_type="customer.subscription.updated",
            target_id="sub_synthetic_backup", target_kind="subscription", provider_created=int(now.timestamp()), status="retrying",
            attempts=2, lease_token=None, lease_until=None, retry_at=now + timedelta(seconds=30), created_at=now, processed_at=None))
    return {"owner": owner, "viewer": viewer, "project": project, "password": password,
            "invite_token": invite_token, "account_token": account_token}


def run(out):
    result = {"test": "postgresql_backup_restore", "synthetic_only": True,
              "started_at": datetime.now(timezone.utc).isoformat(), "success": False}
    started = time.monotonic()
    stage = "configuration"
    created, stores, cleanup = [], [], {}
    admin = None
    marker = uuid.uuid4().hex
    names = [f"br_backup_smoke_{marker}_source", f"br_backup_smoke_{marker}_restore"]
    compose = ["docker", "compose", "-f", str(ROOT / "infra/compose.yaml"), "-f", str(ROOT / "infra/compose.team.yaml")]
    settings = TeamSettings(database_url=os.environ.get("BREAKROOM_BACKUP_TEST_DATABASE_URL", TeamSettings.database_url),
                            environment="test", public_origin="http://127.0.0.1:3000")
    location = local_database_location(settings.database_url)
    connect_options = {"host": location.host, "port": location.port, "user": location.username,
                       "password": location.password, "connect_timeout": 5,
                       "options": "-cstatement_timeout=10000", "autocommit": True, "row_factory": dict_row}

    def docker_pg(program, arguments, **kwargs):
        command = compose + ["exec", "-T", "postgres", program] + arguments
        completed = subprocess.run(command, stderr=subprocess.PIPE, timeout=45, **kwargs)
        if completed.returncode != 0 or completed.stderr:
            # Never echo connection strings, SQL values, archives or secrets.
            raise RuntimeError(f"{program} failed or emitted a warning; inspect local configuration")
        return completed

    try:
        stage = "create_disposable_databases"
        admin = psycopg.connect(dbname="postgres", **connect_options)
        result["postgres_version"] = admin.execute("SHOW server_version").fetchone()["server_version"]
        for name in names:
            if not re.fullmatch(r"br_backup_smoke_[0-9a-f]{32}_(source|restore)", name):
                raise ValueError("Invalid generated database name")
            admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name)))
            created.append(name)
            admin.execute(sql.SQL("COMMENT ON DATABASE {} IS {}").format(sql.Identifier(name), sql.Literal("breakroom disposable backup smoke " + marker)))
        result["temporary_databases"] = names
        stage = "migrate_and_seed"
        source = TeamStore(replace(settings, database_url=location.set(database=names[0]).render_as_string(hide_password=False)))
        stores.append(source)
        with source.engine.connect() as connection:
            if connection.execute(text("SELECT current_database()")).scalar_one() != names[0]:
                raise ValueError("Migration connection is not the owned disposable database")
        source.migrate()
        fixture = seed(source)
        with psycopg.connect(dbname=names[0], **connect_options) as connection:
            before = snapshot(connection)
            original_constraints = constraints(connection)
            original_columns = column_schema(connection)
        result["schema_versions"] = {name: [row["version"] for row in rows] for name, rows in before.items()
                                     if name.endswith("schema_version")}
        result["row_counts"] = {name: len(rows) for name, rows in before.items()}
        result["table_sha256"] = {name: hashlib.sha256(canonical(rows)).hexdigest() for name, rows in before.items()}
        with tempfile.TemporaryDirectory(prefix="breakroom-backup-smoke-") as folder:
            archive = Path(folder) / "synthetic.dump"
            stage = "pg_dump"
            result["pg_dump_version"] = docker_pg("pg_dump", ["--version"], stdout=subprocess.PIPE).stdout.decode().strip()
            with archive.open("xb") as destination:
                os.chmod(archive, 0o600)
                docker_pg("pg_dump", ["--username=breakroom", "--format=custom", "--no-acl", "--dbname=" + names[0]], stdout=destination)
            result["archive_bytes"] = archive.stat().st_size
            result["archive_sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
            if not 1 <= result["archive_bytes"] <= 16 * 1024 * 1024:
                raise ValueError("Synthetic backup size is outside the smoke limit")
            # Exercise a deletion after the snapshot. Restoring the backup must
            # be followed by this deletion before serving traffic again.
            with psycopg.connect(dbname=names[0], **connect_options) as connection:
                connection.execute("DELETE FROM team_projects WHERE id=%s", (fixture["project"],))
                deleted_source = snapshot(connection)
            stage = "pg_restore"
            result["pg_restore_version"] = docker_pg("pg_restore", ["--version"], stdout=subprocess.PIPE).stdout.decode().strip()
            with archive.open("rb") as source_archive:
                docker_pg("pg_restore", ["--username=breakroom", "--exit-on-error", "--single-transaction", "--no-owner", "--no-acl",
                                        "--dbname=" + names[1]], stdin=source_archive, stdout=subprocess.PIPE)
            stage = "restore_validation"
            with psycopg.connect(dbname=names[1], **connect_options) as connection:
                restored = snapshot(connection)
                result["canonical_rows_equal"] = canonical(restored) == canonical(before)
                restored_constraints = constraints(connection)
                result["constraints_equal"] = restored_constraints == original_constraints
                result["column_types_equal"] = column_schema(connection) == original_columns
                if not result["canonical_rows_equal"] or not result["constraints_equal"] or not result["column_types_equal"]:
                    result["different_tables"] = [name for name in set(before) | set(restored)
                                                  if canonical(before.get(name)) != canonical(restored.get(name))]
                    result["different_constraint_names"] = sorted({row["name"] for row in original_constraints if row not in restored_constraints}
                                                                  | {row["name"] for row in restored_constraints if row not in original_constraints})
                    result["different_constraint_definitions"] = {
                        "source": [row for row in original_constraints if row not in restored_constraints],
                        "restored": [row for row in restored_constraints if row not in original_constraints]}
                    raise ValueError("Restored row bytes or schema constraints differ")
                result["foreign_key_count"] = sum(row["type"] == "f" for row in original_constraints)
                for row in restored["team_reports"]:
                    validate_upload_envelope(row["upload"])
                    if upload_digest(row["upload"]) != row["payload_hash"]:
                        raise ValueError("Restored report payload checksum differs")
                result["report_hashes_verified"] = len(restored["team_reports"])
                from breakroom_api.password_auth import verify_password
                credential = restored["team_password_credentials"][0]
                if (not credential["password_hash"].startswith("$argon2id$")
                        or not verify_password(credential["password_hash"], fixture["password"])
                        or verify_password(credential["password_hash"], fixture["password"] + "wrong")):
                    raise ValueError("Restored Argon2 credential no longer verifies correctly")
                for table, token in (("team_invitations", fixture["invite_token"]),
                                     ("team_account_tokens", fixture["account_token"])):
                    if restored[table][0]["token_hash"] != hashlib.sha256(token.encode()).hexdigest():
                        raise ValueError("Restored account or invitation token hash differs")
                if any(secret.encode() in canonical(restored) for secret in (
                        fixture["password"], fixture["invite_token"], fixture["account_token"])):
                    raise ValueError("A plaintext synthetic credential was stored")
                result["password_hash_verified"] = True
                result["account_and_invitation_hashes_verified"] = 2
                result["plaintext_credentials_absent"] = True
                for statement, parameters in (
                    ("UPDATE team_memberships SET role=%s WHERE project_id=%s", ("invalid_role", fixture["project"])),
                    ("UPDATE team_projects SET retention_days=%s WHERE id=%s", (0, fixture["project"])),
                    ("UPDATE team_invitations SET role=%s WHERE project_id=%s", ("owner", fixture["project"])),
                    ("UPDATE team_account_tokens SET purpose=%s WHERE user_id=%s", ("invalid_purpose", fixture["owner"])),
                    ("UPDATE team_auth_limits SET attempts=%s", (0,))):
                    try:
                        connection.execute(statement, parameters)
                    except psycopg.errors.CheckViolation:
                        pass
                    else:
                        raise ValueError("Restored check constraint accepted an invalid value")
                result["check_constraints_enforced"] = True
                result["account_and_invitation_constraints_enforced"] = True
                try:
                    connection.execute("INSERT INTO team_memberships(project_id,user_id,role) VALUES (%s,%s,'viewer')",
                                       (fixture["project"], str(uuid.uuid4())))
                except psycopg.errors.ForeignKeyViolation:
                    result["foreign_key_enforced"] = True
                else:
                    raise ValueError("Restored foreign key did not reject an orphan")
                try:
                    connection.execute("UPDATE team_reports SET payload_hash=%s WHERE id=%s", ("0" * 64, restored["team_reports"][0]["id"]))
                except psycopg.errors.RaiseException:
                    result["report_immutability_enforced"] = True
                else:
                    raise ValueError("Restored report immutability trigger did not reject a mutation")
                if canonical(snapshot(connection)) != canonical(before):
                    raise ValueError("Rejected writes changed restored data")
                # Replay a synthetic post-backup deletion without resurrecting
                # its reports, keys, private suite or memberships.
                connection.execute("DELETE FROM team_projects WHERE id=%s", (fixture["project"],))
                after_deletion = snapshot(connection)
                if canonical(after_deletion) != canonical(deleted_source):
                    raise ValueError("Post-backup deletion reconciliation differs")
                for table in ("team_projects", "team_reports", "team_memberships", "team_api_keys", "team_suites",
                              "team_billing_accounts", "team_billing_operations", "team_billing_events", "team_invitations"):
                    if after_deletion[table]:
                        raise ValueError("Deleted project data remains after restore reconciliation")
                result["post_backup_deletion_replayed"] = True
                result["project_cascades_verified"] = True
                # Account deletion follows project deletion: the report author
                # RESTRICT foreign key must never be silently bypassed.
                connection.execute("DELETE FROM team_users WHERE id=%s", (fixture["owner"],))
                after_account_deletion = snapshot(connection)
                for table in ("team_password_credentials", "team_account_tokens", "team_sessions"):
                    if after_account_deletion[table]:
                        raise ValueError("Deleted account credentials remain after restore reconciliation")
                if len(after_account_deletion["team_users"]) != 1 or after_account_deletion["team_users"][0]["id"] != fixture["viewer"]:
                    raise ValueError("Account deletion affected an unrelated synthetic user")
                # Abuse buckets are anonymous hashes without a user foreign
                # key. Restore must retain them until their ordinary expiry.
                if after_account_deletion["team_auth_limits"] != before["team_auth_limits"]:
                    raise ValueError("Account deletion unexpectedly removed an abuse-limit bucket")
                result["account_cascades_verified"] = True
        result["archive_discarded"] = not archive.exists()
        result["success"] = True
    except Exception as error:
        result["failure_stage"] = stage
        result["error_type"] = type(error).__name__
    finally:
        for store in stores:
            store.engine.dispose()
        if admin is not None:
            for name in reversed(created):
                try:
                    row = admin.execute("SELECT shobj_description(oid,'pg_database') AS marker FROM pg_database WHERE datname=%s", (name,)).fetchone()
                    if row is None or row["marker"] != "breakroom disposable backup smoke " + marker:
                        raise ValueError("Disposable database ownership marker changed")
                    # No FORCE, broad patterns, production DB names or volumes.
                    admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
                    cleanup[name] = "removed"
                except Exception as error:
                    cleanup[name] = "not_removed:" + type(error).__name__
                    result["success"] = False
            admin.close()
        result["cleanup"] = cleanup
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, sort_keys=True))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "artifacts/operations/backup-restore.json")
    arguments = parser.parse_args()
    try:
        raise SystemExit(run(arguments.out))
    except (ValueError, OSError) as error:
        print("Backup smoke configuration error: " + type(error).__name__, file=sys.stderr)
        raise SystemExit(2)
