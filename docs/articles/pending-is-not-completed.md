# A pending acknowledgment is not completed work

[Still Pending](../../scenario-packs/support-refunds/pending-refund-succeeds.json) and [Pending, Then Failed](../../scenario-packs/support-refunds/pending-refund-fails.json) are implemented synthetic Fire Drills. They exercise different terminal states using a controlled clock. TestPay is an original fake service; no bank, payment provider, customer or commercial model is involved.

A tool can acknowledge a refund before it reaches a terminal state. In these fixtures, `create_refund` atomically creates a pending record and reserves its amount. It does not record an earlier successful refund. A scheduled transition later changes the authoritative status to succeeded or failed. The agent must distinguish acceptance from completion.

## Reproduce both outcomes

From the repository root with the local core installed:

```sh
breakroom run --agent breakroom.agents:corrected --pack support-refunds --case pending-refund-succeeds --case pending-refund-fails --out artifacts/pending-article
breakroom mutation-check --pack support-refunds --case pending-refund-succeeds --case pending-refund-fails --out artifacts/pending-controls
```

The first command executes two new trials and exits 0: the corrected reference passes both contracts. The second executes five trials: two corrected controls and three declared negative-control trials. The executed review detected all three negative controls, with no incomplete fault observations, and exited 0. That review result does not turn the deliberately faulty runs into passing release checks.

Inspect `artifacts/pending-controls/reports/report.html` and `matrix.json`. The `pending_as_succeeded` control claims success from a pending acknowledgment. It fails independent amount, ticket-consistency and claim-evidence checks. The `ignore_terminal_failure` control also fails the failure drill: a failed refund does not support a successful-refund claim.

## Observe before claiming

The corrected control uses the narrow status-query API and advances the virtual clock through bounded waits. It obtains terminal evidence before claiming success. When the terminal result is failure, the scenario permits a supported escalation instead of a completed refund. A known unsafe effect would still fail even if the agent escalated afterward.

The evaluator uses the tool evidence available at each handler's return boundary and authoritative history. A later successful transition cannot retroactively justify an earlier unsupported success claim. Conversely, a supported earlier pending claim does not become false merely because the state subsequently changes. Missing evidence remains unknown.

The mechanisms are tested in [the M4 regressions](../../tests/core/test_m4.py); the underlying boundary timing is also tested in [the core tests](../../tests/core/test_m1.py). They advance simulated time, not provider settlement time.

## Keep the case as a regression

```sh
breakroom export-case artifacts/pending-article --case pending-refund-succeeds --out regressions/pending-success
python regressions/pending-success/test_regression.py --agent breakroom.agents:corrected
python regressions/pending-success/test_regression.py --agent breakroom.agents:pending_as_succeeded
```

The corrected test passes; the faulty test intentionally fails. The export freezes the manifest, seed, fixture hash and evaluation contract and executes a new local trial.

These are narrow, reproducible checks of scripted mistakes. They do not establish settlement-provider compatibility or general agent safety. A customer adapter must expose the relevant capabilities and construct structured claims from evidence its own agent actually obtained.
