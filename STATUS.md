# Breakroom status

Updated: 2026-09-11

Customer product upgrade complete locally: real email/password accounts, password recovery/verification, guided setup, private workspaces, real report summaries and check-specific recommendations, visual suite builder and email-bound invitations. The complete implementation is published publicly at [prakharsingh1/breakroom](https://github.com/prakharsingh1/breakroom). The customer sandbox is implemented and the final public code revision passed every remote workflow job, including real Linux gVisor execution and browser tests. No public application deployment or outbound email has been performed.

**Current acceptance (2026-09-11): 341 distinct Python checks, 15 actual browser journeys, and 477 mutation trials passed.** All three final remote jobs passed on f1307558acad63d3123aaabce21856b3e316261d, including real Linux gVisor execution. All 57 retained accessibility scans record zero violations. Typecheck/build, real account/session flows, tenant controls, 21-table backup/restore and responsive screenshots passed. Exact commands, evidence and production prerequisites follow.

The following entries preserve the previous milestone history.

- M0 complete: full specification read; empty repository inspected; guidance and plan created. Original spec preserved. Directory is not a Git repository.
- M1 complete: real SQLite world, ordinary control and lost response, scripted controls, independent evidence evaluation, atomic operation-key handling.
- M2 complete: five initial drills, CLI, bounded process runner, local adapter, compatible report comparisons, JSON/HTML/JUnit and runnable exports.
- M3 complete (Cut A): Next.js website, live allowlisted FastAPI demo, library/details, Runs/Evidence/Compare, local docs and honest pricing; responsive layouts and real browser journey verified.
- M4 complete (Cut B): 24 distinct drills, grouped/concurrent dispatch, pending/event/timed behavior, reviewed seeds 0/1/2, versioned provenance and executable mutation matrix. All acceptance evidence below.
- M5 complete locally: authenticated PostgreSQL team reports, private projects/roles/scoped keys, explicit minimized uploads, private suite metadata, retention/deletion and customer-owned CI protocol. Real tenant, browser and CLI HTTP checks passed. Public OIDC/hosting remains unconfigured.
- M6 complete locally (Cut C): optional test-only subscription lifecycle, owner billing UI, quota enforcement, backup/restore validation and accessibility checks. The final integrated gate passed. Billing defaults to disabled; no external Stripe transaction, live billing or public deployment occurred.

Environment observed: macOS, Python 3.12.14 and 3.14.0, Node 24.19.0 and 26.5.0. Core has zero runtime dependencies. Next 16.3.4, React 19.2.8, TypeScript 5.9.3 and Playwright 1.63.0 are pinned in the npm lockfile. API dependencies are pinned in apps/api/requirements.lock. No external deployments, accounts or billing enabled.

M1 verification (2026-09-07): `PYTHONPATH=packages/breakroom-core/src python3 -m unittest discover -s tests/core -p test_m1.py -v` — 15 tests passed, exit 0. Root separately executed both controls: normal faulty/corrected PASS; lost-response faulty completed/FAIL with 2 refunds totaling 200000 INR minor units; corrected completed/PASS with 1 refund totaling 100000. Assertions read actual records.

M2 verification: root ran 26 core tests and 20 CLI/artifact/runner contract tests, all passed (exit 0). The latter includes actual exported corrected pass/faulty fail and process cleanup. 31 API integration tests passed with two upstream deprecation warnings. Additional agent validation is documented in the final handoff when integrated.

M3 verification: Next typecheck and production build passed. Seven Playwright tests passed (12.8 seconds, exit 0): real engine faulty/corrected effects, compatible comparison, browser ZIP download, recordings/evidence, library/contracts, docs copy, keyboard skip link, honest pricing, offline/expired states, unknown effects and export expiry, and responsive runs. Home/demo screenshots at 390/768/1440 plus desktop comparison were visually inspected; no horizontal overflow. The first attempt exposed an amount-format mismatch and ambiguous Next route-announcer locator; both were repaired. Matching Chromium 153 download timed out; verified browser is installed Chromium 151.0.7922.34 with explicit BREAKROOM_BROWSER_EXECUTABLE override.

Historical M3 root integrated tests: 37 core + 24 runner/artifact + 31 API passed; API has two upstream deprecation warnings. Downloaded browser ZIP was extracted under artifacts/browser-export and executed: corrected exit 0, faulty exit 1. Clean nonhidden `venv/` editable install and isolated wheel were tested on Python 3.14. This Mac repeatedly marks `.venv`'s editable .pth hidden; `venv/` avoids that environment issue without changing package code. The documented `venv/bin/breakroom doctor` and demo passed again.

Screenshots: output/playwright/home-{390,768,1440}.png, demo-{390,768,1440}.png, compare-desktop.png. Latest demo bundles: artifacts/final-demo (JSON/HTML/JUnit/comparison/regression). README and docs contain exact commands; .github/workflows/ci.yml is prepared but has not run remotely.

Docker Desktop 29.4.0 started locally. `docker compose -f infra/compose.yaml build` passed for Python 3.12.14 and Node 24.19.0 images. `docker compose -f infra/compose.yaml up -d --wait` passed; the API is healthy and only the web port is exposed on loopback. All seven browser tests also passed against the containerized application (11.3 seconds, exit 0). These historical Cut A images were subsequently rebuilt for M4 and M5 acceptance below. No public deployment.

Next external task requires owner configuration and authorization for a customer website: supply an HTTPS deployment target and TLS SMTP sender, private database credentials/networking, backup storage, monitoring and security contact, then validate delivered verification and password-reset mail. Email/password sign-in is implemented; OIDC is optional. Optional provider sandbox billing additionally needs merchant eligibility, test credentials, a supported test price and webhook configuration. Live billing remains rejected. All requested local implementation gates are complete; see [DELIVERY.md](DELIVERY.md) for working commands and external prerequisites.

M4 acceptance (root, 2026-09-07): 81 core tests passed (7.789s), 40 contract tests passed (11.999s), 31 API tests passed (2.28s; two upstream deprecation warnings); typecheck and Docker production build passed. Rebuilt Compose startup healthy. Seven updated browser tests passed (12.7s), plus the new local-drill responsive test passed (3.7s) at 390/768/1440 with all three screenshots visually inspected. Browser ZIP extracted under artifacts/browser-export-m4; corrected regression exit 0, faulty exit 1.

M4 matrix: `breakroom mutation-check --pack support-refunds --seed 0 --seed 1 --seed 2 --trials 3 --out artifacts/coverage-24-variations` exited 0: 477 actual trials, 216/216 corrected PASS, 261/261 declared negative detections, zero incomplete/missed. Root independently schema-validated all 477 reports and recomputed classifications/summary and unique trial references. Unknown, untriggered and unscheduled cells were not counted as detections. Historical applicability evidence remains preserved.

M4 packaging: breakroom-core 0.2.0 clean wheel installed outside checkout into fresh Python 3.12 environment without PYTHONPATH; doctor/demo, all 24 corrected at seed 0 and seed 2 passed. Seed-2 event export corrected exit 0 / mutant exit 1. Commands and evidence: artifacts/wheels/clean-install-0.2.0.json. Full website catalog has 24 actual contracts; browser execution still uses its five explicit allowlisted cases. New screenshots: output/playwright/local-drill-{390,768,1440}.png. CI now includes the actual 477-trial mutation command; no remote CI executed.

M5 acceptance (root, 2026-09-07): 81 core tests PASS (7.579s), 61 contract tests PASS (35.701s), 31 demo API tests PASS (2.25s), and 47 team tests PASS (4.449s: 23 real PostgreSQL, 14 auth, 10 request boundary). Upstream HTTPX/Starlette/Authlib deprecation warnings remain. Contracts include the actual 477-report bundle roundtrip: the aggregate JSON node bound now supports the bounded 32 MiB multi-report artifact while individual report limits stay strict.

M5 browser: `BREAKROOM_TEST_PYTHON="$PWD/venv/bin/python" BREAKROOM_TEAM_E2E=1 BREAKROOM_BROWSER_EXECUTABLE="<installed Chromium path>" npm --prefix apps/web run test:e2e -- team.spec.ts` — 1 real full journey PASS (8.0s, 10.5s total), exit 0. Two real faulty/corrected reports were explicitly reviewed/uploaded, compared, downloaded, shared with a viewer, checked with a scoped and revoked key, retained/deleted, and nested evidence/exports denied after deletion. Screenshots at output/playwright/team-project-{390,768,1440}.png were visually inspected. An initial dev-proxy hostname defect and a test select locator were repaired; assertions were preserved. Nonhidden venv/ avoids the recurring macOS editable-link issue.

M5 HTTP worker: `venv/bin/python tests/api/smoke_team_upload_http.py --origin http://127.0.0.1:3000 --web-origin http://127.0.0.1:3000` PASS/exit0 (agent,0.96s), evidence artifacts/uploads/http-smoke.json. Real CLI prepared/uploaded two reports, duplicate retry reused the existing record, compatible comparison showed 2 versus 1 refund, export/evidence/revoked key/deletion were verified. A separate actual redirect smoke confirms zero requests/credentials reach the redirect destination. Customer workflow example is inactive and has no schedule.

M5 Compose rebuilt successfully: PostgreSQL18.6 on loopback54329, web on loopback3000, demo/team APIs internal with no host ports. Local development identities are explicitly labeled and prohibited in production. OIDC uses Authlib1.8 and tested signed claims; no live identity provider, public endpoint, customer data, commercial account or live payment configured. Automatic bounded expiry sweeps run on startup and every300seconds; expired objects are inaccessible even before physical cleanup.

## Historical integrated acceptance — M0 through M6 locally (2026-09-07)

Root executed the complete gate against the final source on 2026-09-07. All commands below exited 0. Python tests used Python 3.12.14 in `venv-team`, explicit source paths, and real local PostgreSQL for the team/billing database cases.

| Suite | Result | Elapsed |
|---|---:|---:|
| Core: `PYTHONPATH=packages/breakroom-core/src venv-team/bin/python -m unittest discover -s tests/core -q` | 81 passed | 8.096s |
| Contracts: `PYTHONPATH=packages/breakroom-core/src venv-team/bin/python -m unittest discover -s tests/contract -q` | 61 passed | 35.960s |
| Demo API: `PYTHONPATH=apps/api:packages/breakroom-core/src venv-team/bin/python -m pytest tests/api -q` | 31 passed | 2.46s |
| Team: `PYTHONPATH=apps/api:packages/breakroom-core/src venv-team/bin/python -m unittest discover -s tests/team -q` | 47 passed | 5.863s |
| Billing: `PYTHONPATH=apps/api:packages/breakroom-core/src venv-team/bin/python -m unittest discover -s tests/billing -q` | 46 passed | 6.447s |
| Operations: `venv-team/bin/python -m unittest discover -s tests/operations -v` | 3 passed | 0.001s |

Total: **269 Python tests passed**. Team includes 23 real PostgreSQL, 14 authentication and 10 request-boundary tests. Billing includes 27 real PostgreSQL lifecycle tests and 19 real Stripe SDK tests using offline transport and signed fixtures. Stripe 15.6.1 is pinned to API version `2026-08-26.dahlia`. No external provider checkout/payment was executed. Upstream HTTPX/Starlette/Authlib and AnyIO deprecation warnings remain; they are not application test failures.

Both `npm --prefix apps/web run typecheck` and `npm --prefix apps/web run build` passed. The production build contains 13 routes and the Next proxy. `docker compose --progress quiet -f infra/compose.yaml -f infra/compose.team.yaml up -d --build --wait` rebuilt and started the final images successfully: demo API, team API and PostgreSQL healthy; web available on loopback port 3000. Only the web and local PostgreSQL ports are exposed; APIs remain internal. Services remain available for local preview.

Final browser command:

```sh
BREAKROOM_TEST_PYTHON="$PWD/venv/bin/python" BREAKROOM_TEAM_E2E=1 BREAKROOM_BROWSER_EXECUTABLE="$HOME/Library/Caches/ms-playwright/chromium-1234/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing" npm --prefix apps/web run test:e2e
```

**11 browser tests passed in 52.4s**, exit 0, against the rebuilt containers. Coverage includes the actual faulty/corrected demo and downloaded ZIP, comparison, library/details/docs controls, offline/expired/unknown states, responsive widths 390/768/1440, real private team upload/role/key/deletion flow, disabled billing endpoint, and isolated billing UI fixtures. The billing UI fixtures do not represent external provider execution. The installed Chromium 151 override remains necessary on this Mac after the matching Chromium 153 download timed out.

**37 accessibility scans recorded zero violations**, with no rules or page regions excluded. Scan artifacts are under `artifacts/accessibility`; only color-contrast items remain flagged for manual review of decorative arrows/empty glyphs, and were retained and visually checked. Actual keyboard skip-link focus and reduced-motion behavior passed. Demo, private team and billing screenshots were inspected at 390/768/1440; homepage, local drill and comparison screenshots are also retained. See [accessibility scope](docs/accessibility.md) and [screenshot links](DELIVERY.md#evidence-and-source-map). This is a bounded automated/manual review, not a certification.

Root re-executed the real CLI upload smoke against the final containers:

```sh
venv/bin/python tests/api/smoke_team_upload_http.py --origin http://127.0.0.1:3000 --web-origin http://127.0.0.1:3000
```

PASS, exit 0, 0.9396s. Evidence at `artifacts/uploads/http-smoke.json` confirms two minimized reports, duplicate retry reuse, compatible two-versus-one-refund comparison, revoked-key rejection and project deletion. No customer system was accessed.

The final browser download at `output/playwright/breakroom-regression.zip` contains the four expected files under its single regression prefix. Root extracted it into `artifacts/browser-export-final` and ran `venv/bin/python artifacts/browser-export-final/test_regression.py --agent examples.agents.corrected:run` (exit 0), then the same command with `examples.agents.faulty:run` (exit 1). Both execute new simulated trials; logs and `verification.json` are retained.

Root independently ran `venv-team/bin/python scripts/backup-restore-smoke.py --out artifacts/operations/backup-restore-final.json`: PASS, exit 0, 1.288s. PostgreSQL/pg_dump/pg_restore 18.6 restored schema versions 1 and 2; all 12 tables matched in rows, values/types and constraints, two report hashes matched, 11 foreign keys and constraint/immutability enforcement passed, and deletion including billing cascades passed. The 36,321-byte synthetic archive had SHA-256 `186d5a67288dc23c3f5fc5427cf04c43a625bfb6a8aac7b6fde0c6872f1a0fda`. The script removed its archive and two uniquely owned temporary databases; it did not restore over the application database. Guard tests reject unsafe target/query options. External encrypted backup storage and an operational restore schedule are not configured.

Final core packaging was revalidated after upload support: `artifacts/wheels/m5/breakroom_core-0.2.0-py3-none-any.whl`, SHA-256 `ec20bb51ef61f391d32b8458672e6d50fec9a6124b5572428e3ae0eb70360d3a`. Ten checks passed in a fresh Python 3.12 environment outside the checkout: dependency-free wheel installation/import, doctor/demo, all 24 corrected cases at seed 2, minimized faulty/corrected preparation, and actual seed-2 event regression corrected pass/negative-control fail. Evidence is `artifacts/wheels/m5/clean-install.json`; prior wheel artifacts are preserved. The previously accepted 477-trial mutation evidence remains unchanged and valid.

M6 implements disabled-by-default, test-only owner checkout/cancellation/reconciliation, signature-verified durable webhook processing, invoice-backed entitlements, bounded recovery and serialized quota enforcement. Unknown billing state grants no paid write entitlement; retained reads/exports/deletion remain available. Live credentials/events are rejected. [Billing](docs/billing.md), [operations](docs/operations.md), [security scope](SECURITY.md), [changelog](CHANGELOG.md) and [delivery](DELIVERY.md) document the implemented behavior and remaining external prerequisites. Prepared repository CI and the inactive customer-upload example have not run remotely or been enabled. No public deployment, package publication, commercial account activation, real payment or customer data processing occurred.

## Customer upgrade acceptance — 2026-09-10

Root independently ran the final Python gate: 81 core (10.499s), 61 contracts (104.343s), 31 demo API (3.93s), 85 team/accounts/workspaces (53.097s), 46 test-billing (27.531s), and 3 operations guards (0.001s). All exited 0, 307 tests total. The team suite includes 25 new password-account checks and 13 new workspace checks. Argon2id uses pinned argon2-cffi 25.1.0/bindings 26.1.0; maintained authentication dependency/source references are in docs/accounts.md. Existing upstream HTTPX/Starlette/Authlib deprecation warnings remain. Core source/pack contracts are unchanged, so the previously accepted 477-trial mutation and clean-wheel evidence remains applicable.

Next typecheck and production build passed; the application now includes /signup, /signin, /account, /verify-email, /reset-password and /invite alongside the existing routes. Final Docker images were rebuilt successfully. The first browser attempt used the core-only Python environment, which lacked Uvicorn; the final command uses the complete Python 3.12 environment with an explicit source path. Stale test selectors were corrected for the new sign-in heading, named select controls and Next’s separate route announcer. No business assertions were removed.

Final browser command (after applying infra/compose.e2e.yaml):

```sh
PYTHONPATH="$PWD/packages/breakroom-core/src" BREAKROOM_TEST_PYTHON="$PWD/venv-team/bin/python" BREAKROOM_TEAM_E2E=1 BREAKROOM_BROWSER_EXECUTABLE="$HOME/Library/Caches/ms-playwright/chromium-1234/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing" npm --prefix apps/web run test:e2e
```

**14 tests passed in 2.7 minutes, exit 0.** They execute real account signup/login/project persistence, password change and other-session rejection, incorrect-password rejection, recovery availability and invalid links, guided suite creation, missing/fail/pass readiness, real local report uploads, filtering/sorting, invitation acceptance/revocation, existing tenant/key/billing states, and the real demo/export journey. Email delivery itself was tested through offline mail transports, not an external provider. The invitation browser flow uses explicit local test identities; verified password-email invitation enforcement is covered by actual PostgreSQL tests.

Browser automation initially reached the normal shared-proxy request budget. The separate e2e overlay permits 1,000 requests/minute for this high-volume local test only; CI uses the same explicit test setting. Default application/production request limits and durable password-abuse limits remain unchanged and are covered by boundary tests. After verification the local team service is returned to its normal configuration.

All 53 scan artifacts in artifacts/accessibility were refreshed during the final gate and recorded zero violations. No rule exclusions were added. Final review repaired named statistic groups by adding their group role and restored horizontal padding in the empty reports card. The focused account/workspace follow-up passed all 3 tests in 56.8s after the rebuilt production images and typecheck passed again. All 53 retained scan results still have zero violations; only 15 decorative color-contrast review entries remain, reviewed in screenshots. All 12 new customer screenshots were inspected, with the fixed mobile dashboard inspected again. New signup/dashboard/overview/suite screenshots at 390/768/1440 are in output/playwright/customer-*.png; view links are in DELIVERY.md. Screenshots are taken from the page top so offscreen fixed skip links are not captured in the middle of a stitched image.

Root backup/restore: `PYTHONPATH=apps/api:packages/breakroom-core/src venv-team/bin/python scripts/backup-restore-smoke.py --out artifacts/operations/customer-backup-restore-root.json` passed in 5.935s, exit 0. PostgreSQL 18.6 restored all 16 tables and schema versions 1–4, 15 foreign keys, 2 report hashes, actual correct/wrong Argon2 password verification, invitation/reset token hashes, plaintext-credential absence, constraints and project/account deletion cascades. Synthetic 45,817-byte archive SHA-256: bd31d51b683d767d5f286e439964e6ffec6cf9e3a685224845f535916946ee15. Both uniquely owned databases and archive were removed; the application database was not restored over.

The production self-host template parses with `docker compose --env-file .env.production.example -f infra/compose.production.yaml config --quiet`. External HTTPS hosting, SMTP delivery, private database networking, encrypted backup storage and operational capacity still need operator configuration and validation. No external account email, customer action, paid service or live billing was activated. Source files, config examples and synthetic screenshots were reviewed for publication; environment secrets, database files, virtual environments, build output, raw test artifacts and archives are excluded. No credential-pattern matches were found in the publishable text files.


## Publication handoff — 2026-09-10

The user explicitly authorized publishing the source publicly under prakharsingh1. Created and independently verified the public repository at https://github.com/prakharsingh1/breakroom. Local implementation commit: `07b160c4caff3c96850ab97f17048afc2bc3efe8`, tree `4bedc4a1ef1ff2954e245daab45d1552cec6e9e6`, 260 reviewed files. Final staged whitespace check passed; candidate-file checks found no credential-pattern matches or excluded runtime/database files.

Publication did **not** succeed. Homebrew Git HTTPS failed with curl 55/broken pipe and LibreSSL bad-record-MAC errors; a system Git retry stalled and its identified processes were stopped. Existing GitHub SSH authentication succeeded, but the push disconnected. GitHub Contents API initialization also failed with TLS bad-record-MAC errors using the normal CLI, HTTP/1.1, verified Python TLS 1.2 and smaller TLS writes. No certificate validation was disabled and no credential was written to source or logs. GitHub API and an independent signed-out browser view confirmed the repository remains empty. No remote CI ran.

All source remains in local Git. The ignored `artifacts/publication/breakroom.bundle` contains the complete committed repository for transfer; it is generated after this handoff commit. Once GitHub transfer connectivity works, run `git push -u origin main` from this directory, then verify the remote commit and Actions checks. The origin is https://github.com/prakharsingh1/breakroom.git. GitHub's empty repository needs no additional creation or permission approval. No public application hosting or SMTP delivery is configured.

The final normal local Compose configuration was restored after browser testing; the team API is healthy with its default request budget. Local account creation remains available at http://127.0.0.1:3000/signup. The next external website step remains operator-provided HTTPS hosting, private PostgreSQL and TLS SMTP, followed by actual email-delivery checks; see docs/self-hosting.md.


## Publication retry — 2026-09-11

Resumed the user-authorized publication with a clean working tree. GitHub still reports PUBLIC with no default branch. `git -c http.connectTimeout=15 -c http.lowSpeedLimit=1 -c http.lowSpeedTime=30 push -u origin main` failed with curl 55/LibreSSL bad-record-MAC. Retried with the existing SSH identity on the explicit standard github.com port 22, strict host-key checking and bounded connection/keepalive settings; upload disconnected with a broken pipe. The prior SSH attempt used ssh.github.com. `gh api repos/prakharsingh1/breakroom/branches/main --jq .commit.sha` again returned 404 Branch not found, so neither retry published source. No remote CI ran and no application code changed. The previously completed local acceptance results above remain the applicable test evidence.

The source bundle is refreshed after committing this note. Next action requires restored GitHub transfer connectivity, for example a different working network, then `git push -u origin main` and remote CI verification. No credential or certificate settings were weakened, no paid service was enabled, and no additional repository creation is needed.


## Source published — 2026-09-11

The retry of `git -c http.connectTimeout=15 -c http.lowSpeedLimit=1 -c http.lowSpeedTime=30 push -u origin main` succeeded, exit 0. GitHub accepted main at `e27b94fd509288ee4d5837eba0eabf03708f8e20` and the local branch now tracks origin/main. A subsequent repository query confirmed PUBLIC visibility and main as the default branch. The earlier transfer blocker is resolved without changing credentials or certificate validation. The initial [remote workflow](https://github.com/prakharsingh1/breakroom/actions/runs/34599158374) is running against that exact commit.

Only the reviewed source, documentation and synthetic screenshots were published. Customer databases, credentials, local environments and raw artifacts remain excluded. Public source publication does not deploy the application; external hosting and SMTP remain operator configuration tasks described in docs/self-hosting.md.


## Active extension — customer-agent sandbox hosting

User requested ZIP or GitHub agent imports tested in isolation, with approved provider access using customer-supplied keys. Implementation is in progress, not complete. This extends the original local-only execution boundary; the anonymous demo remains fixed and allowlisted. PLAN.md records the intended architecture and acceptance. Remote run 34599158374 passed backup/restore, Python suites, mutation controls, typecheck/build and public demo browser tests; customer-browser tests failed and the log is being inspected before functionality advances.

Publication repair (2026-09-11): the first remote customer journey exposed a real invitation race: the copy action could run while creation/refresh was still finishing, and its confirmation was then overwritten. Copy is now disabled until creation settles; the browser test grants clipboard permissions and verifies the clipboard contents. Typecheck passed; the real PostgreSQL-backed customer-workspace browser test passed (1 test, 18.6s, exit 0) against rebuilt local containers. Remote revalidation follows the repair push. The new sandbox package remains work in progress and is not included in this repair commit.


## Customer agent sandbox — local acceptance (2026-09-11)

Implemented the owner-requested hosted execution extension: authenticated ZIP and pinned GitHub source deployment, tenant-bound AES-GCM source/provider keys, owner key management, approved text-model broker with atomic call budgets and hard request deadlines, PostgreSQL durable queue, per-trial isolated guests, cancellation/expiry/worker recovery, trusted external simulator/evaluator, 24-drill/72-trial selection, responsive workspace/evidence/downloads and operator setup. Anonymous demo inputs remain allowlisted. Production requires a dedicated Linux gVisor worker; runc is explicitly restricted to trusted development fixtures and rejected in production. No public application or live provider call has been deployed/executed.

Schema-1 oracle 1.2 fixes declared-capability enforcement for the original five drills. Missing declared capabilities now block execution and yield UNSUPPORTED, matching schema-2 intent. Core stays dependency-free and independent of the web service. The oracle change deliberately invalidates comparisons to older oracle-1.1 results; rerun both sides.

Root local final Python checks: 83 core (8.211s), 61 contracts (46.657s), 31 demo API (2.18s), 85 team/account/workspace (23.065s), 46 test billing (7.024s), 3 operations guards (0.001s), and 29 sandbox (25.888s), **338 passed**, all exit 0. Commands use `PYTHONPATH=apps/api:packages/breakroom-core/src venv-team/bin/python` with unittest discovery in each tests directory, pytest for tests/api. Sandbox runtime tests used `BREAKROOM_SANDBOX_TEST_DOCKER=1 BREAKROOM_SANDBOX_RUNTIME=runc BREAKROOM_SANDBOX_TRUSTED_DEV=1` against only our own fixture code. Ten boundary/provider tests, twelve real PostgreSQL control-plane tests and seven real container tests cover the new feature. All 24 drills passed using the corrected adapter in actual containers, and faulty/corrected, concurrency, no guest oracle/key/socket/network access, read-only root, timeout, cancellation, protocol forgery, queued persistence and atomic budget/revocation paths were executed. Provider HTTP requests use offline synthetic transport; saving a provider key makes no outbound call. Upstream dependency deprecation warnings remain.

The updated mutation command `PYTHONPATH=packages/breakroom-core/src venv-team/bin/python -m breakroom mutation-check --pack support-refunds --seed 0 --seed 1 --seed 2 --trials 3 --out artifacts/sandbox-mutations` passed: 477 actual trials, 216 corrected passes, 261 negative detections, zero missed/incomplete; exit 0. Evidence: artifacts/sandbox-mutations/matrix.json and reports/report.json.

Typecheck, Next production build, and rebuilt local Docker services passed. Full actual browser gate with team and sandbox flags passed **15 tests in 1.4 minutes**, exit 0; evidence artifacts/sandbox-full-browser.log. This includes signup/login, workspace/report/invitation/billing paths, public demo, and new ZIP download/upload → queued worker → actual refund → saved/downloaded trial evidence. Model-key UI used an explicitly synthetic nonworking key and never invoked a provider. Four new sandbox accessibility scans recorded zero violations. Full screenshots and separate intro/evidence panels at 390/768/1440 are under output/playwright/sandbox*.png; all six intro/evidence panels were visually inspected. Earlier contrast failures were repaired, not excluded. Existing accessibility assertions remain unchanged.

Backup/restore passed against PostgreSQL/pg_dump/pg_restore 18.6: **21 tables**, schema versions 1–5, matching rows/column types/constraints, encrypted model-key and source recovery/hash validation, project cascades and post-backup deletion replay; 2.532s, exit 0. Evidence artifacts/operations/backup-restore-sandbox.json. The temporary archive and two owned test databases were removed. An initial purely syntactic PostgreSQL AND-parenthesis difference was repaired by writing the equivalent call-budget constraint in flat comparisons; no check was weakened. Production requires an independently protected vault-key backup and a supervised worker restart/reaping policy.

GitHub: invitation repair commit ab59fdb passed both verify and backup_restore jobs in run https://github.com/prakharsingh1/breakroom/actions/runs/34606623014. A small scripted import example is published on codex/sandbox-starter at commit 5d1506042a45cc2ddad3968798143d96e86bd5f8. The new sandbox job installs official gVisor release 20260907.0 from its checksum-pinned release tarball and runs real runtime/database/browser tests on disposable Linux. Its first result remains pending until the extension is pushed. Linux gVisor isolation must not be inferred from the local Mac/runc fixture checks.

External work still requires operator setup: an HTTPS customer hosting target, TLS mail delivery, a dedicated supervised Linux gVisor worker and protected secret/database/backup configuration. Source publication is authorized; public application deployment, paid services and live model spending are not authorized/configured. User-supplied source embedded secrets are not scanned automatically; ZIP format and text-model adapter limits are documented in docs/sandbox.md.


Sandbox external and packaging verification (2026-09-11): real GitHub ZIP import from the published example commit passed both the import function/container check and the full authenticated HTTP deployment → queue → worker → retained evidence flow. Three reviewed seeds passed with one actual refund each, zero model requests; artifacts/sandbox-github-import.json and artifacts/sandbox-github-http.json. These are our scripted examples, not customer data or commercial-model trials.

The separate **Linux gVisor job passed** on public source ba6511653b90823cdf6cb5904f1d4dca09b2c0a8 in run https://github.com/prakharsingh1/breakroom/actions/runs/34609055065, including the real container/24-drill/tenant/broker suite and actual browser ZIP→gVisor→evidence journey. Its 21-table backup job also passed. This validates the implemented runtime on a disposable Linux CI worker; it does not provision or certify a production service. The general job and final maintenance commit continue through the same checks.

Final maintenance makes orphan-container ownership stable across database-password rotation (scope derives from public origin and schema). Two additional regression checks verify this and ensure reaping only removes expired owned container names; all 12 boundary tests passed (0.160s). The local distinct Python acceptance total is therefore **340**. Existing scope/tenant/runtime assertions remain intact.

The downloaded browser regression ZIP was re-executed: corrected exit 0, faulty exit 1; artifacts/browser-export-sandbox/verification.json. Initial execution without the explicit source path encountered this Mac's known editable-import issue; adding the documented checkout path resolved it. The core wheel was separately built using pinned setuptools 80.9.0 (the full API venv intentionally lacked build tooling), installed with no runtime dependencies into a fresh temporary environment outside the checkout, and tested without PYTHONPATH: doctor and all 24 corrected cases at seed 2 passed, oracle 1.2 present. Wheel SHA-256 94ab17e4194e6ecb2854ac7d48cc233030ea9a49c7d942e1cd52f120343052c8; artifacts/wheels/sandbox/verification.json. Temporary environment removed.

All 57 retained accessibility scans currently record zero violations. New sandbox introduction/evidence screenshots were inspected at 390/768/1440. Local fixture workers are stopped after verification; final local control-plane configuration requires runsc rather than leaving trusted-runc development execution available for customer use. A production worker remains an operator prerequisite.

Final authorization follow-up: queued execution and each model call also recheck whether the submitting account's authentication mode remains enabled and whether production password verification is satisfied. The worker checks permission before creating a guest. A new real-PostgreSQL regression proves a disabled account cannot start its queued container or spend model budget; all 13 control-plane tests passed. Local distinct Python acceptance is now **341**. No changes to the guest protocol, UI or oracle were needed.


## Final public sandbox acceptance — 2026-09-11

Verified public code revision **f1307558acad63d3123aaabce21856b3e316261d** passed the entire workflow: https://github.com/prakharsingh1/breakroom/actions/runs/34609876689. All three jobs succeeded: **sandbox**, **verify** (6m43s) and **backup_restore** (36s). The sandbox job exercised the final 32 sandbox tests with gVisor release 20260907.0 and the real ZIP-to-guest-to-evidence browser journey. General verification includes the complete core/contracts/API/accounts/billing/build/browser checks and 477 mutation controls. The separate backup job tested all 21 tables and encrypted source/key recovery. Remote evidence is retained in artifacts/publication/sandbox-final-ci.json and artifacts/sandbox-final-gvisor.log.

This acceptance receipt changes documentation only; executable source is identical to the verified revision. No required implementation checks remain failing or untested. Production hosting/provisioning, operational security review and live model/email credentials remain external prerequisites, not claimed deployments or validations. The local API is healthy at schema 5 with normal 120/minute request limits, sandbox source storage enabled and runsc required. Trusted fixture workers have stopped; the UI correctly reports no available production worker on this Mac. The source and scripted GitHub import example are public; no runtime database, vault key, provider credential or private customer source is in Git.

Working customer entry: http://127.0.0.1:3000/projects → open a project → **Agent sandbox**. Operator setup: docs/sandbox.md and infra/breakroom-sandbox-worker.service. General local startup remains `docker compose -f infra/compose.yaml -f infra/compose.team.yaml up -d --build --wait`. Enabling the optional sandbox control plane additionally uses infra/compose.sandbox.yaml with an operator-managed vault key; actual untrusted execution requires its separate Linux worker. Keep the standalone Python core usable independently.


## Cloudflare free website — 2026-09-11

The user explicitly authorized a free Cloudflare site deployment. Connected the
selected account, verified Workers Free in its dashboard, and published
**https://breakroom.snghprakhar.workers.dev**. Worker version:
`e0933432-ca89-45d3-ac06-288ccbec9f9e`. Upload completed in 13.91s and trigger
publication in 5.19s. Bundle: 1151.64 KiB (303.07 KiB gzip), startup 26ms;
only static assets and version metadata bindings. No paid plan, container,
database, email service, AI service, or domain purchase was enabled.

This is the public website/documentation and 24-drill catalog deployment, **not
the full customer service**. Cloudflare Free does not host the current Python,
PostgreSQL, and gVisor architecture. All account and execution requests return
explicit no-store HTTP 503 responses; the UI displays the limitation and directs
visitors to local setup. No accounts, secrets, private customer sources, or fake
test results were created or imported. The original full Next.js deployment and
standalone Python engine remain available; backend hosting is still outstanding.

Added a pinned vinext beta deployment target, a fixed public catalog build, a
Worker boundary for unavailable services, repeatable deployment instructions,
and a CI job that builds/dry-runs the Worker and exercises it locally without
Cloudflare credentials. Configuration is in apps/web/wrangler.jsonc and
vite.config.ts; instructions are in docs/cloudflare.md. Build outputs, generated
catalog/runtime types, Wrangler state, and credentials remain ignored.

Validation: `npm --prefix apps/web run build:cloudflare` passed including worker
TypeScript checking; `wrangler deploy --config dist/server/wrangler.json --dry-run`
passed; `npm --prefix apps/web run typecheck` and the ordinary Next production
build passed. The 44-request check passed both locally and against public HTTPS:
11 pages/deep links, catalog plus all 24 records, two unknown paths, and six
unavailable-service cases. Evidence: artifacts/cloudflare/{build,dry-run,deploy,
next-build}.log and {local-check,public-check}.json. Public browser navigation
opened the library and the actual drill contract; signup displayed the precise
unavailable explanation with zero credential inputs. The new site was inspected
at desktop and phone sizes before handoff.

During integration, pinned react-server-dom-webpack to the existing React
19.2.8 release after the initializer selected an incompatible newer peer. Fixed
rendered security headers and separated generated Workers globals from Next
types. Next typecheck regenerates route metadata because vinext also writes
generated routes. A stale local preview caused a missing dynamic asset during a
rebuild; the current public bundle passed fresh browser navigation. Existing
Python, database, billing, and sandbox code did not change. Previous 341 Python,
15 browser, and 477 mutation acceptance remains the applicable engine evidence;
it is not a claim that those suites were rerun for this website-only change.

Next: connect a separately hosted production API, private PostgreSQL, TLS email,
and supervised Linux gVisor worker before offering hosted customer accounts and
agent runs. This needs new infrastructure; the free website does not imply those
services exist. Free Workers request/CPU limits apply; see docs/cloudflare.md.
