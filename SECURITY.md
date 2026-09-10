# Security and privacy scope

Breakroom supplies fake business services. Built-in reference agents use simulated customer records and money. Do not pass production payment or help-desk credentials to the tools.

A customer adapter is trusted executable Python on the customer's own computer or CI. A subprocess supplies lifecycle control, not a security sandbox. Review code before importing it. The built-in demo service accepts only fixed audited implementations and scenarios; it must never execute uploaded code or fetch arbitrary user targets.

Local artifacts can include agent-generated text. Treat them as sensitive when adapting a real agent; inspect and minimize reports before sharing. The team service uses explicit minimized uploads, Argon2id password accounts or optional OIDC, project roles/scoped keys, CSRF protection, bounded imports, expiry and deletion. Password recovery and verification use hashed, expiring single-use tokens; password changes and resets revoke old sessions. Invitation links require a matching verified email. The local development identity is prohibited in production. Production requires HTTPS and TLS email delivery for password accounts; public hosting and external mail delivery are not configured. Checksums describe integrity, not the truth of a customer-generated report.

Optional billing uses only test credentials, SDK-verified webhook signatures, durable retry identities and server-side entitlements. The provider adapter rejects live mode; default local services do not contact a payment provider. Billing operations are separate from the simulator’s TestPay.

Read [operations and recovery](docs/operations.md) before retaining customer data. Backups are sensitive and can restore deleted records or revoked access unless an operator replays the documented deletion/access ledger and invalidates restored credentials. No external backup storage or automatic backup schedule is configured.

The owner has not configured a security contact or disclosure channel. Do not send secrets or real customer information through public issues. Before public release, the owner must choose a private reporting mechanism. No response-time promise or certification is implied.
