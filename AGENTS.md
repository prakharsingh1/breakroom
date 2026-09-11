# Breakroom contributor instructions

Read `BREAKROOM_CODEX_SPEC.md` for the product contract and `STATUS.md` before continuing.
Preserve unrelated work. Keep the Python engine independent of the website.
Use integer minor units with explicit currency, per-trial SQLite transactions, and independent state assertions.
Never select verdicts by agent name. Unknown evidence and untriggered faults cannot pass a release gate.
Scenario manifests are bounded data, never executable code. Customer adapters execute locally as trusted code; subprocesses are not security sandboxes.
Public demo inputs must be allowlisted built-ins. Authenticated sandbox source deployments follow docs/sandbox.md: never execute source in the API; use the configured isolated worker, bounded archives, fixed broker targets and explicit model budgets. Never accept customer commands, images, host paths or arbitrary network targets.
Test each milestone before advancing; keep commands, outcomes, limitations, and next tasks in `STATUS.md`.
Do not publish, deploy, push, enable paid services, or send messages without explicit authorization.
