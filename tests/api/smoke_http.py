"""Explicit localhost HTTP smoke test; starts no service and needs no credentials."""

import json
from pathlib import Path
from urllib.request import Request, urlopen

BASE = "http://127.0.0.1:8000"


def request(path, body=None):
    data = None if body is None else json.dumps(body).encode()
    with urlopen(Request(BASE + path, data=data, headers={"Content-Type": "application/json"}), timeout=10) as response:
        return response.status, json.loads(response.read(2_000_001))


def main():
    destination = Path(__file__).resolve().parents[2] / "artifacts" / "api-smoke"
    destination.mkdir(parents=True, exist_ok=True)
    status, health = request("/health")
    assert status == 200 and health["status"] == "ok"
    print("health: HTTP 200; synthetic built-in demo")
    runs = []
    for agent, verdict, count, amount in (("faulty", "FAIL", 2, 200000), ("corrected", "PASS", 1, 100000)):
        status, envelope = request("/api/runs", {"agent": agent, "case_id": "refund-response-lost"})
        report = envelope["report"]
        refunds = report["final_state"]["refunds"]
        assert status == 201
        assert report["execution"]["status"] == "completed"
        assert report["verdict"] == verdict
        assert len(refunds) == count and sum(refund["amount_minor"] for refund in refunds) == amount
        assert all(refund["currency"] == "INR" for refund in refunds)
        (destination / f"{agent}.json").write_text(json.dumps(envelope, indent=2) + "\n")
        print(f"{agent}: HTTP 201, execution completed, {verdict}, {count} refunds totaling {amount} INR minor units")
        runs.append(envelope)
    status, comparison = request("/api/compare", {"baseline_id": runs[0]["id"], "candidate_id": runs[1]["id"]})
    assert status == 200 and comparison["compatible"] is True
    assert comparison["pairs"][0]["baseline_verdict"] == "FAIL"
    assert comparison["pairs"][0]["candidate_verdict"] == "PASS"
    (destination / "comparison.json").write_text(json.dumps(comparison, indent=2) + "\n")
    print("comparison: HTTP 200, compatible, FAIL → PASS")
    with urlopen(BASE + "/api/runs/" + runs[0]["id"] + "/export", timeout=10) as response:
        assert response.status == 200 and response.headers["content-type"] == "application/zip"
        payload = response.read(2_000_001)
        assert len(payload) <= 2_000_000 and payload.startswith(b"PK")
        (destination / "breakroom-regression.zip").write_bytes(payload)
        print(f"export: HTTP 200, application/zip, {len(payload)} bytes")
    print(f"Saved evidence: {destination}")


if __name__ == "__main__":
    main()
