"""Durable password credentials, single-use tokens and abuse limits (schema 4).

No plaintext password, bearer token, email message or peer address is stored.
All account mutations lock the credential row, including session issuance, so
a reset cannot race a successful login into creating a session for an old hash.
"""
from datetime import timedelta

from sqlalchemy import (CheckConstraint, Column, DateTime, ForeignKey, Integer,
    String, Table, delete, insert, select, text, update)

from .team_db import metadata, token_hash, utcnow

PASSWORD_ISSUER = "breakroom:password"
credentials = Table("team_password_credentials", metadata,
    Column("user_id", ForeignKey("team_users.id", ondelete="CASCADE"), primary_key=True),
    Column("password_hash", String(512), nullable=False),
    Column("verified_at", DateTime(timezone=True)),
    Column("changed_at", DateTime(timezone=True), nullable=False))
account_tokens = Table("team_account_tokens", metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("user_id", ForeignKey("team_users.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("purpose", String(16), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False, index=True),
    CheckConstraint("purpose IN ('verification','reset')", name="ck_team_account_token_purpose"))
auth_limits = Table("team_auth_limits", metadata,
    Column("bucket_hash", String(64), primary_key=True),
    Column("attempts", Integer, nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False, index=True),
    CheckConstraint("attempts BETWEEN 1 AND 10000", name="ck_team_auth_attempt_count"))
PASSWORD_TABLES = (credentials, account_tokens, auth_limits)


def user_email_verified(con, user):
    if user["issuer"] == PASSWORD_ISSUER:
        return con.execute(select(credentials.c.verified_at).where(credentials.c.user_id == user["id"])).scalar_one_or_none() is not None
    return user["issuer"] != "breakroom:development"


def public_user(con, user):
    method = "password" if user["issuer"] == PASSWORD_ISSUER else "development" if user["issuer"] == "breakroom:development" else "oidc"
    return {key: user[key] for key in ("id", "email", "display_name")} | {
        "email_verified": user_email_verified(con, user), "auth_method": method}


def consume_attempts(store, kind, *, email, peer):
    """Atomically reserve account/IP budgets in a separate committed transaction.

    Limits survive workers/restarts. The authenticated proxy is intentionally not
    trusted to forward arbitrary IP headers; its peers share the IP budget.
    """
    budgets = {"login": (8, 80, 900), "register": (5, 20, 3600),
               "mail": (3, 20, 900), "token": (12, 60, 900), "change": (8, 80, 900)}
    account_max, peer_max, seconds = budgets[kind]
    buckets = [(token_hash("account:" + kind + ":" + email), account_max),
               (token_hash("peer:" + kind + ":" + peer), peer_max)]
    now = utcnow()
    allowed = True
    with store.engine.begin() as con:
        # Stable global lock order avoids deadlocks across accounts and peers.
        for key, maximum in sorted(buckets):
            con.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": "auth-limit:" + key})
            row = con.execute(select(auth_limits).where(auth_limits.c.bucket_hash == key)).mappings().first()
            if not row:
                con.execute(insert(auth_limits).values(bucket_hash=key, attempts=1, expires_at=now + timedelta(seconds=seconds)))
            elif row["expires_at"] <= now:
                con.execute(update(auth_limits).where(auth_limits.c.bucket_hash == key).values(attempts=1, expires_at=now + timedelta(seconds=seconds)))
            elif row["attempts"] >= maximum:
                allowed = False
            else:
                con.execute(update(auth_limits).where(auth_limits.c.bucket_hash == key).values(attempts=row["attempts"] + 1))
    return allowed


def lock_credential(con, user_id):
    return con.execute(select(credentials).where(credentials.c.user_id == user_id).with_for_update()).mappings().first()


def save_token(con, user_id, purpose, raw_token):
    # Call while holding the credential lock. Resending supersedes older links.
    con.execute(delete(account_tokens).where(account_tokens.c.user_id == user_id, account_tokens.c.purpose == purpose))
    con.execute(insert(account_tokens).values(token_hash=token_hash(raw_token), user_id=user_id,
        purpose=purpose, expires_at=utcnow() + timedelta(seconds=3600 if purpose == "verification" else 1800)))


def consume_token(con, raw_token, purpose):
    digest = token_hash(raw_token)
    row = con.execute(select(account_tokens).where(account_tokens.c.token_hash == digest,
        account_tokens.c.purpose == purpose)).mappings().first()
    if not row:
        return None
    credential = lock_credential(con, row["user_id"])
    if not credential:
        return None
    row = con.execute(delete(account_tokens).where(account_tokens.c.token_hash == digest,
        account_tokens.c.purpose == purpose).returning(account_tokens)).mappings().first()
    return dict(row) if row and row["expires_at"] > utcnow() else None
