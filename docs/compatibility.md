# Simulator compatibility record

Reviewed 2026-09-07. TestPay and TestDesk are original synthetic services. All current pack fixtures are synthetic. No provider sandbox contract test has been executed, and no live account credentials are required or used by the reference suite.

| Contract area | Breakroom scope | External compatibility |
|---|---|---|
| Amounts and currencies | Integer minor units, explicit currency, order ownership, available refundable balance | Generic simulation; no complete currency/provider payment-method model |
| Operation keys | Atomic SQLite key/effect transaction, same-parameter replay, changed-parameter conflict, distinct authorized requests | Provider-inspired concepts; no claim of equivalent HTTP response caching, key expiry or concurrency responses |
| Refund lifecycle | Scenario-defined acceptance, state transitions and independently checked terminal claims | Narrow simulated states; no settlement, bank network, chargeback or full provider refund schema |
| Ticket writes | Generic ticket versions, ownership, operation metadata and logical workflow updates | No Zendesk, Intercom, Shopify or other help-desk API conformance |
| Fault and event delivery | Declarative synthetic triggers and audited supported adapter capabilities, with actual coverage checks | No external webhook transport, signing, redelivery timing or provider sandbox verification |
| Customer adapter | Local Python `run(task, tools, context) -> AgentResult`; capability requirements remain explicit | Framework-neutral interface, not automatic support for every agent framework |

The precise implemented behavior is the selected versioned manifest, engine/oracle versions and its recorded controls. Reading a planned case description or finding a helper in the simulator does not establish that its adapter path and oracle work. `breakroom mutation-check` generates the applicable executed evidence; unsupported and incomplete observations prevent its all-clear gate.

The official [Stripe idempotent-request documentation](https://docs.stripe.com/api/idempotent_requests) describes response caching and parameter checks with provider-specific lifetime and execution rules. Breakroom uses that material to motivate explicit uncertainty/key semantics; its isolated key lifetime and fake responses differ. The official [refund object](https://docs.stripe.com/api/refunds/object) distinguishes pending and terminal states and includes provider fields outside Breakroom's model. The official [webhook guidance](https://docs.stripe.com/webhooks) discusses event transport that this local simulation does not certify. These pages were checked on the review date above; no Stripe API version has been sandbox-tested here.

A future provider-sandbox record must state the owner's authorization, sandbox-only endpoint/account, exact provider API version, test date, supported operations, redacted evidence locations, source/fixture hashes, observed differences, and remaining unsupported behavior. Keep external-provider tests opt-in and skipped without credentials. A skip is not a pass. Never run a provider contract test against a live payment account by default.

Private customer reproductions stay private. Provider documentation does not grant rights to upload a customer's records or publish their incident. Propose a sanitized synthetic reproduction for separate owner review when helpful; do not silently add private material to the global pack.
