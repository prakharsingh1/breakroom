# Implementation references

Reviewed 2026-09-07. These references inform API usage and limitations; they do not establish TestPay as provider-compatible.

- [Python SQLite](https://docs.python.org/3/library/sqlite3.html): transactional local business state; separate trial databases and atomic operation registry.
- [Python subprocess](https://docs.python.org/3/library/subprocess.html): local worker lifecycle and independent wall-clock limits. A process boundary is not a malicious-code sandbox.
- [Next.js installation](https://nextjs.org/docs/app/getting-started/installation): App Router, TypeScript and supported Node runtime. Use Next.js as required by the project, with no Sites registration or deployment.
- [Next.js rewrites](https://nextjs.org/docs/app/api-reference/config/next-config-js/rewrites): a fixed operator-configured API origin for local same-origin browser calls; never a visitor-selected target.
- [React](https://react.dev/learn): components, state and accessible semantic DOM.
- [axe-core Playwright integration](https://github.com/dequelabs/axe-core-npm/tree/develop/packages/playwright): pinned 4.13.0; `AxeBuilder({page}).withTags(...).analyze()` checks rendered pages. Automated checks supplement keyboard and visual review; they are not an accessibility certification.
- [Next.js Proxy](https://nextjs.org/docs/app/api-reference/file-conventions/proxy): server-only forwarded headers for the fixed local team service; browser-supplied proxy secrets are discarded.
- [Playwright web servers](https://playwright.dev/docs/test-webserver): local API/web startup and real-browser regression tests.
- [Stripe idempotent requests](https://docs.stripe.com/api/idempotent_requests): provider-inspired distinction between same-key replay and parameter conflict. TestPay is an original narrow simulation with its own contract and key lifetime.
- [Stripe refund object](https://docs.stripe.com/api/refunds/object): pending and terminal results are distinct. TestPay does not implement the complete provider schema or payment methods.
- [Stripe error handling](https://docs.stripe.com/error-handling): uncertain responses must not be interpreted as proof no effect occurred.

All included cases are synthetic. No provider sandbox contract tests have been run. Compatibility with Stripe, Zendesk, Shopify or any real service remains unverified. No external model benchmark or production safety claim is made.
