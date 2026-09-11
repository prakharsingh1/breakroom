"""PostgreSQL-owned team records and versioned migration 1.

Every tenant-owned child uses a database foreign key with cascading deletion.
Opaque authentication and API-key tokens are stored only as SHA-256 hashes.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, CheckConstraint, Column, DateTime, ForeignKey,
    Integer, MetaData, String, Table, UniqueConstraint, create_engine, delete,
    insert, select, text, update)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.schema import CreateSchema

SCHEMA_VERSION = 5
metadata = MetaData()
versions = Table("team_schema_version", metadata, Column("version", Integer, primary_key=True), Column("applied_at", DateTime(timezone=True), nullable=False))
users = Table("team_users", metadata,
    Column("id", String(36), primary_key=True), Column("issuer", String(512), nullable=False),
    Column("subject", String(256), nullable=False), Column("email", String(320), nullable=False, unique=True),
    Column("display_name", String(128), nullable=False), Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("issuer", "subject", name="uq_team_identity"))
sessions = Table("team_sessions", metadata,
    Column("token_hash", String(64), primary_key=True), Column("user_id", ForeignKey("team_users.id", ondelete="CASCADE"), nullable=False),
    Column("csrf_token", String(128), nullable=False), Column("expires_at", DateTime(timezone=True), nullable=False, index=True))
login_attempts = Table("team_login_attempts", metadata,
    Column("state_hash", String(64), primary_key=True), Column("nonce", String(128), nullable=False),
    Column("code_verifier", String(128), nullable=False), Column("expires_at", DateTime(timezone=True), nullable=False, index=True))
projects = Table("team_projects", metadata,
    Column("id", String(36), primary_key=True), Column("name", String(120), nullable=False),
    Column("retention_days", Integer, nullable=False), Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("retention_days BETWEEN 1 AND 365", name="ck_team_retention"))
memberships = Table("team_memberships", metadata,
    Column("project_id", ForeignKey("team_projects.id", ondelete="CASCADE"), primary_key=True),
    Column("user_id", ForeignKey("team_users.id", ondelete="CASCADE"), primary_key=True),
    Column("role", String(16), nullable=False), CheckConstraint("role IN ('viewer','developer','owner')", name="ck_team_role"))
api_keys = Table("team_api_keys", metadata,
    Column("id", String(36), primary_key=True), Column("project_id", ForeignKey("team_projects.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("user_id", ForeignKey("team_users.id", ondelete="CASCADE"), nullable=False),
    Column("name", String(120), nullable=False), Column("prefix", String(16), nullable=False),
    Column("token_hash", String(64), nullable=False, unique=True), Column("scopes", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False), Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True)))
reports = Table("team_reports", metadata,
    Column("id", String(36), primary_key=True), Column("project_id", ForeignKey("team_projects.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("submitted_by", ForeignKey("team_users.id", ondelete="RESTRICT"), nullable=False),
    Column("idempotency_key", String(64), nullable=False), Column("payload_hash", String(64), nullable=False),
    Column("case_id", String(80), nullable=False), Column("verdict", String(20), nullable=False),
    Column("upload", JSONB, nullable=False), Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False, index=True),
    UniqueConstraint("project_id", "idempotency_key", name="uq_team_report_idempotency"),
    CheckConstraint("verdict IN ('PASS','FAIL','INCONCLUSIVE','UNSUPPORTED')", name="ck_team_report_verdict"))
suites = Table("team_suites", metadata,
    Column("id", String(36), primary_key=True), Column("project_id", ForeignKey("team_projects.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("name", String(120), nullable=False), Column("cases", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False), Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("jsonb_typeof(cases)='array' AND jsonb_array_length(cases)<=128", name="ck_team_suite_case_count"))
invitations = Table("team_invitations", metadata,
    Column("id", String(36), primary_key=True),
    Column("project_id", ForeignKey("team_projects.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("invited_by", ForeignKey("team_users.id", ondelete="CASCADE"), nullable=False),
    Column("email", String(254), nullable=False), Column("role", String(16), nullable=False),
    Column("token_hash", String(64), nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False, index=True),
    Column("revoked_at", DateTime(timezone=True)), Column("accepted_at", DateTime(timezone=True)),
    CheckConstraint("role IN ('viewer','developer')", name="ck_team_invite_role"))


def utcnow():
    return datetime.now(timezone.utc)


def opaque_id():
    return str(uuid.uuid4())


def token_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


class TeamStore:
    def __init__(self, settings):
        settings.validate()
        self.settings = settings
        self.engine = create_engine(settings.database_url, pool_pre_ping=True, hide_parameters=True,
            connect_args={"connect_timeout": 5, "options": "-csearch_path=" + settings.database_schema + " -cstatement_timeout=10000"})

    def migrate(self):
        from .sandbox.db import SANDBOX_TABLES
        from .billing_db import BILLING_TABLES
        from .password_db import PASSWORD_TABLES
        with self.engine.begin() as con:
            con.execute(CreateSchema(self.settings.database_schema, if_not_exists=True))
            con.execute(text("SELECT pg_advisory_xact_lock(1844271001)"))
            versions.create(con, checkfirst=True)
            current = con.execute(select(versions.c.version)).scalars().all()
            if any(version not in {1, 2, 3, 4, 5} for version in current):
                raise ValueError("Unsupported team database schema version; run a reviewed migration")
            metadata.create_all(con)
            if not current:
                con.execute(text("""CREATE FUNCTION team_report_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                  IF NEW.upload IS DISTINCT FROM OLD.upload OR NEW.payload_hash IS DISTINCT FROM OLD.payload_hash
                     OR NEW.project_id IS DISTINCT FROM OLD.project_id OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
                     OR NEW.case_id IS DISTINCT FROM OLD.case_id OR NEW.verdict IS DISTINCT FROM OLD.verdict
                     OR NEW.submitted_by IS DISTINCT FROM OLD.submitted_by OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                    RAISE EXCEPTION 'Imported report content is immutable';
                  END IF;
                  RETURN NEW;
                END $$"""))
                con.execute(text("CREATE TRIGGER team_report_immutable_before_update BEFORE UPDATE ON team_reports FOR EACH ROW EXECUTE FUNCTION team_report_immutable()"))
                con.execute(insert(versions).values(version=1, applied_at=utcnow()))
            if 2 not in current:
                for table in BILLING_TABLES:
                    table.create(con, checkfirst=True)
                con.execute(insert(versions).values(version=2, applied_at=utcnow()))
            if 3 not in current:
                invitations.create(con, checkfirst=True)
                con.execute(insert(versions).values(version=3, applied_at=utcnow()))
            if 4 not in current:
                for table in PASSWORD_TABLES:
                    table.create(con, checkfirst=True)
                con.execute(insert(versions).values(version=4, applied_at=utcnow()))

            if 5 not in current:
                for table in SANDBOX_TABLES:
                    table.create(con, checkfirst=True)
                con.execute(insert(versions).values(version=5, applied_at=utcnow()))

    def get_user(self, user_id):
        with self.engine.connect() as con:
            row = con.execute(select(users).where(users.c.id == user_id)).mappings().first()
            return dict(row) if row else None

    def create_user(self, issuer, subject, email, display_name):
        email = email.strip().casefold()
        if not issuer or len(issuer) > 512 or not subject or len(subject) > 256 or not email or len(email) > 320 or not display_name or len(display_name) > 128:
            raise ValueError("Verified identity fields exceed supported bounds")
        with self.engine.begin() as con:
            con.execute(text("SELECT pg_advisory_xact_lock(hashtext(:identity))"), {"identity": "team-user:" + email})
            row = con.execute(select(users).where(users.c.issuer == issuer, users.c.subject == subject)).mappings().first()
            if row:
                if row["email"] != email:
                    raise ValueError("Identity email changed; explicit account review is required")
                return dict(row)
            existing = con.execute(select(users.c.id).where(users.c.email == email)).first()
            if existing:
                raise ValueError("An account with that email uses a different verified identity; automatic linking is disabled")
            value = {"id": opaque_id(), "issuer": issuer, "subject": subject, "email": email,
                     "display_name": display_name, "created_at": utcnow()}
            con.execute(insert(users).values(**value))
            return value

    def create_session(self, user_id, token_hash, csrf_token, expires_at):
        expires_at = datetime.fromtimestamp(expires_at, timezone.utc)
        with self.engine.begin() as con:
            con.execute(insert(sessions).values(user_id=user_id, token_hash=token_hash, csrf_token=csrf_token, expires_at=expires_at))

    def get_session(self, token_hash):
        with self.engine.connect() as con:
            row = con.execute(select(sessions).where(sessions.c.token_hash == token_hash, sessions.c.expires_at > utcnow())).mappings().first()
            if not row:
                return None
            user = con.execute(select(users).where(users.c.id == row["user_id"])).mappings().first()
            if user and user["issuer"] == "breakroom:development" and not self.settings.dev_login:
                return None
            return {**dict(row), "expires_at": row["expires_at"].timestamp(), "user": dict(user)} if user else None

    def delete_session(self, token_hash):
        with self.engine.begin() as con:
            con.execute(delete(sessions).where(sessions.c.token_hash == token_hash))

    def create_login_attempt(self, state_hash, nonce, code_verifier, expires_at):
        expires_at = datetime.fromtimestamp(expires_at, timezone.utc)
        with self.engine.begin() as con:
            con.execute(insert(login_attempts).values(state_hash=state_hash, nonce=nonce, code_verifier=code_verifier, expires_at=expires_at))

    def cleanup_expired(self, *, batch_size=1000):
        """Delete a bounded batch per table; concurrent sweepers skip held rows."""
        from .password_db import account_tokens, auth_limits
        from .sandbox.db import jobs
        if type(batch_size) is not int or not 1 <= batch_size <= 10000:
            raise ValueError("Expiry cleanup batch size must be 1..10000")
        counts = {}
        cutoff = utcnow()
        with self.engine.begin() as con:
            for name, table, key in (("sandbox_jobs", jobs, jobs.c.id), ("reports", reports, reports.c.id),
                    ("sessions", sessions, sessions.c.token_hash),
                    ("login_attempts", login_attempts, login_attempts.c.state_hash),
                    ("invitations", invitations, invitations.c.id),
                    ("account_tokens", account_tokens, account_tokens.c.token_hash),
                    ("auth_limits", auth_limits, auth_limits.c.bucket_hash)):
                expired = select(key).where(table.c.expires_at <= cutoff).order_by(table.c.expires_at).limit(batch_size).with_for_update(skip_locked=True)
                counts[name] = con.execute(delete(table).where(key.in_(expired))).rowcount
        return counts

    def consume_login_attempt(self, state_hash):
        with self.engine.begin() as con:
            row = con.execute(delete(login_attempts).where(login_attempts.c.state_hash == state_hash).returning(login_attempts)).mappings().first()
            return {**dict(row), "expires_at": row["expires_at"].timestamp()} if row and row["expires_at"] > utcnow() else None
