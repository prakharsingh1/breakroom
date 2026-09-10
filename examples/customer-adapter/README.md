# Bring one local agent

`adapter.py` is a complete offline integration example. `ApplicationTools` maps an application's `refund` and `finish_ticket` operations to injected TestPay and TestDesk methods. Replace the application logic with your own framework code; retain actual structured claims and tool evidence references.

From the repository root:

```sh
python -m pip install -e ./packages/breakroom-core
cd examples/customer-adapter
breakroom run --agent adapter:run --pack support-refunds --out artifacts/customer
```

The supported interface is `run(task: TaskEnvelope, tools: SupportTools, context: AgentContext) -> AgentResult`. Money is integer minor units plus explicit currency. Keep `context.logical_operation_id` stable throughout retries; refund and ticket steps are retried independently.

The task provides authenticated requester data, logical request ID, known customer/order/ticket references, requested money, text, and synthetic policy. Context provides virtual time, deadline, cancellation, and controlled sleep; it does not provide future faults or the answer. Responses carry `evidence_ref`; preserve these alongside claims such as `refund_succeeded` and `ticket_updated`. Free text alone is not verified evidence.

This example supports the initial five synchronous completion drills. It deliberately escalates unsupported terminal/identity outcomes instead of fabricating a completion claim. Such escalation still fails a drill requiring recoverable completion. It is not universal framework integration or a model benchmark.

Imports execute trusted local code. The worker is not a security sandbox. Default runs receive a minimal environment without inherited service secrets; your code can independently read files or access a network. Use the optional no-network container in `docs/local-runner.md` for stronger network isolation. Never connect these tool mappings to live payment or help-desk services.
