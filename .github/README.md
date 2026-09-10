# Prepared repository checks

`workflows/ci.yml` is local configuration for Python 3.12 and Node 24. It checks source whitespace, core/runner/artifact tests, the real scripted demo, API contracts, TypeScript, a production build, and the Playwright journey. It has read-only repository permission, no secrets, no deployment/publishing steps, and no periodic schedule. It does not create a repository or enable a remote service by itself. Actual local or remote execution evidence belongs in `STATUS.md`.

The action inputs and current v7 usage were checked against the official [checkout](https://github.com/actions/checkout), [setup-python](https://github.com/actions/setup-python), and [setup-node](https://github.com/actions/setup-node) documentation on 2026-09-07. Browser installation follows [Playwright's CI guide](https://playwright.dev/docs/ci-intro). Playwright uses the installed pinned package to select its matching Chromium revision; dependencies and builds are required before the browser smoke test.

The issue forms request synthetic reproductions, exact versioned contracts, executed controls, and contribution rights. They must not be used to disclose credentials or private customer cases without separate permission. `SECURITY.md` describes the owner-configurable security contact gap.
