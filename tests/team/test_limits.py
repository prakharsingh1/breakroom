"""Request-budget and validation-privacy tests without a database or network."""
from __future__ import annotations

import asyncio
import json
import unittest

from fastapi import FastAPI, Request

from breakroom_api.team import LimitsMiddleware, create_app
from breakroom_api.team_config import TeamSettings


class NoDatabase:
    """Any accidental route/lifespan database access fails this isolated test."""
    def __getattr__(self, name):
        raise AssertionError("Request validation unexpectedly accessed the database: " + name)


def app_with_limits(settings):
    app, received = FastAPI(), []
    app.add_middleware(LimitsMiddleware, settings=settings)

    @app.api_route("/{path:path}", methods=["GET", "POST"])
    async def body_probe(request: Request, path: str):
        body = await request.body()
        received.append(body)
        return {"bytes": len(body)}

    return app, received


async def invoke(app, *, path="/arbitrary", raw_path=None, query=b"", chunks=None,
                 headers=None, receiver=None):
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "scheme": "http", "method": "POST", "path": path, "root_path": "",
        "raw_path": raw_path if raw_path is not None else path.encode(), "query_string": query,
        "client": ("127.0.0.1", 51000), "server": ("localhost", 3000),
        "headers": [(b"host", b"localhost:3000"), (b"content-type", b"application/json"),
                    (b"origin", b"http://localhost:3000"), *(headers or [])],
    }
    parts = list(chunks if chunks is not None else [b""])
    sent, receives = [], 0

    async def receive():
        nonlocal receives
        receives += 1
        if receiver is not None:
            return await receiver()
        if not parts:
            return {"type": "http.disconnect"}
        return {"type": "http.request", "body": parts.pop(0), "more_body": bool(parts)}

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    starts = [message for message in sent if message["type"] == "http.response.start"]
    if not starts:
        return {"status": None, "body": b"", "headers": {}, "receives": receives}
    return {
        "status": starts[0]["status"], "headers": dict(starts[0].get("headers", [])),
        "body": b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body"),
        "receives": receives,
    }


class RequestLimitsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.settings = TeamSettings(environment="test", dev_login=True, rate_limit_per_minute=10000)
        self.app, self.received = app_with_limits(self.settings)
        # Direct ASGI invocation deliberately does not send lifespan events.
        self.auth_app = create_app(self.settings, store=NoDatabase())

    async def test_invalid_json_is_rejected_before_auth_or_arbitrary_routes(self):
        payloads = (
            b'{"email":"one@example.invalid","email":"two@example.invalid"}',
            b'{"nested":{"secret":"first","secret":"second"}}',
            b'{"value":NaN}', b'{"value":Infinity}', b'{"value":-Infinity}',
            b'{"value":1e999}', b'{"value":-1e999}',
            b"[" * 65 + b"0" + b"]" * 65,
        )
        for app, path in ((self.app, "/arbitrary"),
                          (self.auth_app, "/api/team/auth/dev-login"),
                          (self.auth_app, "/route-that-does-not-exist")):
            for payload in payloads:
                with self.subTest(path=path, payload=payload[:60]):
                    response = await invoke(app, path=path, chunks=[payload])
                    self.assertEqual(response["status"], 422, response["body"])
                    self.assertIn(b"bounded finite JSON", response["body"])
        self.assertEqual(self.received, [])

    async def test_valid_depth_boundary_and_chunked_body_replay_are_accepted(self):
        body = b"[" * 64 + b"0" + b"]" * 64
        response = await invoke(self.app, chunks=[body[:20], body[20:75], body[75:]])
        self.assertEqual(response["status"], 200, response["body"])
        self.assertEqual(self.received, [body])
        self.assertEqual(json.loads(response["body"])["bytes"], len(body))
        self.assertEqual(response["headers"][b"cache-control"], b"no-store")
        self.assertEqual(response["headers"][b"x-content-type-options"], b"nosniff")

    async def test_url_length_boundaries_apply_before_reading_the_body(self):
        cases = (
            (b"/" + b"a" * 2047, b"", 200),
            (b"/" + b"a" * 2048, b"", 414),
            (b"/arbitrary", b"q=" + b"x" * 8190, 200),
            (b"/arbitrary", b"q=" + b"x" * 8191, 414),
        )
        for raw_path, query, expected in cases:
            with self.subTest(raw_path_length=len(raw_path), query_length=len(query)):
                response = await invoke(self.app, path=raw_path.decode(), raw_path=raw_path, query=query)
                self.assertEqual(response["status"], expected, response["body"])
                if expected == 414:
                    self.assertEqual(response["receives"], 0)

    async def test_raw_url_bytes_are_bounded_even_when_decoded_path_is_short(self):
        response = await invoke(self.app, path="/short", raw_path=b"/" + b"%61" * 683)
        self.assertEqual(response["status"], 414)
        self.assertEqual(response["receives"], 0)

    async def test_streamed_body_cannot_bypass_limit_with_missing_or_small_length(self):
        app, received = app_with_limits(TeamSettings(max_request_bytes=512))
        oversized = [b'"' + b"a" * 255, b"a" * 256 + b'"']
        for headers in ([], [(b"content-length", b"1")]):
            with self.subTest(headers=headers):
                response = await invoke(app, chunks=oversized, headers=headers)
                self.assertEqual(response["status"], 413, response["body"])
                self.assertEqual(response["receives"], 2)
        self.assertEqual(received, [])
        exact = b'"' + b"a" * 510 + b'"'
        response = await invoke(app, chunks=[exact[:256], exact[256:]])
        self.assertEqual(response["status"], 200)
        self.assertEqual(received, [exact])

    async def test_advertised_body_limit_rejects_before_receiving(self):
        app, received = app_with_limits(TeamSettings(max_request_bytes=512))
        response = await invoke(app, headers=[(b"content-length", b"513")])
        self.assertEqual(response["status"], 413)
        self.assertEqual(response["receives"], 0)
        self.assertEqual(received, [])

    async def test_body_deadline_returns_408_without_waiting_for_final_chunk(self):
        app, received = app_with_limits(TeamSettings(body_read_timeout_seconds=0.1))
        blocked, cancelled = asyncio.Event(), asyncio.Event()
        first = True

        async def slow_receive():
            nonlocal first
            if first:
                first = False
                return {"type": "http.request", "body": b"{", "more_body": True}
            blocked.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        # The outer timeout is a failure guard. Only middleware's independent
        # deadline can produce the asserted HTTP 408 and cancel the body wait.
        response = await asyncio.wait_for(invoke(app, receiver=slow_receive), timeout=2)
        self.assertEqual(response["status"], 408, response["body"])
        self.assertTrue(blocked.is_set())
        self.assertTrue(cancelled.is_set())
        self.assertEqual(received, [])

    async def test_body_deadline_covers_entire_stream_not_individual_reads(self):
        app, received = app_with_limits(TeamSettings(body_read_timeout_seconds=0.1))
        reads = 0

        async def trickle():
            nonlocal reads
            if reads:
                await asyncio.sleep(0.02)
            reads += 1
            return {"type": "http.request", "body": b" ", "more_body": True}

        response = await asyncio.wait_for(invoke(app, receiver=trickle), timeout=2)
        self.assertEqual(response["status"], 408, response["body"])
        self.assertGreater(reads, 0)
        self.assertEqual(received, [])

    async def test_pydantic_errors_do_not_echo_rejected_secret_values(self):
        secret = "sk-fixture-private-credential-never-reflect"
        payloads = (
            {"email": {"password": secret}},
            {"display_name": secret * 4},
            {"email": "owner@example.invalid", "unexpected": secret},
        )
        for payload in payloads:
            with self.subTest(payload=payload):
                response = await invoke(self.auth_app, path="/api/team/auth/dev-login",
                                        chunks=[json.dumps(payload).encode()])
                self.assertEqual(response["status"], 422, response["body"])
                self.assertNotIn(secret.encode(), response["body"])
                for detail in json.loads(response["body"])["detail"]:
                    self.assertNotIn("input", detail)
                    self.assertNotIn("ctx", detail)

    async def test_pydantic_errors_do_not_echo_secret_in_unknown_field_name(self):
        secret = "sk-fixture-private-credential-as-mistaken-field-name"
        response = await invoke(self.auth_app, path="/api/team/auth/dev-login",
                                chunks=[json.dumps({secret: "accidental input"}).encode()])
        self.assertEqual(response["status"], 422, response["body"])
        self.assertNotIn(secret.encode(), response["body"])


if __name__ == "__main__":
    unittest.main()
