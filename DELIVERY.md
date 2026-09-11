# Breakroom local delivery

The repository now includes real email/password accounts, private customer workspaces, guided agent setup, a visual 24-drill suite builder, evidence-based insights and invitations, alongside the local engine and optional test-only billing. Source repository: [prakharsingh1/breakroom](https://github.com/prakharsingh1/breakroom). See [STATUS.md](STATUS.md) for the current publication and validation state. The complete source is published in the public GitHub repository. No public application server has been deployed.

## Start the application

From the repository root with Docker Desktop running:

```sh
docker compose -f infra/compose.yaml -f infra/compose.team.yaml up -d --build --wait
```

Open [Create an account](http://127.0.0.1:3000/signup), [your workspace](http://127.0.0.1:3000/projects), or [the example demo](http://127.0.0.1:3000/demo). The web port is loopback-only; the APIs are internal. The optional local PostgreSQL port is 54329. Use the configured **127.0.0.1** origin for development sign-in. Register a real email/password account. The separate developer test identity is not a verified identity; production rejects that bypass.

Stop without deleting the database volume:

```sh
docker compose -f infra/compose.yaml -f infra/compose.team.yaml down
```

For the anonymous demo alone, use only `-f infra/compose.yaml`. The core does not require Docker or any service.

## Reproduce the failure without the website

```sh
python3 -m venv venv
venv/bin/python -m pip install -e ./packages/breakroom-core
venv/bin/breakroom doctor
venv/bin/breakroom demo --output ./artifacts/demo
```

The tutorial executes both scripted references. The faulty agent completes its process but creates two successful refunds totaling **200000 INR minor units** for a request authorizing **100000**. Independent checks fail it. The corrected reference creates one refund and updates the correct ticket. All customers, services, money and faults are simulated; this is not a commercial-model benchmark.

The tutorial exits 0 because it reproduced the expected faulty failure and corrected pass. JSON, HTML, JUnit, comparison and runnable regression artifacts are under `artifacts/demo`. A release check uses different semantics: known failure exits 1; incomplete, unsupported or unknown coverage without a known failure exits 2.

In the browser, execute the faulty reference, inspect **What the agent saw** beside **What actually happened**, select and execute the corrected reference, compare, then export the test. A saved browser recording is labeled as recorded evidence. Anonymous evidence expires after 30 minutes or API restart.

## Connect and share your own agent

The supported local Python contract is `run(task, tools, context) -> AgentResult`. The injected facade supplies TestPay/TestDesk tools, and the result supplies structured claims with observed evidence references. [The complete adapter example](examples/customer-adapter/adapter.py) supports the five initial drills; the packaged reference additionally shows event and grouped-request handling.

Customer code runs on its owner's machine or CI as trusted Python. A subprocess is not a security sandbox. The hosted demo executes only fixed built-ins; team uploads never execute customer code.

Prepare one minimized report, inspect it locally, then explicitly upload through its project:

```sh
venv/bin/breakroom prepare-upload ./artifacts/demo/corrected --case refund-response-lost --out ./artifacts/prepared-report.json
```

Keep `.breakroom/redaction.key` private and reuse it for comparable reports. Automated minimization is imperfect. Imports are labeled customer-generated; checksums cannot certify that the execution happened honestly. [Customer worker instructions](docs/customer-worker.md) contain the scoped-key CLI upload and an inactive workflow example. No upload or schedule is enabled automatically.

## Verification commands

Use Python 3.12 for the complete application dependencies. With local PostgreSQL running (substitute the path to your Python 3.12 interpreter if it is not on PATH):

```sh
python3.12 -m venv venv-team
venv-team/bin/python -m pip install -e ./packages/breakroom-core
venv-team/bin/python -m pip install -r apps/api/requirements-billing.lock
PYTHONPATH=packages/breakroom-core/src venv-team/bin/python -m unittest discover -s tests/core -q
PYTHONPATH=packages/breakroom-core/src venv-team/bin/python -m unittest discover -s tests/contract -q
PYTHONPATH=apps/api:packages/breakroom-core/src venv-team/bin/python -m pytest tests/api -q
PYTHONPATH=apps/api:packages/breakroom-core/src venv-team/bin/python -m unittest discover -s tests/team -q
PYTHONPATH=apps/api:packages/breakroom-core/src venv-team/bin/python -m unittest discover -s tests/billing -q
venv-team/bin/python -m unittest discover -s tests/operations -q
venv-team/bin/python scripts/backup-restore-smoke.py --out artifacts/operations/backup-restore.json
npm --prefix apps/web ci
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
```

Install the matching browser with `cd apps/web && npx playwright install chromium`, then from the repository root:

```sh
docker compose -f infra/compose.yaml -f infra/compose.team.yaml -f infra/compose.e2e.yaml up -d --no-deps --wait team-api
PYTHONPATH="$PWD/packages/breakroom-core/src" BREAKROOM_TEST_PYTHON="$PWD/venv-team/bin/python" BREAKROOM_TEAM_E2E=1 npm --prefix apps/web run test:e2e
docker compose -f infra/compose.yaml -f infra/compose.team.yaml up -d --no-deps --wait team-api
```

On the verified Mac the matching Chromium download timed out. The tested installed browser override is:

```sh
export BREAKROOM_BROWSER_EXECUTABLE="$HOME/Library/Caches/ms-playwright/chromium-1234/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
```

The current Python gate passes 307 tests, including 85 account/workspace/tenant checks. The current customer gate passed 14 browser tests in 2.7 minutes, with 53 accessibility scans reporting zero violations. Manual-review items and the exact scope are recorded in STATUS.md. Browser automation uses a separate request budget because it sends hundreds of requests through one local proxy; normal runtime limits and password-abuse budgets are unchanged. The separate mutation matrix executed 477 fresh trials: 216 corrected passes and 261 expected negative detections, with no missed or incomplete detections. Real backup/restore, CLI HTTP upload and downloaded regression execution also passed. Exact timings and evidence are in [STATUS.md](STATUS.md). Upstream HTTPX/Starlette/Authlib deprecation warnings remain. GitHub Actions now runs the remote checks; see the publication record in STATUS.md and the repository’s Actions tab for the exact run result.

## Evidence and source map

- [Customer dashboard](output/playwright/customer-dashboard-1440.png), [mobile dashboard](output/playwright/customer-dashboard-390.png), and [account registration](output/playwright/customer-signup-1440.png).
- [Actual demo screenshot](output/playwright/demo-1440.png), [mobile demo](output/playwright/demo-390.png), [private team comparison](output/playwright/team-project-1440.png), and [mobile private project](output/playwright/team-project-390.png).
- [Customer account backup/restore evidence](artifacts/operations/customer-backup-restore-root.json), [CLI upload HTTP smoke](artifacts/uploads/http-smoke.json), and [clean final wheel installation](artifacts/wheels/m5/clean-install.json).
- [Downloaded browser regression verification](artifacts/browser-export-final/verification.json) and [accessibility scan artifacts](artifacts/accessibility).
- `packages/breakroom-core`: dependency-free engine, runner, CLI, evaluator, reports and exports.
- `scenario-packs/support-refunds`: 24 reviewed manifests, deterministic variation constraints and negative-control mappings.
- `apps/api`: separate demo and PostgreSQL team services; optional billing adapter.
- `apps/web`: Next.js application; `tests`: core, contracts, API, tenant, billing, operations and browser checks.
- [Compatibility](docs/compatibility.md), [coverage review](docs/coverage-review-2026-09-07.md), [accessibility review](docs/accessibility.md), [operations](docs/operations.md), and [billing](docs/billing.md) define scope and limitations.

## Before an external beta

The next external step is an owner-reviewed deployment with HTTPS and TLS SMTP, followed by actual email verification and password-recovery delivery tests. Email/password sign-in is implemented; OIDC is optional. Read [self-hosting](docs/self-hosting.md) for the production template. It also needs private database credentials/networking, encrypted backups, a disclosed retention/recovery policy, monitoring, and a configured security contact. The local Compose overlay is explicitly development-only.

Billing defaults to disabled. Signed fixtures and the real SDK with offline transport validate the supported monthly USD test contract; no Stripe sandbox purchase or live payment occurred. Merchant eligibility, credentials, test price/webhook and an authorized external sandbox test remain separate. Live billing is rejected by this implementation. The free local engine remains useful independently.

Passing cases establish behavior only under modeled conditions. Provider equivalence, universal safety, statistical model performance, production readiness, trademark/domain/package ownership and a public license are not claimed. Review [the license proposal](LICENSE-PROPOSAL.md) and [security scope](SECURITY.md) before adopting a license or operating a public service.
