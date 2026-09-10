# Customer-owned runs and explicit report sharing

Breakroom adapters execute on your machine or your CI worker as trusted Python code. A subprocess is not a security sandbox. Model/network use remains an explicit local choice; the team service receives report data and never executes your adapter. Running, comparing, exporting or preparing a report does not upload it.

## Prepare locally, review, then send

Run the local adapter and keep its original bundle on your machine. This example uses the scripted reference agent and simulated business records:

```sh
breakroom run --agent examples.agents.corrected:run --pack support-refunds --out ./artifacts/candidate
breakroom prepare-upload ./artifacts/candidate --case refund-response-lost --out ./artifacts/prepared-report.json
```

`prepare-upload` selects a recorded trial; `--trial 2` selects the second occurrence of the selected case. It creates a random 32-byte `.breakroom/redaction.key` with owner-only permissions if absent and reuses it thereafter. `.breakroom/` is ignored by this repository. Use `--redaction-key-file /private/project/key` to manage a key elsewhere. Keep it private and backed up if stable comparisons matter; do not upload it, commit it or pass it to a browser. Use the same project-specific key when preparing baseline and candidate reports. Changing keys deliberately changes identifiers and fixture hashes, making those sharing copies incompatible for comparison.

Open the prepared JSON and review it. The command prints its canonical upload SHA-256. Review the remaining amounts, currencies, record counts, timing, relationships, structured effects, check results and source hashes. These can reveal business information even when free text is absent. Pseudonymous identifiers can link related reports prepared with the same key. Automated minimization is imperfect and is not a confidentiality certification.

After review, select the owner-configured HTTPS origin and project. Supply a project-scoped `reports:write` API key through your secret manager or environment, named `BREAKROOM_API_TOKEN` by default. Never put the secret in command arguments, shell history, URLs, source files or logs.

```sh
breakroom upload-report ./artifacts/prepared-report.json --server https://YOUR_TEAM_ORIGIN --project YOUR_PROJECT_ID
```

`--token-env ANOTHER_SECRET_VARIABLE` selects a different environment variable. `--expected-sha256 REVIEWED_CANONICAL_CHECKSUM` additionally refuses to send a changed file. For the explicitly enabled local Compose team service, use `--server http://127.0.0.1:3000 --allow-localhost-http`; the website proxies `/api/team` and the API container has no host port. Port 8001 applies only when an operator separately starts the manual host API service. Remote HTTP, origin credentials, origin paths, queries and fragments are rejected. The CLI uses verified HTTPS, does not follow redirects, does not inherit proxy routing, and never retries automatically. A 10-second timeout applies to blocking network operations; this is not a guaranteed total wall-clock deadline against a peer that continuously streams data. Responses are limited to 64 KiB. If the result is uncertain, retry the same reviewed file after checking the server; its canonical checksum is the idempotency key.

## What the sharing copy contains

Only `breakroom-minimized-v1` is supported. There is no raw-upload switch. Raw messages, task text, customer display names, contacts, detailed check explanations, full tool arguments/payloads, adapter paths, source code and provider configuration are removed or replaced. Record IDs, event IDs, evidence references, operation keys, tenant references and private case IDs become stable HMAC pseudonyms. The 24 public built-in case IDs remain readable. The transformation retains allowlisted structured fields, integer amounts/currency, state, check statuses, numeric versions, fixture policies and bounded fault behavior. Missing state and metrics remain unknown; the upload does not turn them into zero or a pass.

The envelope is JSON with exactly these top-level fields:

```json
{
  "schema_version": "1.0",
  "kind": "customer_generated_report",
  "report": {},
  "privacy": {
    "format": "breakroom-minimized-v1",
    "normalization": "redacted_materialized_snapshot",
    "original_report_sha256": "64 lowercase hexadecimal characters",
    "original_case_manifest_hash": "64 lowercase hexadecimal characters",
    "original_fixture_hash": "64 lowercase hexadecimal characters",
    "original_source_manifest_hash": null,
    "original_generator_version": null,
    "notice": "Customer-generated, locally minimized evidence. Original hashes are customer-supplied provenance, not independent certification. Automated minimization is imperfect; review before uploading."
  }
}
```

This sketch omits the required report body. The CLI supplies a complete valid core report using the same schema/version fields as local reports. Original source/generator provenance is populated when present. Those original hashes are customer-supplied assertions: the server cannot verify an unavailable original or certify that a run happened honestly.

The nested report contains the **redacted materialized snapshot** with newly computed manifest and fixture hashes. It omits the original source manifest and generator fields; changing identifiers and text does not reproduce the original generator output. Keep the full original bundle for runnable `export-case` regressions. A team JSON export is a sharing artifact, not a faithful executable regression fixture.

## Team protocol and bounds

The CLI posts the envelope directly to `/api/team/projects/{project_id}/reports` with bearer authorization and a 64-character lowercase hexadecimal `Idempotency-Key`. The server independently hashes canonical JSON and validates the complete report and minimization format. It rejects extra/raw fields even if the caller claims that redaction occurred. Uploads are at most 2 MiB, bounded JSON only, with no archives, executable manifests or fetched URLs/paths. Ownership and scope checks happen server-side; report-provided tenant identifiers confer no access.

A new upload returns HTTP 201. Repeating the same key and content returns HTTP 200 with the same report ID and `duplicate: true`; reusing a key for different content returns 409. Stored evidence is labeled `customer_generated`. Project membership controls list, detail, nested evidence, compare and JSON export. The service reports each record's expiry; deletion and backup behavior are described by the configured team service. Private reports and cases are never automatically contributed to a public library.

## Owner-controlled CI example

[`examples/ci/upload-reviewed.yml.example`](../examples/ci/upload-reviewed.yml.example) is an inactive manual workflow example. It is outside `.github/workflows`, has no schedule, no push or pull-request trigger, no repository token permissions, and checks out no code. It expects an owner-managed worker with a reviewed Breakroom installation and a previously prepared report. Preparation and human review happen before dispatch. The operator enters the reviewed checksum, and the upload refuses changed data. It never generates a new report and calls that unseen file reviewed.

Before adopting it, the owner configures a dedicated trusted worker, the `breakroom-report-upload` environment and any required reviewers, an HTTPS server origin, project ID and scoped secret. The owner must review the exact YAML and activate it themselves. It has not been enabled or executed on a remote CI service. A workflow environment name alone does not configure review protection. Avoid sharing a secret-bearing worker with untrusted pull-request jobs.

Implementation references checked 2026-09-07: Python's [urllib handlers and timeouts](https://docs.python.org/3/library/urllib.request.html), GitHub's [manual workflow dispatch](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow) and [workflow permissions syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax).

## Local verification

On 2026-09-07, `venv/bin/python tests/api/smoke_team_upload_http.py --origin http://127.0.0.1:3000 --web-origin http://127.0.0.1:3000` passed against the running Compose/PostgreSQL service. It ran the real prepare/upload CLI for faulty and corrected reference reports, retained two reports after an idempotent retry, compared compatible evidence with two versus one actual refunds, read nested evidence and JSON export, rejected the revoked upload key, then deleted the disposable project. The bounded result is saved at `artifacts/uploads/http-smoke.json`; no API key is persisted there.

`venv/bin/python tests/api/smoke_upload_redirect_http.py` separately passed over real localhost sockets: one request reached the redirecting server and zero reached the destination. The 17 upload contract tests also passed on Python 3.12 and 3.14. These local checks do not establish production OIDC, external HTTPS infrastructure or hosted availability.
