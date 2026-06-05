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
- **Frontend:** HTMX + Alpine.js + Tailwind CSS (Editorial Dark, Token-System, self-hosted Fonts)
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
- **Share links:** SHA-256 + random salt for token lookups; token is also stored replayable for the owner UI. bcrypt is used for user passwords and optional gallery passwords.
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

**Datum:** 2026-06-05
**Wachwechsel-Tag:** `handover-2026-06-05` (zeigt auf `f1b72c3`, verifizierter grüner Migrations-Stand)

### Was ist fertig
- v0.1–v0.4 vollständig (API, Lifecycle, Compliance, Frontend funktional)
- 170 Unit-Tests grün; CI gegen Postgres-Service; ruff + mypy strict grün
- DB-Migrationen produktionsreif; Migration↔ORM-Drift-Guard in CI
- i18n DE+EN vollständig — alle hardcoded Strings auf Keys (`auth.*`, `gallery.*`)
- **v0.5 Foundation komplett:** Design-Tokens (`docs/design/tokens.md`), Tailwind-Config,
  Web-Fonts self-hosted (Inter + Fraunces WOFF2), Dark-Mode-Bootstrap, Layout-Primitives,
  Komponenten-Inventar
- **v0.5 Komponenten komplett:** Button (`_macros/buttons.html`), Form (`_macros/forms.html`),
  Modal/Dialog (`_macros/modal.html`)
- **v0.5 Guest-Viewer komplett:** Spike + Template auf Editorial Dark

### v0.5 – Noch offen (Epic `picture-stage-qdz`)

**Scope:** Nur Guest-Pages. Photographer-Pages (Dashboard, Auth, Admin, Audit-Log etc.) → v0.6+.

| Ausstehend | Beads-ID |
|------------|----------|
| Guest-Lightbox Mockup + Impl | `qdz.13`, `qdz.14` |
| Guest-Password-Gate Mockup + Impl | `qdz.15`, `qdz.16` |
| Mobile-Tuning Guest-Pages (375/768/1280px) | `qdz.18` |

Einstieg: `bd ready` → `qdz.13` (PS-UX-21a, Guest-Lightbox Mockup)

### Kleinere offene Punkte
- Docker-Build verifizieren (oder via `docker-publish.yml`)
- GitHub Actions: Node-20-Deprecation — Frist Sept. 2026
- WATERMARK_OPACITY Breaking-Change-Hinweis in Release-Notes

### Epics
| Epic | Beads-ID | Status |
|------|----------|--------|
| v0.1 Minimal Viable Picdrop | `picture-stage-ebm` | closed |
| v0.2 Lifecycle & Komfort | `picture-stage-9q3` | closed (1 deferred) |
| v0.3 Produktion & Compliance | `picture-stage-fbr` | closed |
| v0.4 Frontend (funktional) | `picture-stage-gza` | closed |
| v0.5 UX-Redesign – Editorial Dark (Guest-Focused) | `picture-stage-qdz` | Foundation + Komponenten + Viewer ✅, Lightbox/PW-Gate/Mobile offen |

### Verifikation für neue Sessions
`bash scripts/verify-handover.sh` prüft den Übergabe-Stand
(clean tree, Tag vorhanden, Tools verfügbar, Tests grün).
