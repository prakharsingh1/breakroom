# Breakroom implementation plan

Authoritative source: `BREAKROOM_CODEX_SPEC.md`. The initial directory contains only that document and is not a Git checkout. No migration or existing application is required.

## Architecture

- `packages/breakroom-core`: Python 3.11+ typed local simulator, bounded scenario data, independent evaluator, process runner, CLI, versioned reports and exports. Each trial owns a SQLite database. Synthetic tools never contact customer services.
- `scenario-packs/support-refunds`: reviewed JSON manifests and versioned compatibility records.
- `examples`: scripted agents and the local customer adapter contract.
- `apps/api`: FastAPI service for fixed built-in demo runs; authenticated sandbox control plane is separate from customer execution workers.
- `apps/web`: Next.js/TypeScript interface using reports from actual engine executions.
- `tests`: core invariants, adversarial controls, artifact contracts, API integration, browser journeys.
- Hosted PostgreSQL reporting follows the validated local product and coverage pack. Billing follows team features; neither is assumed available.

## Milestones and acceptance

1. **M0 / inspection:** read the complete spec, establish contributor guidance and factual tracking. Preserve the source spec unchanged.
2. **M1 / real engine:** ordinary refund and lost response; TestPay/TestDesk, atomic operation registry, deterministic fixtures, scripted faulty/corrected agents, independent assertions. Demonstrate two refunds versus one from actual records. Test currency, balance, ownership, conflicting keys, concurrent calls and evidence precedence.
3. **M2 / usable local product:** complete cases 03–05, bounded local subprocess adapter, CLI, JSON/HTML/JUnit, compatible comparison, regression export, clean editable install. Test timeouts/cancellation, validation, false claims and executable exports.
4. **M3 / Cut A:** build the website only after core validation. Real allowlisted demo API, evidence inspector, comparison, library/details, docs/pricing, useful errors and export. Build/type checks and browser journey at 390/768/1440 pixels; inspect screenshots.
5. **M4 / Cut B:** implement cases 06–24 with actual distinct mechanisms, event adapter, overlapping concurrency and expected mutant detection. Version packs/oracles, repeated trials, private pack validation, contribution and compatibility documentation, local CI workflow.
6. **M5 / team reporting:** PostgreSQL, maintained auth, server-enforced roles, scoped hashed keys, explicit bounded redacted report uploads, private suites, deletion/retention and cross-tenant tests. Never equate customer-generated evidence with independently verified truth.
7. **M6 / optional billing:** tested provider adapter and signed fixture events only after team features; entitlement and lifecycle tests, operations/backup validation. Live activation and merchant approval remain external prerequisites.

## Verification and handoff

Record tested pinned runtime/dependency versions and official API references. Run milestone tests and fix actual defects; no weakened assertions. Run documented commands in an isolated installation. Do not count planned, skipped, unsupported or untested work as completed. Prepare local artifacts and release documentation without publishing. At a session boundary report completed gates, exact commands/results, artifact paths, limitations and the next executable task.


## Customer product upgrade (2026-09-10)

Owner requested email/password accounts, a richer customer workspace and public GitHub source at prakharsingh1. Implement durable accounts with verification/recovery, guided first-project onboarding, actual report summaries and recommendations, visual suite creation and email-bound invitation links. Preserve local-only agent execution and explicit minimized uploads. Verify backend, real browser journeys, responsive screenshots and backup restoration before publishing the reviewed source. External hosting and SMTP remain owner configuration; public GitHub publication is explicitly authorized.


## Hosted agent sandboxes (owner-directed extension, 2026-09-11)

The user explicitly requests customer-agent deployment and stress testing, choosing both ZIP uploads and GitHub repositories, plus approved model providers with customer-supplied keys. This extends the original local-only customer-execution scope. The anonymous demo remains an allowlist of built-ins. Hosted execution belongs to a separate authenticated worker boundary; the web/API process never imports customer code.

1. Repair and pass the initial remote customer-browser gate, preserving the existing account/report product.
2. Add bounded Python source packages and pinned GitHub imports; encrypted tenant-scoped source/key records; explicit resource/model-call budgets; durable jobs, cancellation, expiry and reports.
3. Keep authoritative simulator state and evaluation outside the guest. Bridge only supported fake tools and bounded model calls over a validated protocol; expose neither provider keys nor database/host access to uploaded agents.
4. Use a dedicated container worker with gVisor for untrusted production workloads. Standard Docker is an explicit development-only mode for trusted test fixtures. Reject production execution if the required isolation is unavailable.
5. Build project UI for deployment, credentials, test selection, run progress, cancellation and failure evidence. Include adaptable starters and clear integration/runtime limits.
6. Verify malformed packages/RPC, import redirects, secrets, tenant controls, cancellation/quotas, independent verdicts, real container runs, browser acceptance and remote CI.

External hosting, dedicated execution-host provisioning and actual provider credentials remain unconfigured. Do not claim external deployment, live model execution or universal safety from local/offline tests.

Sandbox implementation and local gates complete; next gate is the real Linux gVisor workflow and final public source verification. Operator hosting/provisioning remains external.
