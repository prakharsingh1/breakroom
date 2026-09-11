# Run Breakroom for your customers

The application includes real email/password accounts and persistent private workspaces. Publishing the source on GitHub does not host the application. Choose a server and domain to operate your own installation.

For a free public website on Cloudflare, see [the Cloudflare deployment guide](cloudflare.md). That website serves documentation and the drill catalog; the customer services below still need separate hosting.

## Local use

```sh
docker compose -f infra/compose.yaml -f infra/compose.team.yaml up -d --build --wait
```

Open `http://127.0.0.1:3000/signup`. Registration stores an Argon2id password hash and issues an opaque HttpOnly session cookie. Accounts, projects, suites and imported reports persist in the database volume. The local email service is unconfigured, so verification/recovery cannot send emails; the UI explains this. Local unverified accounts can work in their own projects, but password-account invitations require a verified address. The development identity form is an explicit test-only alternative.

## Production configuration

Use the standalone `infra/compose.production.yaml`, not the development overlay. Provide a private PostgreSQL database, exact HTTPS public origin and TLS SMTP sender:

```sh
cp .env.production.example .env.production
# Edit .env.production with your own database, domain and email settings.
docker compose --env-file .env.production -f infra/compose.production.yaml config --quiet
docker compose --env-file .env.production -f infra/compose.production.yaml up -d --build --wait
```

The example values are placeholders. The production template was parsed locally; it has not been deployed or validated with external credentials. Never commit `.env.production` or send its contents in logs. URL-encode database passwords and use a TLS connection to an external database. Secrets are read only by the team service. APIs and the database have no host-published ports in this template.

Put an HTTPS reverse proxy in front of the loopback web port `127.0.0.1:3000`. Forward `/api/team/` through the Next application to the private team service. The browser origin must exactly match `BREAKROOM_TEAM_PUBLIC_ORIGIN`. Configure DNS and certificates before testing customer accounts. The team service needs outbound connectivity to your PostgreSQL and SMTP hosts; the fixed demo worker stays on an internal network.

Production rejects development identity, requires TLS SMTP when password accounts are enabled, sets secure cookies, and blocks unverified accounts from workspaces while allowing their account/verification page. Email links use short-lived, single-use fragment tokens. Customers register, verify their email, create a project, connect their agent, and upload evidence. Owners create email-bound invitation links and share them themselves; the application does not send invitations automatically.

After configuration, exercise registration, delivered email verification, sign-out/sign-in, reset-password mail, a real local agent run/upload and a second-account invitation. Check `/api/team/health` for aggregate storage, cleanup, billing-worker and mail delivery status. A successful offline mail test does not establish that a real SMTP provider delivered mail.

## Operating scope

This is a bounded single-worker application. Durable account attempt limits survive restarts; requests forwarded through the same proxy share peer budgets. Configure shared ingress rate limits and review capacity before scaling to multiple replicas or large customer traffic. The server does not accept or execute arbitrary customer code; customer adapters stay on their own worker.

Arrange encrypted backups and test restores before accepting valuable customer data. Database snapshots include password hashes, hashed tokens and private evidence; restrict their access and disclose retention. Review [the operations guide](operations.md), [account contract](accounts.md), [workspace contract](workspaces.md), and [security scope](../SECURITY.md). Live billing remains disabled; no payments or merchant services are needed for the self-hosted workspace.
