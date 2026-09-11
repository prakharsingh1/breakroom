"""Migration 5: tenant-owned source, keys, queued runs and trusted trial evidence."""
from sqlalchemy import (Boolean, CheckConstraint, Column, DateTime, ForeignKey,
                        Integer, String, Table, Text, UniqueConstraint)
from sqlalchemy.dialects.postgresql import JSONB
from ..team_db import metadata


deployments = Table('sandbox_deployments', metadata,
    Column('id', String(36), primary_key=True),
    Column('project_id', ForeignKey('team_projects.id', ondelete='CASCADE'), nullable=False, index=True),
    Column('name', String(120), nullable=False), Column('source_kind', String(16), nullable=False),
    Column('repository', String(160)), Column('commit_sha', String(40)),
    Column('sha256', String(64), nullable=False), Column('manifest', JSONB, nullable=False),
    Column('source', Text, nullable=False), Column('created_at', DateTime(timezone=True), nullable=False))
credentials = Table('sandbox_credentials', metadata,
    Column('id', String(36), primary_key=True),
    Column('project_id', ForeignKey('team_projects.id', ondelete='CASCADE'), nullable=False, index=True),
    Column('provider', String(16), nullable=False), Column('secret', Text, nullable=False),
    Column('created_at', DateTime(timezone=True), nullable=False),
    UniqueConstraint('project_id', 'provider', name='uq_sandbox_provider'),
    CheckConstraint("provider IN ('openai','anthropic')", name='ck_sandbox_provider'))
jobs = Table('sandbox_jobs', metadata,
    Column('id', String(36), primary_key=True),
    Column('project_id', ForeignKey('team_projects.id', ondelete='CASCADE'), nullable=False, index=True),
    Column('deployment_id', ForeignKey('sandbox_deployments.id', ondelete='CASCADE'), nullable=False),
    Column('submitted_by', ForeignKey('team_users.id', ondelete='CASCADE'), nullable=False),
    Column('credential_id', ForeignKey('sandbox_credentials.id', ondelete='SET NULL')),
    Column('request_key', String(64), nullable=False), Column('request_hash', String(64), nullable=False),
    Column('plan', JSONB, nullable=False), Column('provider', String(16)), Column('model', String(120)),
    Column('max_calls', Integer, nullable=False), Column('max_output_tokens', Integer, nullable=False),
    Column('calls_used', Integer, nullable=False), Column('usage', JSONB, nullable=False),
    Column('status', String(20), nullable=False), Column('verdict', String(20), nullable=False),
    Column('cancel_requested', Boolean, nullable=False), Column('error', String(256)),
    Column('worker_id', String(36)), Column('runtime', String(16), nullable=False), Column('image_id', String(80)),
    Column('created_at', DateTime(timezone=True), nullable=False), Column('started_at', DateTime(timezone=True)),
    Column('heartbeat_at', DateTime(timezone=True)), Column('finished_at', DateTime(timezone=True)),
    Column('expires_at', DateTime(timezone=True), nullable=False, index=True),
    UniqueConstraint('project_id', 'request_key', name='uq_sandbox_request'),
    CheckConstraint("status IN ('queued','running','completed','error','cancelled')", name='ck_sandbox_status'),
    CheckConstraint("verdict IN ('PASS','FAIL','INCONCLUSIVE')", name='ck_sandbox_verdict'),
    CheckConstraint('calls_used >= 0 AND calls_used <= max_calls AND max_calls >= 0 AND max_calls <= 100', name='ck_sandbox_calls'),
    CheckConstraint('max_output_tokens BETWEEN 1 AND 2048', name='ck_sandbox_tokens'),
    CheckConstraint("jsonb_typeof(plan)='array' AND jsonb_array_length(plan) BETWEEN 1 AND 72", name='ck_sandbox_plan'))
trials = Table('sandbox_trials', metadata,
    Column('job_id', ForeignKey('sandbox_jobs.id', ondelete='CASCADE'), primary_key=True),
    Column('position', Integer, primary_key=True), Column('report', JSONB, nullable=False),
    Column('created_at', DateTime(timezone=True), nullable=False))
workers = Table('sandbox_workers', metadata,
    Column('id', String(36), primary_key=True), Column('runtime', String(16), nullable=False),
    Column('image_id', String(80), nullable=False), Column('development_only', Boolean, nullable=False),
    Column('heartbeat_at', DateTime(timezone=True), nullable=False, index=True))
SANDBOX_TABLES = (deployments, credentials, jobs, trials, workers)
