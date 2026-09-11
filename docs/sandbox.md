# Customer agent sandbox

The authenticated workspace can now accept a Python agent ZIP or a GitHub commit, queue repeated Fire Drills, execute the adapter in an isolated container, and retain independently evaluated trial evidence. This is an explicit extension to the original local-execution product scope. The anonymous demo still runs fixed built-ins only. Source uploads are never executed by the web/API service.

## Customer workflow

1. Sign in, open a private project, and select **Agent sandbox**.
2. Download the starter ZIP. Replace `agent.py` with your adapter. Keep `breakroom-agent.json` at the archive/repository root:

   ```json
   {"version":1,"entrypoint":"agent:run","capabilities":["payments","tickets","reconciliation","virtual_clock","events"]}
   ```

3. Upload a ZIP, or enter `owner/repository` and a full 40-character GitHub commit SHA. Private repository imports accept a transient repository-read token; it is not stored. Use a small dedicated agent repository. GitHub archive redirects are restricted to its archive host and bearer credentials are stripped on that redirect.
4. Select a saved version and the drills. Quick check selects Missing Response; All 24 drills uses seed 0; Stress selects 24 drills × three reviewed seeds. Custom selections and repetitions are supported within 72 trials.
5. Start the run. Inspect each trial’s observed calls, committed refunds, independent checks, execution status and verdict. Download the full run or individual trial JSON. Cancel active work; partial evidence remains visible. Delete terminal runs or an inactive agent version when no longer needed.

A small [importable GitHub example](https://github.com/prakharsingh1/breakroom/tree/5d1506042a45cc2ddad3968798143d96e86bd5f8) is available: repository `prakharsingh1/breakroom`, commit `5d1506042a45cc2ddad3968798143d96e86bd5f8`. This separate example commit has its manifest at the root and meets archive limits.

The starter is a scripted example. Choosing `agent:faulty` in its manifest demonstrates the lost-response duplicate refund; `agent:corrected` demonstrates recovery. Neither is an AI model benchmark or an automatic repair of your code. Source names never select verdicts.

## Adapter interface and supported execution

```python
from breakroom.models import AgentResult

def run(task, tools, context):
    # Call the injected fake tools; return claims tied to their evidence refs.
    order = tools.get_order(task.order_id)
    # Optional, only when a model was enabled and authorized for this run:
    # answer = context.model("Your bounded text prompt")
    return AgentResult(customer_text="No action taken yet.")
```

This minimal example intentionally does not pass completion drills. Start from the downloaded complete adapter to learn refund reconciliation, operation keys, ticket versions and evidence-supported claims. Supported fake operations are the `SupportTools` interface in the core. `context.now`, `sleep`, `check_budget`, `cancelled`, `logical_operation_id` and `deadline` retain the local adapter meaning; sleeps advance virtual time.

The first runtime supports Python 3.12, its standard library and bundled pure Python modules. It does not install dependencies, run build hooks, accept Dockerfiles/custom images, or provide arbitrary networking/native packages. Framework-specific agents need a small adapter; provider SDK calls should be replaced with `context.model` for this runtime. Model access is synchronous text generation, not streaming, multimodal, SDK or automatic tool-calling emulation. The guest receives only task/tool/context data and optional model text. The entire engine, oracle, scenario manifest, SQLite business records and stored provider keys stay in the trusted worker.

ZIP bounds: 1 MiB compressed, 4 MiB expanded, 128 files, 256 KiB per file, UTF-8 Python/text/data only. Paths, collisions, symlinks, special files, encrypted entries, hidden credentials and adapter-interface replacements are rejected. Ordinary `.gitignore`, `.gitattributes`, `.github` and license metadata are skipped. `requirements.txt` and `pyproject.toml` are rejected because runtime installs are unsupported. Remove embedded credentials before uploading; this is not a source secret scanner.

## Model providers and costs

Owners save or revoke AES-256-GCM encrypted OpenAI and Anthropic keys. Encryption authenticates project, record type and ID; keys are never returned after saving. Developers can use project keys only for explicitly authorized, bounded runs. Replacing a key revokes the previous record for already queued/running jobs.

Operator model allowlists default to `gpt-4.1-mini` and `claude-sonnet-4-6`; availability depends on the customer’s provider account. The broker uses only fixed HTTPS provider endpoints, disables redirects and environment proxies, caps prompt bytes at 16 KiB, and caps output at 2,048 tokens per request. At most 100 requests can be reserved per job; default is 20 requests × 512 output tokens. Failed outbound attempts consume call budget; unknown returned usage remains unknown. There are no automatic retries. Each request rechecks membership, cancellation, key revocation and budget. A request already sent may finish and be billed after cancellation/revocation.

Model prompts leave the isolated container through the selected provider. Provider terms, data handling and account spending limits apply. Limits bound calls and requested output; they are not a fixed price or dollar spending cap. No live model request is made when saving a key. The automated provider tests use offline HTTP transports and synthetic keys, never customer model spending.

## Isolation, jobs and limits

Production workers require `runsc`/gVisor on a dedicated Linux worker host. Every trial uses a new non-root container with no network, a read-only root filesystem, all capabilities dropped, no-new-privileges, 512 MiB memory/swap ceiling, one CPU, 64 processes, 128 open files, no core dumps and a 64 MiB temporary filesystem. No host bind mounts or Docker socket enter the guest. The operator image is resolved to an immutable image ID recorded on the job. Bounded source data is sent over stdin into the guest’s temporary filesystem; no customer code is imported on the host. Per-trial global agent state survives sequential/concurrent invocations within that trial only.

The trusted worker validates and dispatches bounded RPC calls in the original simulator invocation threads; actual concurrent handlers remain concurrent. A guest cannot supply its verdict or directly modify the authoritative database. Output and tool-call bounds turn crashes, malformed claims, infinite loops and protocol abuse into incomplete evidence. Known failing checks remain failures; missing trials, unsupported capabilities, untriggered faults and interrupted infrastructure cannot pass a release gate.

Limits: two queued/running jobs per project, 20 retained jobs, 10 deployments, 72 trials per job, 60 seconds per trial and 15 minutes per job. Queue waits expire after 15 minutes. Row locks make claims and model budget reservations atomic; request idempotency prevents duplicate submissions. Heartbeat loss marks interrupted jobs incomplete without replaying possibly billed model requests. A known FAIL survives interruption. Jobs follow project report retention, including shortening it; project deletion cascades through source, keys, runs and evidence. Deleting a deployment deletes its terminal runs. Source and provider keys remain until explicitly deleted or the project is deleted. Backups require a separate retention/deletion policy.

The worker must run under a restarting process supervisor. It labels containers with a hard expiry and reaps only expired Breakroom containers in its own installation scope. If the worker is killed, its restarted process cleans orphaned containers. Do not operate an unsupervised production worker. A stopped host/runtime requires operator recovery; no UI claim substitutes for availability monitoring.

## Operator setup

Public hosting is not configured in this checkout. Supply HTTPS, the account/email configuration in [self-hosting](self-hosting.md), private PostgreSQL, a dedicated Linux worker/daemon, encrypted backup storage and the sandbox vault key before admitting customer code. Docker daemon access is privileged: the trusted worker must not share a daemon or host with the web/API, production workloads or unrelated secrets. Follow gVisor’s production guide; this feature is not a security certification or proof that all escapes are impossible.

1. Install a maintained gVisor release and configure Docker’s `runsc` runtime on the dedicated worker. CI pins official release `20260907.0` and checks its release SHA-256; `scripts/ci-install-gvisor.sh` is restricted to disposable Linux CI and must not be used as a production installer.
2. Build the reviewed guest image:

   ```sh
   docker build -f infra/Dockerfile.sandbox -t breakroom-sandbox:local .
   ```

3. Generate a random 32-byte base64 vault key in an operator secret manager. Configure the **same key** for API and worker. Do not commit it or expose it to the browser/guest. Back it up separately from the database. Losing it makes stored source and model credentials unreadable. Rotation requires a reviewed decrypt/re-encrypt maintenance operation; changing the environment alone is not rotation.
4. Configure API and worker environment:

   ```sh
   BREAKROOM_SANDBOX_ENABLED=1
   BREAKROOM_SANDBOX_RUNTIME=runsc
   BREAKROOM_SANDBOX_IMAGE=breakroom-sandbox:local
   BREAKROOM_SANDBOX_TRUSTED_DEV=0
   # Inject BREAKROOM_SANDBOX_VAULT_KEY from your secret manager.
   # Configure matching BREAKROOM_TEAM_DATABASE_URL and BREAKROOM_TEAM_DB_SCHEMA.
   # Set BREAKROOM_TEAM_ENV=production and the real HTTPS origin in production.
   ```

   Optional allowlists: `BREAKROOM_SANDBOX_OPENAI_MODELS` and `BREAKROOM_SANDBOX_ANTHROPIC_MODELS`, comma-separated. The API needs outbound GitHub access; only the worker broker needs outbound model-provider access. Restrict egress to their fixed hosts. The guest network remains disabled.
5. Install the pinned team Python dependencies, migrate the database, and start the dedicated worker using the reviewed service template in `infra/breakroom-sandbox-worker.service` with operator-specific paths/account/secret file:

   ```sh
   PYTHONPATH=apps/api:packages/breakroom-core/src venv-team/bin/python -m breakroom_api.sandbox.worker
   ```

   Set a restart policy, health/queue monitoring and log redaction. API `/sandbox` reports whether a matching worker heartbeat is fresh. The optional `infra/compose.sandbox.yaml` configures the control plane without mounting a container socket; it does not provision a worker host.

For **trusted local fixture development only**, explicitly set `BREAKROOM_SANDBOX_RUNTIME=runc` and `BREAKROOM_SANDBOX_TRUSTED_DEV=1` in a development/test environment. The UI labels this restriction. Production refuses it. Do not use this mode to execute unreviewed customer uploads. macOS Docker fixture tests do not establish Linux gVisor isolation; the separate CI job exercises that runtime.

## Verification and references

`tests/sandbox` tests archive attacks, redirect/credential boundaries, tenant access, CSRF, atomic claims/budgets, revocation, retention, restored encryption, actual faulty/corrected effects, all 24 drills, concurrency, timeout/cancellation and forbidden guest access. `tests/e2e/sandbox.spec.ts` executes ZIP upload → real container → saved evidence/downloads and responsive accessibility checks. Runtime/browser checks are explicitly opt-in and skips are not passes. See [STATUS](../STATUS.md) for actual command outcomes and remote CI results.

Official interfaces reviewed 2026-09-11: [gVisor installation](https://gvisor.dev/docs/user_guide/install/), [gVisor security model](https://gvisor.dev/docs/architecture_guide/security/), [Docker runtime setup](https://gvisor.dev/docs/user_guide/quick_start/docker/), [GitHub commit ZIP archives](https://docs.github.com/en/rest/repos/contents#download-a-repository-archive-zip), [OpenAI Responses](https://developers.openai.com/api/reference/cli/resources/responses/methods/create), [GPT-4.1 mini](https://developers.openai.com/api/docs/models/gpt-4.1-mini), [Anthropic Messages](https://platform.claude.com/docs/en/api/http/messages/create), [Anthropic versioning](https://platform.claude.com/docs/en/api/versioning), [Sonnet 4.6](https://platform.claude.com/docs/en/models/sonnet-4-6/overview). Tests validate the implemented subset, not provider conformance beyond it.
