"""Explicit localhost-only team upload smoke. Requires enabled development login.

Creates a disposable project and scoped key, invokes the real upload CLI against
the running PostgreSQL-backed service, verifies effects/ownership/revocation,
then deletes the project. No credential is printed or persisted.
"""
from __future__ import annotations

import argparse
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener
import uuid

from breakroom.reports import read_json, write_bundle
from breakroom.runner import run_case
from breakroom.scenarios import load_case
from breakroom.uploads import _NoRedirect, upload_digest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--origin", default="http://127.0.0.1:3000")
    parser.add_argument("--web-origin", default="http://127.0.0.1:3000")
    parser.add_argument("--out", type=Path, default=Path("artifacts/uploads/http-smoke.json"))
    args = parser.parse_args()
    for value in (args.origin, args.web_origin):
        parsed = urlsplit(value)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.path or parsed.query or parsed.fragment or parsed.username:
            raise ValueError("This smoke is restricted to explicit localhost HTTP origins.")
    opener = build_opener(ProxyHandler({}), _NoRedirect(), HTTPCookieProcessor(CookieJar()))
    csrf = None

    def request(method, route, data=None):
        headers = {"Origin": args.web_origin, "Accept": "application/json"}
        if csrf:
            headers["X-CSRF-Token"] = csrf
        body = None
        if data is not None:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
        with opener.open(Request(args.origin + "/api/team" + route, data=body, method=method, headers=headers), timeout=10) as response:
            content = response.read(2 * 1024 * 1024 + 1)
            assert len(content) <= 2 * 1024 * 1024
            return response.status, json.loads(content)

    status, identity = request("POST", "/auth/dev-login", {"email": f"cli-smoke-{uuid.uuid4().hex}@example.invalid", "display_name": "Disposable CLI smoke"})
    assert status == 200
    csrf = identity["csrf_token"]
    status, project = request("POST", "/projects", {"name": "Disposable explicit CLI upload", "retention_days": 1})
    assert status == 201
    route = "/projects/" + project["id"]
    result = {"origin": args.origin, "provenance": "customer_generated", "synthetic": True}
    try:
        status, key = request("POST", route + "/keys", {"name": "Disposable smoke key", "scopes": ["reports:write", "reports:read"], "expires_days": 1})
        assert status == 201
        env = os.environ.copy()
        env["BREAKROOM_SMOKE_UPLOAD_KEY"] = key["secret"]
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            files = []
            for agent in ("faulty", "corrected"):
                report = run_case(load_case("refund-response-lost"), "breakroom.agents:" + agent)
                assert report["verdict"] == ("FAIL" if agent == "faulty" else "PASS")
                bundle = base / agent
                write_bundle([report], bundle)
                prepared = base / f"{agent}.json"
                command = [sys.executable, "-m", "breakroom", "prepare-upload", str(bundle), "--case", "refund-response-lost", "--out", str(prepared), "--redaction-key-file", str(base / "key")]
                completed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=15)
                assert completed.returncode == 0, completed.stderr
                files.append(prepared)
            # These are explicitly reviewed synthetic reference fixtures. Bind
            # every network operation to their exact canonical prepared digest.
            def upload(path):
                reviewed = read_json(path)
                assert reviewed["report"]["case"]["case_id"] == "refund-response-lost"
                command = [sys.executable, "-m", "breakroom", "upload-report", str(path), "--server", args.origin,
                           "--project", project["id"], "--allow-localhost-http", "--token-env", "BREAKROOM_SMOKE_UPLOAD_KEY",
                           "--expected-sha256", upload_digest(reviewed)]
                completed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=15)
                assert key["secret"] not in completed.stdout + completed.stderr
                return completed

            for path in files:
                completed = upload(path)
                assert completed.returncode == 0, completed.stderr
                assert completed.stdout.startswith("Uploaded customer-generated")
            repeated = upload(files[0])
            assert repeated.returncode == 0 and repeated.stdout.startswith("Previously uploaded")
            _, listing = request("GET", route + "/reports")
            assert len(listing["items"]) == 2
            by_verdict = {item["verdict"]: item["id"] for item in listing["items"]}
            _, comparison = request("POST", route + "/compare", {"baseline_id": by_verdict["FAIL"], "candidate_id": by_verdict["PASS"]})
            assert comparison["compatible"] and comparison["provenance"] == "customer_generated"
            _, evidence = request("GET", route + "/reports/" + by_verdict["FAIL"] + "/evidence")
            assert len(evidence["final_state"]["refunds"]) == 2
            _, exported = request("GET", route + "/reports/" + by_verdict["PASS"] + "/export")
            assert len(exported["report"]["final_state"]["refunds"]) == 1
            request("DELETE", route + "/keys/" + key["id"])
            revoked = upload(files[1])
            assert revoked.returncode == 2 and ("401" in revoked.stderr or "403" in revoked.stderr)
            result.update(created_reports=2, duplicate_reused=True, compatible=True,
                          baseline_refunds=2, candidate_refunds=1, revoked_key_rejected=True)
    finally:
        _, deleted = request("DELETE", route)
        assert deleted["deleted"] is True
        result["project_deleted"] = True
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
