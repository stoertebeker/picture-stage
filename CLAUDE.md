# Project Instructions for AI Agents

This file provides instructions and context for AI coding agents working on this project.

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:ca08a54f -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd dolt push
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
<!-- END BEADS INTEGRATION -->

## Build & Test

```bash
# Install dependencies (Python 3.12+)
pip install -e ".[dev]"

# Lint
ruff check .

# Type check
mypy app/

# Tests (requires PostgreSQL via DATABASE_URL)
pytest --cov=app

# Tenant isolation tests
pytest tests/security/ -v

# Docker (full stack: App + Postgres + Alembic migrate)
docker compose up -d

# API docs after startup
# http://localhost:8000/docs
```

## Architecture Overview

Picture-Stage is a self-hosted photo proofing app. Photographers share galleries with models via magic links. Models select/favorite/comment images. Photographers export selections as CSV/JSON for Lightroom/Capture One.

### Stack
- **Backend:** Python 3.12 + FastAPI + Pydantic v2 + SQLAlchemy 2.x (async) + Alembic
- **Database:** PostgreSQL 16 (separate container)
- **Storage:** Pluggable — LocalStorage (Docker volume) or S3Storage (aioboto3, MinIO/AWS/Hetzner/R2/B2)
- **Frontend:** HTMX + Alpine.js + Tailwind CSS (not yet implemented)
- **Imaging:** Pillow — WebP thumbnails (320/640/1280px), RGBA watermark overlay

### API Routers (5 isolated routers)
| Router | Prefix | Auth | Purpose |
|--------|--------|------|---------|
| auth | `/api/v1/auth/` | None/JWT | Signup, login, email verify |
| admin | `/api/v1/admin/` | JWT (admin) | Approve/reject signups |
| galleries | `/api/v1/galleries/` | JWT (active) | CRUD, share links, export |
| images | `/api/v1/galleries/{id}/images/` | JWT (active) | Upload, list, delete |
| guest | `/g/` | Share token | Model-facing: view, select, complete |

### Database (10 tables)
users, galleries, images, image_previews, selection_events (append-only), share_sessions, audit_log, notification_configs, notification_deliveries, pending_signups

### Key Design Decisions
- **Event-sourced selections:** `selection_events` is append-only (INSERT only, no UPDATE/DELETE). Current state materialized by replaying events. Enables audit trail, undo, change detection.
- **Isolated Guest API:** `/g/` router has zero overlap with admin API. No shared endpoints, no auth dependencies.
- **Token hashing:** SHA-256 + random salt for share tokens (fast lookups). bcrypt only for user passwords.
- **Signed URLs:** HMAC-SHA256 for image delivery. Thumbnails 1h TTL, previews 15min TTL.
- **UUIDs as external IDs** on all endpoints to prevent IDOR enumeration.

## Conventions & Patterns

- Atomic commits: one feature/fix per commit, referenced by beads issue ID
- Tenant isolation: every DB query on user-owned resources filters by `owner_id == current_user.id`
- Path traversal protection: `LocalStorage._full_path()` rejects absolute paths and `..` components
- Preview format: WebP at 3 sizes (thumb_sm=320px, thumb_md=640px, preview=1280px)
- Watermark format: `PREVIEW · {gallery_id[:8].upper()}` in bottom-right corner
- Rate limits: signup 5/min, login 10/min, token resolution 20/10min, password verify 5/min
- Security headers: CSP, HSTS, X-Frame-Options, Referrer-Policy — applied via middleware on every response

## Aktueller Stand

**Datum:** 2026-05-27

### Was ist fertig
- v0.1 API komplett: 12/12 Issues, 27 Endpoints
- v0.2 Lifecycle & Komfort: 6/7 Issues (Tastatur-Shortcuts deferred)
- v0.4 Frontend komplett: 6/6 Issues, HTMX + Alpine.js + Tailwind CSS
  - 5 Frontend-Router (auth, dashboard, galleries, guest, admin) in `app/frontend/`
  - 20 Jinja2-Templates (inkl. HTMX-Partials) in `app/templates/`
  - Cookie-Auth (HttpOnly JWT), CSRF Double-Submit, Dark-Mode
  - Guest-Viewer mit Lightbox, Keyboard-Nav, Selections, Sort/Filter
  - Gallery-Management mit Drag-Drop Upload, Share-Links, Status-Transitions
- 10 DB-Tabellen, Pluggable Storage (Local + S3), Security-Middleware, Rate-Limiting
- CI/CD Workflows (GitHub Actions), Multi-Arch Dockerfile
- 154 Tests, alle gruen

### Naechster Epic: v0.3 Produktion & Compliance (picture-stage-fbr)
- **Epic:** `picture-stage-fbr` – 0/7 Issues closed
- **Ready-Queue:** Alle 7 Issues unblocked, keine Dependencies untereinander
- **Einstieg:** `bd ready` zeigt die Queue, `/make-it-so picture-stage-fbr` startet Ausfuehrung

| Issue | Beads-ID | Prio | Beschreibung |
|-------|----------|------|-------------|
| Ablaufdatum | `picture-stage-fbr.1` | P2 | Optionales Ablaufdatum pro Galerie |
| Audit-Log | `picture-stage-fbr.2` | P1 | Audit-Log pro Galerie |
| DSGVO | `picture-stage-fbr.3` | P1 | DSGVO-Seiten & Compliance |
| Galerie-Loesch | `picture-stage-fbr.4` | P1 | Galerie-Loesch-Workflow |
| Backup/Restore | `picture-stage-fbr.5` | P1 | Backup/Restore-CLI im Container |
| i18n | `picture-stage-fbr.6` | P2 | Deutsch + Englisch |
| Wasserzeichen | `picture-stage-fbr.7` | P3 | Erweiterte Wasserzeichen-Konfig |

### Was fehlt fuer Produktivnutzung
- **v0.3 Produktion & Compliance** (7 Issues offen)
- **Alembic initiale Migration** (braucht laufende Postgres)
- **Docker-Build testen** (lokal oder via CI)
- **Vendored JS** (HTMX + Alpine.js .min.js ersetzen – aktuell Platzhalter)
- **Tailwind CSS Build** (styles.css via Tailwind CLI generieren)

### Epics
| Epic | Beads-ID | Status |
|------|----------|--------|
| v0.1 Minimal Viable Picdrop | `picture-stage-ebm` | 12/12 closed |
| v0.2 Lifecycle & Komfort | `picture-stage-9q3` | 6/7 closed (1 deferred) |
| v0.3 Produktion & Compliance | `picture-stage-fbr` | 0/7 open |
| v0.4 Frontend | `picture-stage-gza` | 6/6 closed |
