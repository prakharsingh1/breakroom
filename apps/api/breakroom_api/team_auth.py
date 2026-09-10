"""OIDC authorization-code login and opaque, server-owned team sessions.

Authlib supplies OAuth/PKCE, signature verification and OIDC claim validation.
The application stores login state server-side, binds it to the browser, and
stores only a SHA-256 digest of each opaque authentication session token.
Provider tokens are neither retained nor sent to the application browser.
"""
from __future__ import annotations

import hashlib
import re
import secrets
import time
from typing import Any
from urllib.parse import urlsplit

import httpx
from authlib.integrations.httpx_client import AsyncOAuth2Client
from authlib.oidc.core import CodeIDToken
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from joserfc import jwt
from joserfc.jwk import KeySet

SESSION_COOKIE = "breakroom_team_session"
LOGIN_COOKIE = "breakroom_team_login"
SESSION_PATH = "/api/team"
LOGIN_PATH = "/api/team/auth"
LOGIN_TTL_SECONDS = 600
_LOOPBACK_NAMES = frozenset({"localhost", "127.0.0.1", "::1"})
_OPAQUE = re.compile(r"[A-Za-z0-9_-]{32,128}\Z")
_EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+\Z")


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _safe_url(value: str, *, local_http: bool) -> bool:
    try:
        parsed = urlsplit(value)
        return bool(parsed.hostname and not parsed.username and not parsed.password
                    and not parsed.query and not parsed.fragment
                    and (parsed.scheme == "https" or (local_http and parsed.scheme == "http"
                         and parsed.hostname in _LOOPBACK_NAMES)))
    except (TypeError, ValueError):
        return False


def validate_auth_settings(settings: Any) -> None:
    local = settings.environment in {"development", "test"}
    if settings.environment not in {"development", "test", "production"}:
        raise ValueError("Unknown team authentication environment")
    if not _safe_url(settings.public_origin, local_http=local):
        raise ValueError("Team public origin must use HTTPS, or explicit local development HTTP")
    origin = urlsplit(settings.public_origin)
    if origin.path not in {"", "/"}:
        raise ValueError("Team public origin must not include a path")
    if settings.dev_login and (not local or origin.hostname not in _LOOPBACK_NAMES):
        raise ValueError("Development login is only allowed in a local development/test environment")
    proxy_secret = getattr(settings, "trusted_proxy_secret", None)
    if proxy_secret is not None and (not isinstance(proxy_secret, str) or len(proxy_secret) < 32):
        raise ValueError("A configured local proxy secret must contain at least 32 characters")
    if type(settings.session_ttl_seconds) is not int or not 1 <= settings.session_ttl_seconds <= 604800:
        raise ValueError("Team sessions must expire within one week")
    if bool(settings.oidc_issuer) != bool(settings.oidc_client_id):
        raise ValueError("OIDC requires both issuer and client ID")
    if settings.oidc_client_secret and not settings.oidc_issuer:
        raise ValueError("OIDC client secret requires an issuer and client ID")
    if settings.oidc_issuer:
        if not _safe_url(settings.oidc_issuer, local_http=local):
            raise ValueError("OIDC issuer must use HTTPS, or local development HTTP")
        if not _safe_url(_redirect_uri(settings), local_http=local):
            raise ValueError("OIDC callback must use HTTPS, or local development HTTP")


def _redirect_uri(settings: Any) -> str:
    return settings.oidc_redirect_uri or settings.public_origin.rstrip("/") + LOGIN_PATH + "/callback"


def _secure(settings: Any) -> bool:
    return settings.environment == "production" or urlsplit(settings.public_origin).scheme == "https" or settings.secure_cookies


def principal(request: Request, store: Any) -> dict:
    token = request.cookies.get(SESSION_COOKIE, "")
    if not _OPAQUE.fullmatch(token):
        raise HTTPException(401, "Sign in to access team reports.")
    digest = token_hash(token)
    session = store.get_session(digest)
    if not session or session["expires_at"] <= time.time():
        if session:
            store.delete_session(digest)
        raise HTTPException(401, "Your session is unavailable or expired. Sign in again.")
    if (session.get("user", {}).get("issuer") == "breakroom:password"
            and not getattr(getattr(store, "settings", None), "password_enabled", True)):
        raise HTTPException(401, "Email/password accounts are disabled.")
    return session


def _require_origin(request: Request, settings: Any) -> None:
    if request.headers.get("origin") != settings.public_origin.rstrip("/"):
        raise HTTPException(403, "Request origin is not allowed.")


def _require_csrf(request: Request, session: dict) -> None:
    supplied = request.headers.get("x-csrf-token", "")
    expected = session.get("csrf_token", "")
    if not supplied or not expected or not secrets.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(403, "A valid CSRF token is required.")


def _trusted_proxy(request: Request, settings: Any) -> bool:
    expected = getattr(settings, "trusted_proxy_secret", None)
    supplied = request.headers.get("x-breakroom-local-proxy", "")
    return bool(expected and supplied and secrets.compare_digest(expected.encode(), supplied.encode()))


def _local_peer(request: Request, settings: Any) -> bool:
    return bool((request.client and request.client.host in _LOOPBACK_NAMES) or _trusted_proxy(request, settings))


def _start_session(request: Request, response: Any, settings: Any, store: Any, user: dict) -> str:
    previous = request.cookies.get(SESSION_COOKIE, "")
    if _OPAQUE.fullmatch(previous):
        store.delete_session(token_hash(previous))
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    store.create_session(user["id"], token_hash(token), csrf, time.time() + settings.session_ttl_seconds)
    response.set_cookie(SESSION_COOKIE, token, max_age=settings.session_ttl_seconds,
                        path=SESSION_PATH, httponly=True, secure=_secure(settings), samesite="lax")
    response.headers["Cache-Control"] = "no-store"
    return csrf


def verify_id_token(id_token: str, jwks: dict, *, issuer: str, client_id: str,
                    nonce: str, access_token: str | None = None) -> dict:
    """Use Authlib's RS256 and OIDC validators; reject unverified profile email."""
    if not isinstance(id_token, str) or len(id_token) > 65536:
        raise ValueError("Missing or oversized ID token")
    if not isinstance(nonce, str) or not nonce:
        raise ValueError("The original OIDC nonce is required")
    decoded = jwt.decode(id_token, KeySet.import_key_set(jwks), algorithms=["RS256"])
    audience = decoded.claims.get("aud")
    if (decoded.claims.get("iss") != issuer
            or not (isinstance(audience, str) or (isinstance(audience, list)
                and audience and all(isinstance(item, str) and item for item in audience)))):
        raise ValueError("Invalid OIDC issuer or audience claim shape")
    claims = CodeIDToken(
        decoded.claims, decoded.header,
        options={"iss": {"essential": True, "value": issuer},
                 "aud": {"essential": True, "value": client_id}},
        params={"nonce": nonce, "client_id": client_id, "access_token": access_token},
    )
    claims.validate(leeway=30)
    email = claims.get("email")
    if (claims.get("email_verified") is not True or not isinstance(email, str)
            or len(email) > 254 or not _EMAIL.fullmatch(email)):
        raise ValueError("A verified email address is required")
    if not isinstance(claims.get("sub"), str) or not 1 <= len(claims["sub"]) <= 255:
        raise ValueError("Invalid OIDC subject")
    name = claims.get("name", email)
    if not isinstance(name, str) or not name.strip() or len(name) > 120:
        raise ValueError("Invalid profile display name")
    return {"issuer": issuer, "subject": claims["sub"], "email": email.strip().casefold(), "display_name": name.strip()}


class DevLogin(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    email: str = Field(default="developer@example.invalid", min_length=3, max_length=254)
    display_name: str = Field(default="Local developer", min_length=1, max_length=120)


def configure_auth(app: FastAPI, settings: Any, store: Any, *, http_transport=None) -> APIRouter:
    """Install authentication routes; transport injection is only for local tests."""
    validate_auth_settings(settings)
    router = APIRouter(prefix=LOGIN_PATH, tags=["team-auth"])

    def client() -> AsyncOAuth2Client:
        return AsyncOAuth2Client(settings.oidc_client_id, settings.oidc_client_secret,
            scope="openid email profile", redirect_uri=_redirect_uri(settings),
            code_challenge_method="S256",
            token_endpoint_auth_method="client_secret_basic" if settings.oidc_client_secret else "none",
            transport=http_transport, timeout=5, follow_redirects=False)

    async def metadata(oauth: AsyncOAuth2Client) -> dict:
        response = await oauth.request("GET", settings.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration", withhold_token=True)
        response.raise_for_status()
        if len(response.content) > 262144:
            raise ValueError("OIDC metadata exceeds bounds")
        data = response.json()
        if data.get("issuer") != settings.oidc_issuer:
            raise ValueError("OIDC discovery issuer does not match configuration")
        for field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            if not isinstance(data.get(field), str) or not _safe_url(data[field], local_http=settings.environment != "production"):
                raise ValueError("OIDC discovery endpoint must use a trusted transport")
        if "RS256" not in data.get("id_token_signing_alg_values_supported", []):
            raise ValueError("This integration requires provider RS256 ID tokens")
        if "S256" not in data.get("code_challenge_methods_supported", []):
            raise ValueError("This integration requires provider S256 PKCE")
        return data

    @router.get("/login")
    async def login(request: Request):
        if not settings.oidc_issuer:
            raise HTTPException(503, "Team sign-in requires configured OIDC credentials.")
        state, nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(64)
        try:
            async with client() as oauth:
                config = await metadata(oauth)
                location, _ = oauth.create_authorization_url(config["authorization_endpoint"],
                    state=state, nonce=nonce, code_verifier=verifier)
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            raise HTTPException(503, "The identity provider is unavailable or misconfigured.") from exc
        store.create_login_attempt(token_hash(state), nonce, verifier, time.time() + LOGIN_TTL_SECONDS)
        response = RedirectResponse(location, status_code=302, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
        response.set_cookie(LOGIN_COOKIE, state, max_age=LOGIN_TTL_SECONDS,
                            path=LOGIN_PATH, httponly=True, secure=_secure(settings), samesite="lax")
        return response

    @router.get("/callback")
    async def callback(request: Request):
        if not settings.oidc_issuer:
            raise HTTPException(503, "Team sign-in requires configured OIDC credentials.")
        state = request.query_params.get("state", "")
        browser_state = request.cookies.get(LOGIN_COOKIE, "")
        if (len(request.query_params.getlist("state")) != 1 or not _OPAQUE.fullmatch(state)
                or not _OPAQUE.fullmatch(browser_state)
                or not secrets.compare_digest(state, browser_state)):
            raise HTTPException(400, "The sign-in state is invalid. Start sign-in again.")
        attempt = store.consume_login_attempt(token_hash(state))
        if not attempt or attempt["expires_at"] <= time.time():
            raise HTTPException(400, "The sign-in attempt expired or was already used.")
        code = request.query_params.get("code", "")
        if (request.query_params.get("error") or len(request.query_params.getlist("code")) != 1
                or not 1 <= len(code) <= 4096 or request.query_params.get("iss", settings.oidc_issuer) != settings.oidc_issuer):
            raise HTTPException(400, "The identity provider did not authorize this sign-in.")
        try:
            async with client() as oauth:
                config = await metadata(oauth)
                token = await oauth.fetch_token(config["token_endpoint"], code=code,
                    grant_type="authorization_code", code_verifier=attempt["code_verifier"])
                response = await oauth.request("GET", config["jwks_uri"], withhold_token=True)
                response.raise_for_status()
                if len(response.content) > 262144:
                    raise ValueError("OIDC keys exceed bounds")
                profile = verify_id_token(token.get("id_token"), response.json(),
                    issuer=settings.oidc_issuer, client_id=settings.oidc_client_id,
                    nonce=attempt["nonce"], access_token=token.get("access_token"))
        except Exception as exc:
            # Provider errors can contain credentials/tokens; never return them.
            raise HTTPException(400, "The identity provider response could not be verified. Start sign-in again.") from exc
        try:
            user = store.create_user(**profile)
        except ValueError as exc:
            raise HTTPException(409, "This identity could not be linked to a team account.") from exc
        response = RedirectResponse(settings.public_origin.rstrip("/") + "/projects", status_code=303,
                                    headers={"Referrer-Policy": "no-referrer"})
        _start_session(request, response, settings, store, user)
        response.delete_cookie(LOGIN_COOKIE, path=LOGIN_PATH, httponly=True, secure=_secure(settings), samesite="lax")
        return response

    @router.post("/dev-login")
    async def development_login(request: Request, body: DevLogin):
        if (not settings.dev_login or settings.environment not in {"development", "test"}
                or (request.url.hostname not in _LOOPBACK_NAMES and not _trusted_proxy(request, settings))
                or not _local_peer(request, settings)
                or urlsplit(settings.public_origin).hostname not in _LOOPBACK_NAMES):
            raise HTTPException(404, "Development sign-in is unavailable.")
        _require_origin(request, settings)
        email, name = body.email.strip().casefold(), body.display_name.strip()
        if not _EMAIL.fullmatch(email) or not name:
            raise HTTPException(422, "A valid email and display name are required.")
        try:
            user = store.create_user("breakroom:development", email, email, name)
        except ValueError as exc:
            raise HTTPException(409, "This local identity could not be linked to an account.") from exc
        response = JSONResponse({})
        csrf = _start_session(request, response, settings, store, user)
        response.body = JSONResponse({"user": {key: user[key] for key in ("id", "email", "display_name")}, "csrf_token": csrf}).body
        response.headers["content-length"] = str(len(response.body))
        return response

    @router.post("/logout")
    async def logout(request: Request):
        _require_origin(request, settings)
        session = principal(request, store)
        _require_csrf(request, session)
        store.delete_session(token_hash(request.cookies[SESSION_COOKIE]))
        response = JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})
        response.delete_cookie(SESSION_COOKIE, path=SESSION_PATH, httponly=True, secure=_secure(settings), samesite="lax")
        return response

    app.include_router(router)
    return router
