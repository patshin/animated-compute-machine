# Deployment

AGENT//TRACE has two deployment surfaces:

1. A static GitHub Pages product tour with no backend or persistence.
2. The complete single-node studio with React, FastAPI, SQLite, and a Caddy reverse proxy.

## GitHub Pages product tour

The workflow at `.github/workflows/pages.yml` publishes the contents of `site/`. It runs on every push to `main` and can also be started manually.

The Pages tour is intentionally static. It demonstrates navigation, graph stepping, state inspection, evaluation views, protocol boundaries, and controlled fault injection. It does not claim to provide the backend runtime.

## Loopback-only Docker deployment

Use this path for local evaluation of the hardened public-preview profile.

```bash
cp .env.example .env
docker compose config --quiet
docker compose build --pull
docker compose up -d
curl --fail http://127.0.0.1:47800/api/ready
```

Only the proxy binds a host port, and it binds to `127.0.0.1` by default. The API and web containers remain on internal networks.

## HTTPS deployment

Requirements:

- a Linux host with Docker and Compose;
- a DNS record pointing a dedicated hostname to the host;
- inbound TCP 80 and TCP/UDP 443;
- a backup destination outside the application volume.

Set the deployment variables in `.env`:

```dotenv
AGENT_TRACE_PUBLIC_HOST=trace.example.com
AGENT_TRACE_ACME_EMAIL=ops@example.com
AGENT_TRACE_PUBLIC_ORIGIN=https://trace.example.com
AGENT_TRACE_IMAGE_TAG=0.1.0
```

Validate and start:

```bash
docker compose -f compose.yaml -f compose.https.yaml config --quiet
docker compose -f compose.yaml -f compose.https.yaml build --pull
docker compose -f compose.yaml -f compose.https.yaml up -d
curl --fail https://trace.example.com/api/ready
```

The header must show `PUBLIC PREVIEW / SAFE LAB MODE`.

## Required public-preview boundaries

- Keep `AGENT_TRACE_DEPLOYMENT_PROFILE=public-preview`.
- Keep the API at one replica because the bundled SQLite runtime is single-instance.
- Do not expose the API or web container port directly.
- Do not enable the optional provider subprocess in a public preview.
- Keep request-body limits, rate limits, timeouts, read-only filesystems, dropped capabilities, and non-root users enabled.
- Keep the development server and local profile private.
- Back up the persistent volume before every upgrade.

## Backup and restore

Create a backup from the repository root:

```bash
./scripts/backup.sh ./backups
```

Verify the backup:

```bash
./scripts/verify-backup.sh ./backups/<backup-file>
```

Restore only during a planned maintenance window after stopping the application:

```bash
docker compose down
./scripts/restore.sh ./backups/<backup-file>
docker compose up -d
```

Store backups outside the repository and outside the Docker volume. Test restoration before relying on the backup policy.

## Upgrade

1. Create and verify a backup.
2. Record the currently deployed Git tag and image tag.
3. Pull the intended release tag.
4. Rebuild images with the new `AGENT_TRACE_IMAGE_TAG`.
5. Start the stack and check `/api/ready`.
6. Exercise one deterministic scenario, one security case, and one evaluation batch.
7. Keep the previous image tag until the acceptance window closes.

## Rollback

1. Stop the new stack.
2. Restore the previous Git tag and image tag.
3. Restore the pre-upgrade backup if the data format changed.
4. Start the stack.
5. Verify `/api/ready` and a deterministic scenario.

## Operational limits

The included deployment is designed for one public learning instance. It is not a multi-tenant service, a horizontally scaled API, or a high-availability database architecture. For broader access, place it behind an additional authentication layer and replace the persistence and rate-limiting design with infrastructure appropriate to the intended load and trust model.
