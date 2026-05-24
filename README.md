# Picture-Stage

**Self-hosted photo proofing for photographers and models.**

Picture-Stage lets photographers share image galleries with models for review. Models can select images for editing, mark favorites, and leave comments — all without creating an account. Self-hosted, open-source, and fully under your control.

> **Status:** Work in progress — v0.1 in development.

## Features (v0.1 planned)

- **Share galleries via magic link** — no model login required
- **Select, favorite, and comment** on individual images
- **Auto-save** — selections persist immediately
- **Export selections** as CSV/JSON for Lightroom/Capture One
- **Server-side watermarking** — originals are never exposed
- **Pluggable storage** — local Docker volume or S3-compatible (MinIO, AWS, Hetzner, R2, B2)
- **Multi-arch Docker image** — runs on amd64 and arm64 (Raspberry Pi, Synology)

## Quick Start

```bash
# 1. Clone the repository
git clone https://github.com/stoertebeker/picture-stage.git
cd picture-stage

# 2. Configure
cp .env.example .env
# Edit .env — at minimum set SECRET_KEY, HMAC_SECRET_KEY, ADMIN_EMAIL, ADMIN_PASSWORD

# 3. Run
docker compose up -d

# 4. Open
# http://localhost:8000
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12, FastAPI, Pydantic v2 |
| Database | PostgreSQL 16 |
| ORM | SQLAlchemy 2.x + Alembic |
| Frontend | HTMX, Alpine.js, Tailwind CSS |
| Image processing | Pillow (thumbnails, watermarks) |
| Storage | Local filesystem or S3-compatible (via aioboto3) |
| Container | Multi-stage Docker, docker-compose |
| CI/CD | GitHub Actions |

## Configuration

All configuration is done via environment variables. See [`.env.example`](.env.example) for the full list with descriptions.

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
```

## Docker Hub

```bash
docker pull stoertebeker/picture-stage:latest
```

Multi-arch images are built automatically on each tagged release via GitHub Actions.

## License

[GNU Affero General Public License v3.0](LICENSE)

This means: if you modify Picture-Stage and offer it as a service, you must publish your changes under the same license.
