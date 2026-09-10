# Retrying the ticket should not repeat the payment

[Fire Drill — Half a Job](../../scenario-packs/support-refunds/ticket-write-failure.json) is a synthetic two-step workflow failure. The refund succeeds; the first ticket-note write receives a transient error before that ticket write commits. The business operation is partly complete, so retrying the entire workflow can duplicate a payment that already happened.

The faulty control in this drill is [the repeated-workflow agent](../../examples/agents/repeat_workflow.py). It creates the refund, tries the ticket operation, catches the ticket error, and starts the whole sequence again with a fresh refund key. The second refund is a real new state transition. A successful final ticket update does not undo the extra payment.

The corrected reference retries only the ticket step. It preserves the payment outcome and uses the logical operation ID for ticket writes, allowing a repeated note/status call to refer to the same intended action. The ticket tools also support an explicit version precondition so a stale writer does not silently overwrite a concurrent edit.

## Reproduce the partial workflow failure

Install the core from the repository, then run these commands separately. The baseline intentionally exits 1:

```sh
python -m pip install -e ./packages/breakroom-core
breakroom run --agent examples.agents.repeat_workflow:run --pack support-refunds --case ticket-write-failure --out artifacts/half-job-baseline
breakroom run --agent examples.agents.corrected:run --pack support-refunds --case ticket-write-failure --out artifacts/half-job-candidate
breakroom compare artifacts/half-job-baseline artifacts/half-job-candidate
```

The independent evaluator records two refund effects for the repeated-workflow control and returns `FAIL`. The corrected execution returns `PASS`; its recorded invocation counts are one `create_refund` and two `append_note` calls. Those counts distinguish retrying the failed ticket step from repeating an already completed payment. The before-commit ticket error contributes no accepted note of its own.

Inspect `artifacts/half-job-baseline/report.html` and `artifacts/half-job-candidate/report.html`. Follow the event order from `refund_committed` to the `append_note` tool error, then to subsequent payment/ticket effects. `ticket_target` checks ownership; `ticket_consistent` checks the associated resolved ticket and note; money checks inspect the refund records independently. An agent's completion text cannot override an excessive refund total.

## Export the contract

```sh
breakroom export-case artifacts/half-job-baseline --case ticket-write-failure --out regressions/half-job
python regressions/half-job/test_regression.py --agent examples.agents.corrected:run
python regressions/half-job/test_regression.py --agent examples.agents.repeat_workflow:run
```

The exported test detects the deliberately repeated workflow when run again. Connect your application's tool vocabulary using the [complete customer adapter](../../examples/customer-adapter/adapter.py), then run the same test against that trusted local module. Structured claims must contain the evidence references actually obtained from tools. Unparseable free text remains unsupported evidence.

This fixture covers one temporary ticket failure after a successful immediate refund. It is not evidence that every distributed workflow, compensation policy, or help-desk concurrency behavior is modeled. TestPay and TestDesk remain generic synthetic services. The next useful coverage extension must specify its own trigger, authoritative state, allowed outcome, and executed negative control.
