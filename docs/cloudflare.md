# Cloudflare free website deployment

Breakroom has an additional Cloudflare Workers build using vinext. The ordinary
Next.js + Python + PostgreSQL installation remains available and independent.

Live website: [breakroom.snghprakhar.workers.dev](https://breakroom.snghprakhar.workers.dev).
Deployed on 2026-09-11 to the existing Workers Free account. Initial verified
Worker version: `e0933432-ca89-45d3-ac06-288ccbec9f9e`.

## What this deployment provides

- The public website, documentation, pricing/availability, and all 24 reviewed
  synthetic Fire Drill contracts.
- HTTPS on the account's `workers.dev` subdomain.
- Local quickstart and self-hosting instructions.

This deployment does **not** host password accounts, private workspaces, the
Python test API, PostgreSQL, or customer-agent execution. The UI states this;
account and execution endpoints return HTTP 503 with an explanation and never
create sessions, accept customer sources, spend model budgets, or fabricate
reports. The catalog is built only from the checked-in synthetic scenario pack.

The full application needs the production services described in
[self-hosting.md](self-hosting.md) and the supervised Linux gVisor worker in
[sandbox.md](sandbox.md). Do not expose the local development services through a
public tunnel to fill that gap. Cloudflare Containers require Workers Paid and
are outside this free deployment.

## Rebuild and deploy

From the repository root:

```sh
npm --prefix apps/web ci
npm --prefix apps/web run build:cloudflare
npm --prefix apps/web run preview:cloudflare
```

The preview listens on `http://127.0.0.1:8787`. In another terminal:

```sh
npm --prefix apps/web run check:cloudflare
cd apps/web
npx wrangler deploy --config dist/server/wrangler.json --dry-run
```

Authenticate Wrangler with the intended account and confirm its Workers plan
is Free before the authorized upload:

```sh
npx wrangler login --scopes account:read user:read workers_scripts:write --use-keyring
npm run deploy:cloudflare
```

The Worker name is `breakroom`; no domain purchase, paid storage, model service,
Containers, database, or email service is provisioned by this configuration.
The generated bundle, catalog, local Wrangler state, and credentials are not
committed. The committed lockfile pins the build dependencies. vinext is beta;
validate this target separately from the normal Next.js build.

After upload, verify the returned URL:

```sh
BREAKROOM_CHECK_ORIGIN=https://breakroom.YOUR_SUBDOMAIN.workers.dev npm run check:cloudflare
```

The check verifies page/deep-link responses, security headers, all 24 catalog
records, unknown routes, and explicit rejection of unavailable service actions.
Also inspect a desktop and mobile browser, follow a drill link, and test a
direct signup URL. Public-site mode is built into the Cloudflare configuration;
setting an environment variable on an unrelated API does not enable accounts.
Connecting the backend requires a separately reviewed and tested gateway setup.

## Free-tier bounds

As checked on 2026-09-11, Workers Free includes 100,000 dynamic requests/day
with a 10 ms CPU allowance per invocation. Static asset requests are free.
Requests can fail when free limits are exhausted; this is not an unlimited
production service. No upgrade or overage plan is enabled by this deployment.
See Cloudflare's [limits](https://developers.cloudflare.com/workers/platform/limits/),
[pricing](https://developers.cloudflare.com/workers/platform/pricing/), and
[Container pricing](https://developers.cloudflare.com/containers/platform/pricing/).

To validate the existing full installation after working with vinext, run
`npm run typecheck` (which regenerates Next route types), then `npm run build`.
Both tools write generated route metadata, so do not run their builds in
parallel. Python behavior and database migrations are unchanged.
