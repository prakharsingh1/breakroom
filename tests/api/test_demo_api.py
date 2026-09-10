"""The flagship API tests execute the actual subprocess runner and SQLite engine."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
import subprocess
import sys
import threading
from zipfile import ZipFile

from fastapi.testclient import TestClient
import pytest

from breakroom_api import main
from breakroom_api.main import CASE_IDS, CATALOG_IDS, DemoStore, RequestBounds, Settings, create_app


@pytest.fixture
def client():
    with TestClient(create_app()) as client:
        yield client


def execute(client, agent="corrected", case_id="refund-response-lost"):
    response = client.post("/api/runs", json={"agent": agent, "case_id": case_id})
    assert response.status_code == 201, response.text
    return response.json()


def successful_refunds(report):
    return [refund for refund in report["final_state"]["refunds"] if refund["status"] == "succeeded"]


def test_drills_are_fixed_actual_versioned_manifests(client):
    assert client.get("/health").json()["mode"] == "synthetic_builtin_demo"
    drills = client.get("/api/drills").json()["drills"]
    assert [case["case_id"] for case in drills] == list(CATALOG_IDS)
    assert len(drills) == 24
    assert [case["case_id"] for case in drills if case["demo_available"]] == list(CASE_IDS)
    assert all(case["status"] == "implemented" and case["provenance"] == "synthetic" for case in drills)
    assert client.get("/api/drills/refund-response-lost").json() == drills[1]
    assert client.get("/api/drills/not-a-built-in").status_code == 422
    assert client.get("/api/drills/pending-refund-succeeds").json()["demo_available"] is False
    assert client.post("/api/runs", json={"agent": "corrected", "case_id": "pending-refund-succeeds"}).status_code == 422


def test_flagship_runs_compare_real_effects_and_are_isolated(client):
    baseline = execute(client, "faulty")
    candidate = execute(client, "corrected")
    assert baseline["source"] == candidate["source"] == "live_builtin"
    assert baseline["id"] != candidate["id"]
    faulty, corrected = baseline["report"], candidate["report"]
    assert faulty["execution"]["status"] == corrected["execution"]["status"] == "completed"
    assert faulty["verdict"] == "FAIL"
    assert corrected["verdict"] == "PASS"
    assert faulty["initial_state"]["refunds"] == corrected["initial_state"]["refunds"] == []
    assert len(successful_refunds(faulty)) == 2
    assert len(successful_refunds(corrected)) == 1
    assert sum(refund["amount_minor"] for refund in successful_refunds(faulty)) == 200000
    assert sum(refund["amount_minor"] for refund in successful_refunds(corrected)) == 100000
    assert all(refund["currency"] == "INR" for refund in successful_refunds(faulty))
    assert any(check["status"] == "fail" for check in faulty["checks"])
    assert client.get(f'/api/runs/{baseline["id"]}').json() == baseline
    comparison = client.post("/api/compare", json={"baseline_id": baseline["id"], "candidate_id": candidate["id"]})
    assert comparison.status_code == 200, comparison.text
    result = comparison.json()
    assert result["compatible"] is True
    assert result["pairs"][0]["baseline_verdict"] == "FAIL"
    assert result["pairs"][0]["candidate_verdict"] == "PASS"
    assert result["pairs"][0]["changed_checks"]
    assert result["pairs"][0]["changed_effects"]["baseline"] == faulty["final_state"]


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_corrected_reference_completes_all_public_drills(client, case_id):
    run = execute(client, case_id=case_id)
    assert run["report"]["verdict"] == "PASS", run["report"]["checks"]


@pytest.mark.parametrize("body", [
    {"agent": "os:system", "case_id": "refund-response-lost"},
    {"agent": "corrected", "case_id": "../../secret.json"},
    {"agent": "corrected", "case_id": "http://169.254.169.254/"},
    {"agent": "corrected", "case_id": "refund-response-lost", "source": "print('SECRET')"},
    {"agent": "corrected", "case_id": "refund-response-lost", "url": "http://localhost:22"},
    {"agent": "corrected", "case_id": "refund-response-lost", "command": ["sh", "-c", "true"]},
    {"agent": "corrected", "case_id": "refund-response-lost", "timeout": 99999},
    {"agent": "corrected", "case_id": "refund-response-lost", "env_allowlist": ["SECRET"]},
    {"agent": 1, "case_id": "refund-response-lost"},
    {"agent": "corrected", "case_id": "concurrent-request"},
])
def test_arbitrary_execution_inputs_rejected_without_invocation(client, monkeypatch, body):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid public input reached the worker")
    monkeypatch.setattr(main, "run_case", forbidden)
    response = client.post("/api/runs", json=body)
    assert response.status_code == 422
    assert "SECRET" not in response.text


def test_comparison_rejects_incompatible_cases(client):
    baseline = execute(client, case_id="normal-refund")
    candidate = execute(client, case_id="refund-response-lost")
    result = client.post("/api/compare", json={"baseline_id": baseline["id"], "candidate_id": candidate["id"]})
    assert result.status_code == 409
    assert result.json()["detail"]["compatible"] is False


def test_export_download_is_runnable_and_detects_regression(client, tmp_path):
    baseline = execute(client, "faulty")
    response = client.get(f'/api/runs/{baseline["id"]}/export')
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert "attachment" in response.headers["content-disposition"]
    with ZipFile(BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == {
            f"breakroom-regression/{name}" for name in ("case.json", "test_regression.py", "README.md", "requirements.txt")
        }
        archive.extractall(tmp_path)
    script = tmp_path / "breakroom-regression" / "test_regression.py"
    corrected = subprocess.run([sys.executable, str(script), "--agent", "breakroom.agents:corrected"], capture_output=True, text=True, timeout=15)
    assert corrected.returncode == 0, corrected.stdout + corrected.stderr
    faulty = subprocess.run([sys.executable, str(script), "--agent", "breakroom.agents:faulty"], capture_output=True, text=True, timeout=15)
    assert faulty.returncode == 1, faulty.stdout + faulty.stderr
    assert "FAIL" in faulty.stdout + faulty.stderr


def test_body_bounds_include_missing_content_length_and_invalid_length(client):
    assert client.post("/api/runs", content=b"x" * 4097).status_code == 413
    assert client.post("/api/runs", content=iter([b"x" * 3000, b"x" * 3000])).status_code == 413
    assert client.post("/api/runs", content="{}", headers={"content-length": "unknown"}).status_code == 400
    assert client.post("/api/runs", content="{}", headers={"content-length": "-1"}).status_code == 400
    assert client.post("/api/runs", content="{", headers={"content-type": "application/json"}).status_code == 422
    assert client.post("/api/runs", content='{"agent":"faulty","agent":"corrected","case_id":"normal-refund"}', headers={"content-type": "application/json"}).status_code == 422
    assert client.post("/api/runs", content="[" * 1500 + "]" * 1500, headers={"content-type": "application/json"}).status_code == 422


def test_api_does_not_reflect_unknown_sensitive_values(client):
    response = client.post("/api/runs", json={"agent": "corrected", "case_id": "refund-response-lost", "token": "TOP_SECRET_DO_NOT_REFLECT"})
    assert response.status_code == 422
    assert "TOP_SECRET_DO_NOT_REFLECT" not in response.text


def test_builtin_case_cannot_be_shadowed_by_a_same_name_local_path(client, tmp_path, monkeypatch):
    (tmp_path / "refund-response-lost").write_text('{"not":"a built-in manifest"}')
    monkeypatch.chdir(tmp_path)
    response = client.get("/api/drills/refund-response-lost")
    assert response.status_code == 200
    assert response.json()["case_id"] == "refund-response-lost"
    assert execute(client)["report"]["verdict"] == "PASS"


def test_rate_limit_ignores_forged_forwarding_headers():
    with TestClient(create_app(Settings(requests_per_minute=1))) as client:
        assert client.get("/api/drills", headers={"X-Forwarded-For": "192.0.2.1"}).status_code == 200
        response = client.get("/api/drills", headers={"X-Forwarded-For": "192.0.2.2"})
        assert response.status_code == 429
        assert response.headers["retry-after"] == "60"
        assert client.get("/health").status_code == 200


def test_run_rate_is_separate_from_read_rate():
    with TestClient(create_app(Settings(runs_per_minute=1))) as client:
        execute(client)
        assert client.get("/api/drills").status_code == 200
        assert client.post("/api/runs", json={"agent": "corrected", "case_id": "normal-refund"}).status_code == 429


def test_ephemeral_evidence_capacity_and_expiry():
    clock = [100.0]
    app = create_app(Settings(ttl_seconds=10, max_reports=1), clock=lambda: clock[0])
    with TestClient(app) as client:
        run = execute(client)
        assert client.post("/api/runs", json={"agent": "corrected", "case_id": "normal-refund"}).status_code == 503
        clock[0] += 11
        assert client.get(f'/api/runs/{run["id"]}').status_code == 404
        assert client.get(f'/api/runs/{run["id"]}/export').status_code == 404
        assert not app.state.demo_store.reports
        assert execute(client)["id"] != run["id"]
    assert not app.state.demo_store.reports
    assert not app.state.demo_store.rate


def test_worker_concurrency_is_bounded(client, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    actual_runner = main.run_case

    def delayed(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return actual_runner(*args, **kwargs)

    monkeypatch.setattr(main, "run_case", delayed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(execute, client)
        assert entered.wait(5)
        try:
            response = client.post("/api/runs", json={"agent": "corrected", "case_id": "normal-refund"})
            assert response.status_code == 503
            assert "busy" in response.json()["detail"]
        finally:
            release.set()
        assert pending.result(timeout=10)["report"]["verdict"] == "PASS"


def test_worker_fixed_arguments_and_environment(client, monkeypatch):
    actual_runner = main.run_case
    observed = []

    def recording(case, agent, **kwargs):
        observed.append((case, agent, kwargs))
        return actual_runner(case, agent, **kwargs)

    monkeypatch.setattr(main, "run_case", recording)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "this-must-not-reach-the-worker")
    report = execute(client)["report"]
    assert observed[0][1] == "breakroom.agents:corrected"
    assert observed[0][2] == {"seed": 0, "timeout": 5, "allow_network": False, "env_allowlist": ()}
    assert "this-must-not-reach-the-worker" not in json.dumps(report)


def test_output_limits_are_errors_and_failed_worker_releases_capacity(monkeypatch):
    with TestClient(create_app(Settings(report_bytes=10))) as client:
        response = client.post("/api/runs", json={"agent": "corrected", "case_id": "normal-refund"})
        assert response.status_code == 502
        assert "size limit" in response.json()["detail"]
    actual_runner = main.run_case
    with TestClient(create_app()) as client:
        def failure(*args, **kwargs):
            raise RuntimeError("secret /private/local/path")
        monkeypatch.setattr(main, "run_case", failure)
        response = client.post("/api/runs", json={"agent": "corrected", "case_id": "normal-refund"})
        assert response.status_code == 502
        assert "secret" not in response.text
        monkeypatch.setattr(main, "run_case", actual_runner)
        execute(client)


def test_cors_is_restricted_and_evidence_is_not_cached(client):
    response = client.get("/api/drills", headers={"Origin": "https://untrusted.example"})
    assert "access-control-allow-origin" not in response.headers
    assert response.headers["cache-control"] == "no-store"
    allowed = client.get("/api/drills", headers={"Origin": "http://localhost:3000"})
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_rate_client_memory_is_bounded_and_expires():
    clock = [100.0]
    store = DemoStore(Settings(max_rate_clients=1), lambda: clock[0])
    assert store.check_rate("first", False) is None
    assert store.check_rate("second", False) == 503
    clock[0] += 61
    assert store.check_rate("second", False) is None
    assert len(store.rate) == 1


def test_slow_request_body_has_a_deadline():
    async def exercise():
        async def downstream(*args):
            pytest.fail("an incomplete body reached the application")
        async def receive():
            await asyncio.sleep(1)
        messages = []
        async def send(message):
            messages.append(message)
        middleware = RequestBounds(downstream, DemoStore(Settings(body_timeout_seconds=0.01), lambda: 0))
        await middleware({"type": "http", "path": "/api/runs", "method": "POST", "headers": [], "client": ("test", 0)}, receive, send)
        assert messages[0]["status"] == 408
    asyncio.run(exercise())
