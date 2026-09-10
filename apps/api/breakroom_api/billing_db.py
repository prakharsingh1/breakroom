"""Migration 2: project-bound billing records and a bounded durable event inbox."""
from sqlalchemy import (BigInteger, Boolean, CheckConstraint, Column, DateTime, ForeignKey,
    Integer, String, Table, UniqueConstraint)
from sqlalchemy.dialects.postgresql import JSONB
from .team_db import metadata

accounts = Table("team_billing_accounts", metadata,
    Column("project_id", ForeignKey("team_projects.id", ondelete="CASCADE"), primary_key=True),
    Column("customer_id", String(128), unique=True), Column("subscription_id", String(128), unique=True),
    Column("status", String(32), nullable=False), Column("sync_state", String(16), nullable=False),
    Column("cancel_at_period_end", Boolean, nullable=False),
    Column("current_period_end", DateTime(timezone=True)), Column("paid_through", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("sync_state IN ('ready','pending','retrying','unknown')", name="ck_billing_sync"))
operations = Table("team_billing_operations", metadata,
    Column("id", String(36), primary_key=True),
    Column("project_id", ForeignKey("team_projects.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("kind", String(16), nullable=False), Column("idempotency_key", String(64), nullable=False),
    Column("request_hash", String(64), nullable=False), Column("status", String(16), nullable=False),
    Column("result", JSONB), Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("project_id", "kind", "idempotency_key", name="uq_billing_operation"),
    CheckConstraint("kind IN ('checkout','cancel')", name="ck_billing_operation_kind"),
    CheckConstraint("status IN ('pending','completed','unknown')", name="ck_billing_operation_status"))
events = Table("team_billing_events", metadata,
    Column("event_id", String(128), primary_key=True),
    Column("project_id", ForeignKey("team_projects.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("payload_hash", String(64), nullable=False), Column("event_type", String(80), nullable=False),
    Column("target_id", String(128), nullable=False), Column("target_kind", String(16), nullable=False),
    Column("provider_created", BigInteger, nullable=False), Column("status", String(16), nullable=False, index=True),
    Column("attempts", Integer, nullable=False), Column("lease_token", String(36)),
    Column("lease_until", DateTime(timezone=True)), Column("retry_at", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False), Column("processed_at", DateTime(timezone=True)),
    CheckConstraint("status IN ('pending','processing','retrying','processed','ignored','failed')", name="ck_billing_event_status"),
    CheckConstraint("attempts BETWEEN 0 AND 20", name="ck_billing_event_attempts"))

BILLING_TABLES = (accounts, operations, events)
