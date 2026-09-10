# Breakroom local runner

The Python core runs without a cloud account, Docker, model API key, or database service. Tested editable installation uses Python 3.12; supported Python versions are 3.11–3.14 on macOS/Linux. A local Linux container is the current Windows route. No public package namespace is claimed: install this repository, not an unrelated public `breakroom` package.

From the repository root, with a Python virtual environment activated:

```sh
python -m pip install -e ./packages/breakroom-core
breakroom doctor
breakroom demo --output ./artifacts/demo
breakroom run --agent examples.agents.faulty:run --pack support-refunds --out ./artifacts/baseline
breakroom run --agent examples.agents.corrected:run --pack support-refunds --out ./artifacts/candidate
breakroom compare ./artifacts/baseline ./artifacts/candidate
breakroom export-case ./artifacts/baseline --case refund-response-lost --out ./regressions
breakroom validate-pack ./scenario-packs/support-refunds
python regressions/test_regression.py --agent examples.agents.corrected:run
```

The faulty run intentionally exits **1**. The tutorial `demo` returns **0** only when the observed faulty control fails and corrected control passes; this is demonstration completion, not a faulty-agent release pass. Each output directory contains `report.json`, escaped standalone `report.html`, and `junit.xml`. Demo also contains per-agent bundles, `comparison.json`, and a rerunnable `regression` export.

For release checking, **1** means an observed known failure and takes precedence over incomplete evidence. **2** means invalid configuration, missing required coverage, unsupported mandatory checks, an inconclusive result, or infrastructure trouble when there is no known failure. **0** requires all selected required trials to pass; no trials never passes. An explicitly selected `--case` gates only that subset and is not evidence of complete-pack coverage. Comparison gates the candidate and requires compatible paired case/seed evidence; baseline failures are expected when testing a correction.

Repeat with `--trials 3 --seed 0` to record three independent executions of each selected fixture. The initial five reviewed manifests support seed 0; unsupported seed values are rejected. Comparison pairs by case, seed, and trial occurrence and checks schema, engine, oracle, manifest, fixture, and pack versions. Changed contracts are shown as incompatible. The observed counts are not a statistical confidence claim.

## Adapter contract

See `examples/customer-adapter/adapter.py` for a complete tool-mapping example. It accepts `TaskEnvelope`, `SupportTools`, and `AgentContext` and returns `AgentResult`. Unknown model versions, costs, token usage, or claims remain unknown. The evaluator reads committed SQLite state independently of customer-facing prose. A known duplicate remains a failure even if the worker then hangs or escalates.

`--agent` accepts a local `module:function` reference. Imports are executable trusted local code. Each trial runs in a fresh worker process with a separate SQLite database, controlled simulator time, an independent wall-clock timeout (`--timeout`, at most 120 seconds), bounded worker output, and process-group cleanup. Press Ctrl+C to cancel the active trial. Cancelled or lost-worker state is recovered and evaluated where the database exists. Worker stdout is not copied into reports because adapters may print credentials.

A subprocess is **not a security sandbox**. Customer code can inspect the host, read files, or independently access the network. The injected fake tools use only synthetic local services, and default workers receive a minimal environment with a temporary home and no inherited service secrets. This avoids accidental credential inheritance; it does not prove all credentials on the machine are inaccessible.

Network-enabled model integration is an explicit operator choice. Pass `--allow-network --env OPENAI_API_KEY` only for an adapter you control, with operator-managed provider configuration. The example adapters do not require this. The flag allows named environment variables; it does not turn an OS network firewall on or off. Never pass real payment/help-desk credentials or point injected tools at production services.

## Optional no-network container

After building the core image locally from the repository, run with Docker's network isolation. Building may need network access to acquire the base image and pinned build dependency. Running the test does not:

```sh
docker build -f examples/customer-adapter/Dockerfile -t breakroom-local .
docker run --rm --network none --read-only --tmpfs /tmp:rw,noexec,nosuid,size=128m --cap-drop ALL --security-opt no-new-privileges --pids-limit 64 --memory 256m --cpus 1 -v "$PWD/artifacts:/work/artifacts" breakroom-local demo --output /work/artifacts/demo
```

Create `artifacts` before running. This optional container command needs a working local Docker installation; ordinary core usage does not. Container isolation is a separate control, not a claim that trusted local Python is sandboxed by Breakroom itself. No hosted arbitrary-code runner exists.

## Data artifacts and limits

Reports are schema version 1.0 JSON: at most 8 MiB per report, 32 MiB per bundle, 500 trials, 200 checks per report, 24 nesting levels, bounded arrays and strings. Duplicate keys, non-finite values, unexpected top-level fields, invalid statuses, and contradictory passing verdicts are rejected. HTML escapes all report text and contains no report-controlled scripts or links; JUnit records inconclusive/unsupported cases as errors. Imported reports are customer-generated claims, not independently certified truth.

Regression exports contain a validated frozen `case.json`, `test_regression.py`, dependency notes, and commands. They execute a new trial against a selected trusted local adapter. Keep the matching Breakroom engine source with the export. Viewing an existing report is evidence replay; rerunning starts fresh simulation state.

The scenario format currently accepts strict JSON only. YAML is rejected instead of permitting executable tags or offering an untested loader. See the pack documentation for supported assertions and fault phases. Report validation does not fetch report-provided URLs or import case text as code.

## Troubleshooting

`doctor` reports Python, process-group support, SQLite, write access, and built-in case availability. It does not install global software, alter profiles, upload reports, or start services. Run commands from the repository root to resolve `examples.agents`; for a customer adapter, use its directory or an installed package. A missing callable is an infrastructure result with exit 2. A hung agent has a wall timeout even if it ignores the virtual clock.

The build backend is pinned to setuptools 80.9.0; there are no third-party runtime dependencies. Python subprocess semantics and setuptools pyproject configuration were verified against the official documentation on 2026-09-07. The optional container path requires separate Docker verification on an equipped machine.
