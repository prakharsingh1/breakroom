"""Email/password accounts with Argon2id, TLS mail and durable recovery tokens.

Registration/login are same-origin JSON requests. Authenticated changes require
the opaque session's CSRF token. Link tokens are transmitted in URL fragments,
then POSTed by the first-party page: they never belong in access-log query URLs.
"""
from __future__ import annotations

import re
import secrets
import smtplib
import ssl
import threading
from datetime import timedelta
from email.message import EmailMessage

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from argon2.profiles import RFC_9106_LOW_MEMORY
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, insert, select, text, update

from .password_db import (PASSWORD_ISSUER, account_tokens, consume_attempts,
    consume_token, credentials, lock_credential, public_user, save_token)
from .team_auth import (LOGIN_PATH, SESSION_COOKIE, SESSION_PATH, _OPAQUE,
    _require_csrf, _require_origin, _secure, principal)
from .team_db import opaque_id, sessions, token_hash, users, utcnow

HASHER = PasswordHasher.from_parameters(RFC_9106_LOW_MEMORY)
_HASH_SLOTS = threading.BoundedSemaphore(2)
_DUMMY_HASH = HASHER.hash(secrets.token_urlsafe(48))
_EMAIL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}\Z")
_GENERIC_LOGIN = "Email or password is incorrect."
_GENERIC_RESET = "If an eligible account exists, a password reset email has been requested."


def normalize_email(value):
    value = value.strip().casefold()
    if not _EMAIL.fullmatch(value) or len(value.split("@", 1)[0]) > 64:
        raise ValueError("Enter a valid email address")
    return value


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class EmailInput(StrictInput):
    email: str = Field(min_length=3, max_length=254)
    _normalize = field_validator("email")(normalize_email)


class LoginInput(EmailInput):
    password: str = Field(min_length=12, max_length=128)


class RegisterInput(LoginInput):
    display_name: str = Field(min_length=1, max_length=120)

    @field_validator("display_name")
    @classmethod
    def valid_name(cls, value):
        value = value.strip()
        if not value or any(ord(character) < 32 for character in value):
            raise ValueError("Enter a display name without control characters")
        return value


class TokenInput(StrictInput):
    token: str = Field(min_length=43, max_length=43, pattern=r"^[A-Za-z0-9_-]{43}$")


class ResetInput(TokenInput):
    password: str = Field(min_length=12, max_length=128)


class ChangeInput(StrictInput):
    current_password: str = Field(min_length=12, max_length=128)
    password: str = Field(min_length=12, max_length=128)


def hash_password(password):
    if not _HASH_SLOTS.acquire(timeout=0.05):
        raise HTTPException(429, "Sign-in is busy. Try again shortly.", headers={"Retry-After": "5"})
    try:
        return HASHER.hash(password)
    finally:
        _HASH_SLOTS.release()


def verify_password(encoded, password):
    if not _HASH_SLOTS.acquire(timeout=0.05):
        raise HTTPException(429, "Sign-in is busy. Try again shortly.", headers={"Retry-After": "5"})
    try:
        try:
            return HASHER.verify(encoded or _DUMMY_HASH, password)
        except (VerificationError, InvalidHashError):
            return False
    finally:
        _HASH_SLOTS.release()


class SMTPMailer:
    def __init__(self, settings):
        self.settings = settings

    def send(self, recipient, purpose, link):
        settings = self.settings
        message = EmailMessage()
        message["From"] = settings.smtp_from
        message["To"] = recipient
        message["Subject"] = "Verify your Breakroom email" if purpose == "verification" else "Reset your Breakroom password"
        action = "verify your email address" if purpose == "verification" else "reset your password"
        expiry = "one hour" if purpose == "verification" else "30 minutes"
        message.set_content(f"Use this single-use link to {action}:\n\n{link}\n\nThe link expires in {expiry}. If you did not request this, you can ignore this message.\n")
        context = ssl.create_default_context()
        if settings.smtp_mode == "ssl":
            connection = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=8, context=context)
        else:
            connection = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=8)
        with connection as smtp:
            if settings.smtp_mode == "starttls":
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.ehlo()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)


def configure_password_auth(app, settings, store, *, mailer=None):
    router = APIRouter(prefix=LOGIN_PATH, tags=["password-accounts"])
    app.state.password_mailer = mailer if mailer is not None else SMTPMailer(settings) if settings.mail_available else None
    app.state.password_mail_status = "ready" if app.state.password_mailer is not None else "disabled"

    def available():
        if not settings.password_enabled:
            raise HTTPException(404, "Email/password accounts are disabled.")

    def mail_available():
        return app.state.password_mailer is not None

    def attempt(request, kind, email):
        # Ignore X-Forwarded-For and all client supplied identity headers.
        peer = request.client.host if request.client else "unknown"
        if not consume_attempts(store, kind, email=email, peer=peer):
            raise HTTPException(429, "Too many account attempts. Try again later.", headers={"Retry-After": "900"})

    def send_link(email, purpose, token):
        sender = app.state.password_mailer
        if not sender:
            return False
        path = "/verify-email" if purpose == "verification" else "/reset-password"
        try:
            sender.send(email, purpose, settings.public_origin + path + "#token=" + token)
            app.state.password_mail_status = "ready"
            return True
        except Exception:
            # SMTP responses can contain credentials, message bodies and tokens.
            # Expose only aggregate health; an explicit retry supersedes the link.
            app.state.password_mail_status = "retry_required"
            return False

    def session_response(request, con, user, *, status_code=200, extra=None):
        previous = request.cookies.get(SESSION_COOKIE, "")
        if _OPAQUE.fullmatch(previous):
            con.execute(delete(sessions).where(sessions.c.token_hash == token_hash(previous)))
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        con.execute(insert(sessions).values(user_id=user["id"], token_hash=token_hash(token), csrf_token=csrf,
            expires_at=utcnow() + timedelta(seconds=settings.session_ttl_seconds)))
        response = JSONResponse({"user": public_user(con, user), "csrf_token": csrf,
            "verification_required": settings.password_require_verification, **(extra or {})}, status_code=status_code,
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
        response.set_cookie(SESSION_COOKIE, token, max_age=settings.session_ttl_seconds,
            path=SESSION_PATH, httponly=True, secure=_secure(settings), samesite="lax")
        return response

    def current(request):
        _require_origin(request, settings)
        who = principal(request, store)
        _require_csrf(request, who)
        if who["user"]["issuer"] != PASSWORD_ISSUER:
            raise HTTPException(409, "This account signs in through a different identity provider.")
        return who["user"]

    @router.get("/config")
    def config():
        return JSONResponse({"password_available": settings.password_enabled, "mail_available": mail_available(),
            "verification_required": settings.password_require_verification,
            "oidc_available": bool(settings.oidc_issuer and settings.oidc_client_id)}, headers={"Cache-Control": "no-store"})

    @router.post("/register")
    def register(request: Request, body: RegisterInput):
        available()
        _require_origin(request, settings)
        attempt(request, "register", body.email)
        encoded = hash_password(body.password)
        verification_token = secrets.token_urlsafe(32) if mail_available() else None
        with store.engine.begin() as con:
            con.execute(text("SELECT pg_advisory_xact_lock(hashtext(:identity))"), {"identity": "team-user:" + body.email})
            if con.execute(select(users.c.id).where(users.c.email == body.email)).first():
                raise HTTPException(409, "Could not create an account with those details. Try signing in or password recovery.")
            user = {"id": opaque_id(), "issuer": PASSWORD_ISSUER, "subject": opaque_id(), "email": body.email,
                "display_name": body.display_name, "created_at": utcnow()}
            con.execute(insert(users).values(**user))
            con.execute(insert(credentials).values(user_id=user["id"], password_hash=encoded, verified_at=None, changed_at=utcnow()))
            if verification_token:
                save_token(con, user["id"], "verification", verification_token)
            response = session_response(request, con, user, status_code=201)
        sent = bool(verification_token and send_link(body.email, "verification", verification_token))
        # Mail only after the durable account transaction commits.
        import json
        payload = json.loads(response.body)
        payload.update(verification_sent=sent, mail_available=mail_available())
        response.body = JSONResponse(payload).body
        response.headers["content-length"] = str(len(response.body))
        return response

    @router.post("/password-login")
    def login(request: Request, body: LoginInput):
        available()
        _require_origin(request, settings)
        attempt(request, "login", body.email)
        with store.engine.begin() as con:
            user = con.execute(select(users).where(users.c.email == body.email, users.c.issuer == PASSWORD_ISSUER)).mappings().first()
            credential = lock_credential(con, user["id"]) if user else None
            valid = verify_password(credential["password_hash"] if credential else None, body.password)
            if not user or not credential or not valid:
                raise HTTPException(401, _GENERIC_LOGIN)
            if HASHER.check_needs_rehash(credential["password_hash"]):
                con.execute(update(credentials).where(credentials.c.user_id == user["id"]).values(password_hash=hash_password(body.password)))
            return session_response(request, con, user)

    @router.post("/request-verification", status_code=202)
    def request_verification(request: Request):
        available()
        user = current(request)
        attempt(request, "mail", user["email"])
        token = None
        with store.engine.begin() as con:
            credential = lock_credential(con, user["id"])
            if credential and credential["verified_at"] is None and mail_available():
                token = secrets.token_urlsafe(32)
                save_token(con, user["id"], "verification", token)
        sent = bool(token and send_link(user["email"], "verification", token))
        return {"ok": True, "mail_available": mail_available(), "verification_sent": sent}

    @router.post("/verify-email")
    def verify_email(request: Request, body: TokenInput):
        available()
        _require_origin(request, settings)
        attempt(request, "token", token_hash(body.token))
        with store.engine.begin() as con:
            row = consume_token(con, body.token, "verification")
            if not row:
                raise HTTPException(400, "This verification link is invalid, expired or already used.")
            con.execute(update(credentials).where(credentials.c.user_id == row["user_id"]).values(verified_at=utcnow()))
        return {"ok": True}

    @router.post("/request-password-reset", status_code=202)
    def request_reset(request: Request, body: EmailInput, background_tasks: BackgroundTasks):
        available()
        _require_origin(request, settings)
        attempt(request, "mail", body.email)
        token = None
        with store.engine.begin() as con:
            user = con.execute(select(users).where(users.c.email == body.email, users.c.issuer == PASSWORD_ISSUER)).mappings().first()
            if user and mail_available() and lock_credential(con, user["id"]):
                token = secrets.token_urlsafe(32)
                save_token(con, user["id"], "reset", token)
        if token:
            # The generic response is sent before contacting SMTP. The response
            # must not reveal account existence through provider latency.
            background_tasks.add_task(send_link, body.email, "reset", token)
        return {"ok": True, "message": _GENERIC_RESET, "mail_available": mail_available()}

    @router.post("/reset-password")
    def reset_password(request: Request, body: ResetInput):
        available()
        _require_origin(request, settings)
        attempt(request, "token", token_hash(body.token))
        encoded = hash_password(body.password)
        with store.engine.begin() as con:
            row = consume_token(con, body.token, "reset")
            if not row:
                raise HTTPException(400, "This reset link is invalid, expired or already used.")
            con.execute(update(credentials).where(credentials.c.user_id == row["user_id"]).values(password_hash=encoded, changed_at=utcnow()))
            con.execute(delete(sessions).where(sessions.c.user_id == row["user_id"]))
            con.execute(delete(account_tokens).where(account_tokens.c.user_id == row["user_id"]))
        response = JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})
        response.delete_cookie(SESSION_COOKIE, path=SESSION_PATH, httponly=True, secure=_secure(settings), samesite="lax")
        return response

    @router.post("/change-password")
    def change_password(request: Request, body: ChangeInput):
        available()
        user = current(request)
        attempt(request, "change", user["email"])
        with store.engine.begin() as con:
            credential = lock_credential(con, user["id"])
            if not credential or not verify_password(credential["password_hash"], body.current_password):
                raise HTTPException(401, _GENERIC_LOGIN)
            con.execute(update(credentials).where(credentials.c.user_id == user["id"]).values(password_hash=hash_password(body.password), changed_at=utcnow()))
            con.execute(delete(sessions).where(sessions.c.user_id == user["id"]))
            con.execute(delete(account_tokens).where(account_tokens.c.user_id == user["id"]))
            return session_response(request, con, user, extra={"ok": True})

    app.include_router(router)
    return router
