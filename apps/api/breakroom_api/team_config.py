"""Explicit local/hosted configuration for the separate team reporting service."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class TeamSettings:
    database_url: str = "postgresql+psycopg://breakroom:breakroom-local-development-only@127.0.0.1:54329/breakroom"
    database_schema: str = "public"
    environment: str = "development"
    dev_login: bool = False
    public_origin: str = "http://localhost:3000"
    session_ttl_seconds: int = 86400
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    oidc_redirect_uri: str | None = None
    trusted_proxy_secret: str | None = None
    password_enabled: bool = True
    smtp_host: str | None = None
    smtp_port: int = 465
    smtp_mode: str = "ssl"
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    max_request_bytes: int = 2 * 1024 * 1024
    rate_limit_per_minute: int = 120
    body_read_timeout_seconds: float = 10.0
    retention_sweep_seconds: float = 300.0
    billing_mode: str = "disabled"
    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None
    stripe_price_id: str | None = None
    billing_amount_minor: int = 4900
    billing_currency: str = "usd"
    billing_worker_seconds: float = 2.0
    billing_seat_limit: int = 10
    billing_report_limit: int = 1000
    billing_storage_limit: int = 64 * 1024 * 1024

    @property
    def secure_cookies(self) -> bool:
        return self.environment == "production" or self.public_origin.startswith("https://")

    @property
    def password_require_verification(self) -> bool:
        return self.environment == "production"

    @property
    def mail_available(self) -> bool:
        return bool(self.smtp_host and self.smtp_from)

    @classmethod
    def from_environment(cls):
        return cls(database_url=os.environ.get("BREAKROOM_TEAM_DATABASE_URL", cls.database_url),
            database_schema=os.environ.get("BREAKROOM_TEAM_DB_SCHEMA", "public"),
            environment=os.environ.get("BREAKROOM_TEAM_ENV", "development"),
            dev_login=os.environ.get("BREAKROOM_TEAM_DEV_LOGIN", "0") == "1",
            public_origin=os.environ.get("BREAKROOM_TEAM_PUBLIC_ORIGIN", "http://localhost:3000").rstrip("/"),
            session_ttl_seconds=int(os.environ.get("BREAKROOM_TEAM_SESSION_TTL", "86400")),
            oidc_issuer=os.environ.get("BREAKROOM_TEAM_OIDC_ISSUER") or None,
            oidc_client_id=os.environ.get("BREAKROOM_TEAM_OIDC_CLIENT_ID") or None,
            oidc_client_secret=os.environ.get("BREAKROOM_TEAM_OIDC_CLIENT_SECRET") or None,
            oidc_redirect_uri=os.environ.get("BREAKROOM_TEAM_OIDC_REDIRECT_URI") or None,
            trusted_proxy_secret=os.environ.get("BREAKROOM_TEAM_LOCAL_PROXY_SECRET") or None,
            password_enabled=os.environ.get("BREAKROOM_PASSWORD_ENABLED", "1") == "1",
            smtp_host=os.environ.get("BREAKROOM_SMTP_HOST") or None,
            smtp_port=int(os.environ.get("BREAKROOM_SMTP_PORT", "465")),
            smtp_mode=os.environ.get("BREAKROOM_SMTP_MODE", "ssl"),
            smtp_username=os.environ.get("BREAKROOM_SMTP_USERNAME") or None,
            smtp_password=os.environ.get("BREAKROOM_SMTP_PASSWORD") or None,
            smtp_from=os.environ.get("BREAKROOM_SMTP_FROM") or None,
            rate_limit_per_minute=int(os.environ.get("BREAKROOM_TEAM_RATE_LIMIT", "120")),
            retention_sweep_seconds=float(os.environ.get("BREAKROOM_TEAM_RETENTION_SWEEP_SECONDS", "300")),
            billing_mode=os.environ.get("BREAKROOM_BILLING_MODE", "disabled"),
            stripe_secret_key=os.environ.get("BREAKROOM_STRIPE_SECRET_KEY") or None,
            stripe_webhook_secret=os.environ.get("BREAKROOM_STRIPE_WEBHOOK_SECRET") or None,
            stripe_price_id=os.environ.get("BREAKROOM_STRIPE_PRICE_ID") or None,
            billing_amount_minor=int(os.environ.get("BREAKROOM_BILLING_AMOUNT_MINOR", "4900")),
            billing_currency=os.environ.get("BREAKROOM_BILLING_CURRENCY", "usd"),
            billing_seat_limit=int(os.environ.get("BREAKROOM_BILLING_SEAT_LIMIT", "10")),
            billing_report_limit=int(os.environ.get("BREAKROOM_BILLING_REPORT_LIMIT", "1000")),
            billing_storage_limit=int(os.environ.get("BREAKROOM_BILLING_STORAGE_LIMIT", "67108864")))

    def validate(self):
        if not self.database_url.startswith("postgresql+psycopg://"):
            raise ValueError("Team reporting requires PostgreSQL with the psycopg driver")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", self.database_schema):
            raise ValueError("Invalid PostgreSQL schema name")
        if self.environment not in {"development", "test", "production"}:
            raise ValueError("Invalid team environment")
        origin = urlsplit(self.public_origin)
        if origin.scheme not in {"http", "https"} or not origin.hostname or origin.username or origin.password or origin.query or origin.fragment or origin.path:
            raise ValueError("Public origin must be an exact HTTP(S) origin without a path")
        local = origin.hostname in {"localhost", "127.0.0.1", "::1"}
        if origin.scheme != "https" and not local:
            raise ValueError("Non-local hosted origins require HTTPS")
        if self.dev_login and (self.environment == "production" or not local):
            raise ValueError("Development login is allowed only on explicit localhost development/test origins")
        if self.environment == "production" and not self.public_origin.startswith("https://"):
            raise ValueError("Production team reporting requires HTTPS")
        if not 300 <= self.session_ttl_seconds <= 604800:
            raise ValueError("Session TTL must be 300..604800 seconds")
        if not 1 <= self.rate_limit_per_minute <= 10000:
            raise ValueError("Rate limit must be 1..10000 requests per minute")
        if not 0.1 <= self.body_read_timeout_seconds <= 30:
            raise ValueError("Body read timeout must be 0.1..30 seconds")
        if not 0.1 <= self.retention_sweep_seconds <= 3600:
            raise ValueError("Retention sweep interval must be 0.1..3600 seconds")
        if not 0.1 <= self.billing_worker_seconds <= 60:
            raise ValueError("Billing worker interval must be 0.1..60 seconds")
        if not (1 <= self.billing_seat_limit <= 100 and 1 <= self.billing_report_limit <= 1000 and 1 <= self.billing_storage_limit <= 1073741824):
            raise ValueError("Billing usage limits exceed supported bounds")
        if self.billing_currency != "usd":
            raise ValueError("The supported monthly test plan uses USD only")
        if type(self.password_enabled) is not bool:
            raise ValueError("Password account setting must be a boolean")
        if self.smtp_mode not in {"ssl", "starttls"} or type(self.smtp_port) is not int or not 1 <= self.smtp_port <= 65535:
            raise ValueError("SMTP requires TLS with a valid port")
        if bool(self.smtp_host) != bool(self.smtp_from) or bool(self.smtp_username) != bool(self.smtp_password):
            raise ValueError("SMTP requires host/from and paired username/password settings")
        if self.smtp_host and (len(self.smtp_host) > 253 or not re.fullmatch(r"[A-Za-z0-9.-]+", self.smtp_host)):
            raise ValueError("SMTP host must be a hostname")
        if self.smtp_from and (len(self.smtp_from) > 254 or not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}", self.smtp_from)):
            raise ValueError("SMTP sender must be a single valid email address")
        if self.password_enabled and self.environment == "production" and not self.mail_available:
            raise ValueError("Production email/password accounts require configured TLS SMTP for verification and recovery")
