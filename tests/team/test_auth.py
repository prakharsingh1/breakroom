"""Credential-free OIDC fixtures exercise real Authlib/JOSE verification."""
import copy
import threading
import time
import unittest
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
from authlib.oauth2.rfc7636 import create_s256_code_challenge
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from joserfc import jwt
from joserfc.jwk import RSAKey

from breakroom_api.team_auth import (LOGIN_COOKIE, LOGIN_PATH, SESSION_COOKIE,
    configure_auth, principal, token_hash, validate_auth_settings, verify_id_token)


class MemoryStore:
    def __init__(self):
        self.attempts, self.sessions, self.users = {}, {}, {}
        self.lock = threading.Lock()

    def create_login_attempt(self, state_hash, nonce, code_verifier, expires_at):
        self.attempts[state_hash] = dict(nonce=nonce, code_verifier=code_verifier, expires_at=expires_at)

    def consume_login_attempt(self, state_hash):
        with self.lock:
            return self.attempts.pop(state_hash, None)

    def create_user(self, issuer, subject, email, display_name):
        for user in self.users.values():
            if user["issuer"] == issuer and user["subject"] == subject:
                return copy.deepcopy(user)
            if user["email"] == email:
                raise ValueError("Identity conflict")
        user = dict(id=f"user_{len(self.users) + 1}", issuer=issuer, subject=subject,
                    email=email, display_name=display_name)
        self.users[user["id"]] = user
        return copy.deepcopy(user)

    def create_session(self, user_id, token_hash, csrf_token, expires_at):
        self.sessions[token_hash] = dict(user_id=user_id, token_hash=token_hash,
            csrf_token=csrf_token, expires_at=expires_at, user=self.users[user_id])

    def get_session(self, token_hash):
        return copy.deepcopy(self.sessions.get(token_hash))

    def delete_session(self, token_hash):
        self.sessions.pop(token_hash, None)


def settings(**overrides):
    return SimpleNamespace(**{**dict(environment="test", dev_login=False,
        public_origin="http://localhost:3000", session_ttl_seconds=86400,
        oidc_issuer="https://identity.example.invalid", oidc_client_id="breakroom-test",
        oidc_client_secret="fixture-secret", oidc_redirect_uri=None, secure_cookies=False), **overrides})


class TeamAuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.signing_key = RSAKey.generate_key(2048, parameters={"kid": "fixture-key"})
        cls.other_key = RSAKey.generate_key(2048, parameters={"kid": "fixture-key"})
        cls.jwks = {"keys": [cls.signing_key.as_dict(private=False)]}

    def setUp(self):
        self.store = MemoryStore()
        self.config = settings()
        self.authorization = None
        self.claim_updates = {}
        self.omit_claims = set()
        self.bad_signature = False
        self.token_requests = []
        self.discovery = {
            "issuer": self.config.oidc_issuer,
            "authorization_endpoint": self.config.oidc_issuer + "/authorize",
            "token_endpoint": self.config.oidc_issuer + "/token",
            "jwks_uri": self.config.oidc_issuer + "/keys",
            "id_token_signing_alg_values_supported": ["RS256"],
            "code_challenge_methods_supported": ["S256"],
        }

    def claims(self, nonce="fixture-nonce"):
        now = int(time.time())
        return dict(iss=self.config.oidc_issuer, aud=self.config.oidc_client_id,
            sub="subject_123", iat=now, exp=now + 300, nonce=nonce,
            email="owner@example.invalid", email_verified=True, name="Fixture owner")

    def sign(self, claims, *, wrong_key=False):
        return jwt.encode({"alg": "RS256", "kid": "fixture-key"}, claims,
                          self.other_key if wrong_key else self.signing_key)

    def provider(self, request):
        if request.url.path == "/.well-known/openid-configuration":
            return httpx.Response(200, json=self.discovery)
        if request.url.path == "/keys":
            return httpx.Response(200, json=self.jwks)
        if request.url.path == "/token":
            body = parse_qs(request.content.decode())
            self.token_requests.append(body)
            verifier = body.get("code_verifier", [""])[0]
            if (body.get("code") != ["fixture-code"] or not self.authorization
                    or create_s256_code_challenge(verifier) != self.authorization["code_challenge"][0]):
                return httpx.Response(400, json={"error": "invalid_grant"})
            claims = {**self.claims(self.authorization["nonce"][0]), **self.claim_updates}
            for key in self.omit_claims:
                claims.pop(key, None)
            return httpx.Response(200, json={"access_token": "fixture-access-token", "token_type": "Bearer",
                                           "id_token": self.sign(claims, wrong_key=self.bad_signature)})
        raise AssertionError("Unexpected provider request: " + str(request.url))

    def application(self, config=None):
        app = FastAPI()
        configure_auth(app, config or self.config, self.store, http_transport=httpx.MockTransport(self.provider))

        @app.get("/api/team/me")
        def me(request: Request):
            session = principal(request, self.store)
            return {"user": session["user"], "csrf_token": session["csrf_token"]}

        return app

    def client(self, config=None, **kwargs):
        return TestClient(self.application(config), base_url="http://localhost:3000",
                          client=kwargs.pop("client", ("127.0.0.1", 50000)), **kwargs)

    def begin(self, client):
        response = client.get(LOGIN_PATH + "/login", follow_redirects=False)
        self.assertEqual(response.status_code, 302, response.text)
        self.authorization = parse_qs(urlsplit(response.headers["location"]).query)
        return self.authorization["state"][0]

    def callback(self, client, state, **kwargs):
        return client.get(LOGIN_PATH + "/callback", params={"state": state, "code": "fixture-code"},
                          follow_redirects=False, **kwargs)

    def test_complete_oidc_flow_uses_pkce_and_only_persists_session_digest(self):
        with self.client() as client:
            state = self.begin(client)
            attempt = self.store.attempts[token_hash(state)]
            self.assertEqual(self.authorization["code_challenge_method"], ["S256"])
            self.assertEqual(self.authorization["code_challenge"], [create_s256_code_challenge(attempt["code_verifier"])])
            self.assertNotEqual(state, attempt["nonce"])
            self.assertNotIn(state, self.store.attempts)
            response = self.callback(client, state)
            self.assertEqual(response.status_code, 303, response.text)
            self.assertEqual(response.headers["location"], "http://localhost:3000/projects")
            self.assertEqual(self.store.attempts, {})
            token = client.cookies.get(SESSION_COOKIE)
            self.assertIn(token_hash(token), self.store.sessions)
            self.assertNotIn(token, self.store.sessions)
            self.assertNotIn("fixture-access-token", str(self.store.sessions))
            self.assertEqual(client.get("/api/team/me").status_code, 200)
            self.assertIn("HttpOnly", response.headers["set-cookie"])
            self.assertIn("SameSite=lax", response.headers["set-cookie"])
            self.assertEqual(response.headers["cache-control"], "no-store")

    def test_issuer_audience_nonce_signature_and_email_negatives_reject_callback(self):
        mutations = ({"iss": "https://wrong.example.invalid"}, {"aud": "other-client"},
                     {"iss": [self.config.oidc_issuer]}, {"aud": [self.config.oidc_client_id, 1]},
                     {"nonce": "wrong-nonce"}, {"email_verified": False},
                     {"exp": int(time.time()) - 300}, {"iat": int(time.time()) + 300},
                     {"aud": [self.config.oidc_client_id, "other-client"]},
                     {"azp": "other-client"}, {"email_verified": "true"})
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.client() as client:
                self.claim_updates = mutation
                state = self.begin(client)
                response = self.callback(client, state)
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(self.store.sessions, {})
                self.assertNotIn("fixture-secret", response.text)
        self.claim_updates = {}
        self.bad_signature = True
        with self.client() as client:
            self.assertEqual(self.callback(client, self.begin(client)).status_code, 400)
            self.assertEqual(self.store.sessions, {})

    def test_missing_essential_oidc_claims_are_rejected(self):
        for field in ("iss", "aud", "sub", "exp", "iat", "nonce", "email_verified"):
            with self.subTest(field=field), self.client() as client:
                self.omit_claims = {field}
                self.assertEqual(self.callback(client, self.begin(client)).status_code, 400)
                self.assertEqual(self.store.sessions, {})

    def test_state_is_bound_to_browser_and_replay_is_rejected(self):
        with self.client() as first, self.client() as other:
            state = self.begin(first)
            self.assertEqual(self.callback(other, state).status_code, 400)
            self.assertEqual(self.callback(first, "x" * 43).status_code, 400)
            self.assertEqual(self.token_requests, [])
            self.assertEqual(self.callback(first, state).status_code, 303)
            replay = self.callback(first, state, headers={"cookie": f"{LOGIN_COOKIE}={state}"})
            self.assertEqual(replay.status_code, 400)
            self.assertEqual(len(self.token_requests), 1)

    def test_expired_attempt_is_consumed_before_token_exchange(self):
        with self.client() as client:
            state = self.begin(client)
            self.store.attempts[token_hash(state)]["expires_at"] = time.time() - 1
            self.assertEqual(self.callback(client, state).status_code, 400)
            self.assertEqual(self.token_requests, [])
            self.assertEqual(self.store.attempts, {})

    def test_wrong_pkce_verifier_is_rejected_by_fixture_provider(self):
        with self.client() as client:
            state = self.begin(client)
            self.store.attempts[token_hash(state)]["code_verifier"] = "wrong" * 20
            self.assertEqual(self.callback(client, state).status_code, 400)
            self.assertEqual(len(self.token_requests), 1)
            self.assertEqual(self.store.sessions, {})

    def test_discovery_issuer_and_pkce_requirements_are_checked(self):
        for update in ({"issuer": "https://other.example.invalid"},
                       {"code_challenge_methods_supported": ["plain"]},
                       {"token_endpoint": "http://remote.example.invalid/token"}):
            with self.subTest(update=update), self.client() as client:
                original = self.discovery
                self.discovery = {**original, **update}
                try:
                    self.assertEqual(client.get(LOGIN_PATH + "/login").status_code, 503)
                    self.assertEqual(self.store.attempts, {})
                finally:
                    self.discovery = original

    def test_session_expiration_rotation_and_logout_csrf(self):
        config = settings(dev_login=True, oidc_issuer=None, oidc_client_id=None, oidc_client_secret=None)
        with self.client(config) as client:
            headers = {"origin": config.public_origin}
            response = client.post(LOGIN_PATH + "/dev-login", json={"email": "local@example.invalid"}, headers=headers)
            self.assertEqual(response.status_code, 200)
            first = client.cookies.get(SESSION_COOKIE)
            response = client.post(LOGIN_PATH + "/dev-login", json={"email": "local@example.invalid"}, headers=headers)
            second = client.cookies.get(SESSION_COOKIE)
            self.assertNotEqual(first, second)
            self.assertNotIn(token_hash(first), self.store.sessions)
            self.assertEqual(client.post(LOGIN_PATH + "/logout", headers=headers).status_code, 403)
            csrf = response.json()["csrf_token"]
            bad_origin = {"origin": "https://attacker.example.invalid", "x-csrf-token": csrf}
            self.assertEqual(client.post(LOGIN_PATH + "/logout", headers=bad_origin).status_code, 403)
            self.assertEqual(client.post(LOGIN_PATH + "/logout", headers={**headers, "x-csrf-token": csrf}).status_code, 200)
            self.assertEqual(client.get("/api/team/me").status_code, 401)
            client.post(LOGIN_PATH + "/dev-login", json={}, headers=headers)
            token = client.cookies.get(SESSION_COOKIE)
            self.store.sessions[token_hash(token)]["expires_at"] = time.time() - 1
            self.assertEqual(client.get("/api/team/me").status_code, 401)
            self.assertNotIn(token_hash(token), self.store.sessions)

    def test_development_login_requires_flag_local_host_origin_and_peer(self):
        with self.client() as client:
            self.assertEqual(client.post(LOGIN_PATH + "/dev-login", json={}).status_code, 404)
        config = settings(dev_login=True)
        with self.client(config) as client:
            self.assertEqual(client.post(LOGIN_PATH + "/dev-login", json={}).status_code, 403)
            response = client.post(LOGIN_PATH + "/dev-login", json={}, headers={"origin": config.public_origin, "host": "remote.example.invalid"})
            self.assertEqual(response.status_code, 404)
        with self.client(config, client=("203.0.113.20", 50000)) as client:
            self.assertEqual(client.post(LOGIN_PATH + "/dev-login", json={}, headers={"origin": config.public_origin}).status_code, 404)

    def test_local_proxy_requires_explicit_secret_and_ignores_forwarded_for(self):
        secret = "fixture-local-proxy-secret-" * 2
        config = settings(dev_login=True, trusted_proxy_secret=secret)
        with self.client(config, client=("172.18.0.2", 50000)) as client:
            headers = {"origin": config.public_origin, "x-forwarded-for": "127.0.0.1"}
            self.assertEqual(client.post(LOGIN_PATH + "/dev-login", json={}, headers=headers).status_code, 404)
            self.assertEqual(client.post(LOGIN_PATH + "/dev-login", json={}, headers={**headers, "x-breakroom-local-proxy": "wrong"}).status_code, 404)
            headers["x-breakroom-local-proxy"] = secret
            self.assertEqual(client.post(LOGIN_PATH + "/dev-login", json={}, headers=headers).status_code, 200)
            headers["origin"] = "https://attacker.example.invalid"
            self.assertEqual(client.post(LOGIN_PATH + "/dev-login", json={}, headers=headers).status_code, 403)

    def test_empty_nonce_cannot_disable_oidc_nonce_validation(self):
        with self.assertRaises(ValueError):
            verify_id_token(self.sign(self.claims()), self.jwks, issuer=self.config.oidc_issuer,
                            client_id=self.config.oidc_client_id, nonce="")

    def test_production_rejects_dev_bypass_and_insecure_settings_at_startup(self):
        for config in (settings(environment="production", dev_login=True, public_origin="https://app.example.invalid"),
                       settings(environment="production"), settings(dev_login=True, public_origin="https://app.example.invalid"),
                       settings(oidc_client_id=None)):
            with self.subTest(config=config), self.assertRaises(ValueError):
                validate_auth_settings(config)

    def test_https_production_sets_secure_cookies(self):
        self.config = settings(environment="production", public_origin="https://app.example.invalid")
        with TestClient(self.application(), base_url=self.config.public_origin) as client:
            state = self.begin(client)
            response = self.callback(client, state)
            self.assertEqual(response.status_code, 303, response.text)
            self.assertIn("Secure", response.headers["set-cookie"])

    def test_unconfigured_oidc_is_honestly_unavailable(self):
        config = settings(oidc_issuer=None, oidc_client_id=None, oidc_client_secret=None)
        with self.client(config) as client:
            self.assertEqual(client.get(LOGIN_PATH + "/login").status_code, 503)


if __name__ == "__main__":
    unittest.main()
