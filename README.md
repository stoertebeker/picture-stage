# Picture-Stage

**Self-hosted photo proofing for photographers and models.**

Picture-Stage lets photographers share image galleries with models for review. Models can select images for editing, mark favorites, and leave comments — all without creating an account. Self-hosted, open-source, and fully under your control.

> **Status:** v0.3 complete — backend, i18n (DE+EN), 181 tests green. Next: Frontend-Redesign (Tailwind build + vendored JS + visual overhaul).

## Features

- **Share galleries via magic link** — no model login required, optional password protection
- **Select, favorite, and comment** on individual images
- **Auto-save** — selections persist immediately (event-sourced)
- **Export selections** as CSV/JSON for Lightroom/Capture One
- **Server-side watermarking** — originals are never exposed to models
- **Pluggable storage** — local Docker volume or S3-compatible (MinIO, AWS, Hetzner, R2, B2)
- **Multi-tenant** — multiple photographers with admin approval registration
- **Admin user management** — list users, change roles, lock/unlock, delete, reset passwords
- **Multi-arch Docker image** — runs on amd64 and arm64 (Raspberry Pi, Synology)

## Installation (Docker)

```bash
# 1. Verzeichnis anlegen
mkdir picture-stage && cd picture-stage

# 2. Compose und Konfiguration herunterladen
curl -O https://raw.githubusercontent.com/stoertebeker/picture-stage/main/docker-compose.yml
curl -O https://raw.githubusercontent.com/stoertebeker/picture-stage/main/.env.example
cp .env.example .env

# 3. .env anpassen — diese Werte MÜSSEN von CHANGE_ME abweichen, sonst startet die App nicht:
#    SECRET_KEY         (generieren: python3 -c "import secrets; print(secrets.token_urlsafe(64))")
#    HMAC_SECRET_KEY    (generieren: python3 -c "import secrets; print(secrets.token_urlsafe(32))")
#    APP_URL            (öffentliche HTTPS-Domain, z.B. https://deine-domain.tld — sonst zeigen Share-Links auf den internen Host)

# 4. Starten
docker compose up -d

# 5. Öffnen — beim ersten Start erscheint der Setup-Assistent
#    http://localhost:8000
#    Erstelle dort den Admin-Account, danach ist die App einsatzbereit.
#    API-Docs: http://localhost:8000/docs
```

### Update auf eine neue Version

```bash
# Image-Tag in docker-compose.yml anpassen oder :latest verwenden, dann:
docker compose pull
docker compose up -d
```

> **Tag-Wahl:** `:latest` für die Produktion (nur stabile Releases), `:dev` für
> einen Test-Server (folgt jedem `main`-Commit). Details siehe [Docker Hub](#docker-hub).

## API Overview

### Authentication (`/api/v1/auth/`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/auth/signup` | Register (pending admin approval) |
| POST | `/api/v1/auth/verify-email/{token}` | Verify email address |
| POST | `/api/v1/auth/login` | Login, receive JWT |
| GET | `/api/v1/auth/me` | Current user profile |

### Galleries (`/api/v1/galleries/`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/galleries` | Create gallery |
| GET | `/api/v1/galleries` | List own galleries |
| GET | `/api/v1/galleries/{id}` | Gallery details |
| PATCH | `/api/v1/galleries/{id}` | Update gallery |
| DELETE | `/api/v1/galleries/{id}` | Delete gallery + images |
| POST | `/api/v1/galleries/{id}/share` | Generate share link |
| DELETE | `/api/v1/galleries/{id}/share` | Revoke share link |
| GET | `/api/v1/galleries/{id}/export` | Export selections (CSV/JSON) |

### Images (`/api/v1/galleries/{id}/images/`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/galleries/{id}/images` | Upload images (multi-file) |
| GET | `/api/v1/galleries/{id}/images` | List images with preview URLs |
| DELETE | `/api/v1/galleries/{id}/images/{id}` | Delete image |

### Guest API (`/g/`) — isolated router, no auth required

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/g/{token}` | Access shared gallery |
| POST | `/g/{token}/verify-password` | Unlock password-protected gallery |
| GET | `/g/{token}/images` | List images with signed preview URLs |
| POST | `/g/{token}/selections` | Submit selection event |
| GET | `/g/{token}/selections` | Current selection state |
| POST | `/g/{token}/complete` | Mark review as done |

### Admin (`/api/v1/admin/`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/admin/pending-signups` | List pending registrations |
| GET | `/api/v1/admin/pending-signups/count` | Count pending registrations |
| POST | `/api/v1/admin/approve/{id}` | Approve registration |
| DELETE | `/api/v1/admin/reject/{id}` | Reject registration |
| GET | `/api/v1/admin/users` | List user accounts (paginated, status filter) |
| PATCH | `/api/v1/admin/users/{id}/status` | Promote/demote, lock/unlock (disabled) |
| DELETE | `/api/v1/admin/users/{id}` | Delete user incl. galleries + storage files |
| POST | `/api/v1/admin/users/{id}/reset-password` | Set a new password for a user |

## Security

- **Share links:** long random capability URLs are hashed for lookup and stored replayable for the owner UI; optional gallery passwords protect sensitive galleries
- **Signed URLs:** HMAC-SHA256 with configurable TTL (thumbnails 1h, previews 15min)
- **Rate limiting:** signup 5/min, login 10/min, token resolution 20/10min
- **Security headers:** CSP, HSTS, X-Frame-Options, Referrer-Policy
- **Tenant isolation:** all queries filtered by owner_id, verified by structural tests
- **Non-root container:** app runs as unprivileged user

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12, FastAPI, Pydantic v2 |
| Database | PostgreSQL 16 |
| ORM | SQLAlchemy 2.x + Alembic (async) |
| Frontend | HTMX, Alpine.js, Tailwind CSS |
| Image processing | Pillow (WebP thumbnails at 320/640/1280px, watermarks) |
| Storage | Local filesystem or S3-compatible (via aioboto3) |
| Container | Multi-stage Docker, docker-compose |
| CI/CD | GitHub Actions (lint, test, multi-arch Docker Hub publish) |

## Configuration

All configuration is done via environment variables. See [`.env.example`](.env.example) for the full list with descriptions.

Key variables:

| Variable | Description |
|----------|-------------|
| `SECRET_KEY` | JWT signing key (must change from default) |
| `HMAC_SECRET_KEY` | Image URL signing key (must change from default) |
| `APP_URL` | Public base URL for share links — set to your real HTTPS domain in production (forced to https behind a TLS proxy) |
| `DATABASE_URL` | PostgreSQL connection string |
| `STORAGE_BACKEND` | `local` or `s3` |

## Development

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install with dev dependencies
pip install -e ".[dev]"

# Run linter
ruff check .

# Run type checker
mypy app/

# Run tests (requires PostgreSQL)
pytest --cov=app

# Run tenant isolation tests
pytest tests/security/ -v
```

## Docker Hub

Multi-arch images (amd64 + arm64) are built and pushed automatically via GitHub Actions ([`docker-publish.yml`](.github/workflows/docker-publish.yml)).

| Tag | When it's built | Use for |
|-----|-----------------|---------|
| `:latest` | On every version tag `v*` | **Production** — stable releases only |
| `:1.2.3`, `:1.2`, `:1` | On every version tag `v*` (semver) | Pinning a specific version |
| `:dev` | On **every push to `main`** | **Staging / test server** — latest development build |
| `:sha-<short>` | On every push to `main` | Pinning a specific commit |

```bash
# Production / stable
docker pull stoertebeker2k/picture-stage:latest

# Test server / bleeding edge
docker pull stoertebeker2k/picture-stage:dev
```

### How the pipeline is gated

The Docker build runs only when both conditions hold, so no image is ever published from broken or irrelevant code:

1. **CI must be green.** [`ci.yml`](.github/workflows/ci.yml) is a reusable workflow (`workflow_call`) invoked as a prerequisite job — `build-and-push` has `needs: ci`. Lint (ruff), type check (mypy) and tests (pytest) all run before any image is built. This applies to `:dev` builds too.
2. **Relevant files changed** (push to `main` only). A `dorny/paths-filter` gate skips the build for doc-only commits. It triggers on changes to `app/`, `alembic/`, `Dockerfile`, `pyproject.toml`, or the workflow files. **Version tags `v*` always build**, regardless of the path filter.

## License

[GNU Affero General Public License v3.0](LICENSE) — Copyright 2026 Norbert Schramm

If you modify Picture-Stage and offer it as a service, you must publish your changes under the same license.
