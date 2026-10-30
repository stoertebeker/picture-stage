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
   git push
   git status  # MUST show "up to date with origin"
   ```
   Note: there is NO external Dolt remote. Beads **issues** sync via
   `.beads/issues.jsonl` (run `bd export > .beads/issues.jsonl`, which ships
   with the git push). **Memories** (`bd remember`) deliberately live ONLY in
   the local Dolt DB on this machine and are NOT synced to git (captain's
   decision 2026-06-15) — `bd export` excludes them by default anyway, and
   `bd prime` loads them into every session regardless. Do NOT run
   `bd dolt push`.
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
ruff format --check .

# Type check
mypy app/

# DB-free tests
pytest tests/unit/ tests/security/

# Full suite (requires a separate PostgreSQL test DB via DATABASE_URL;
# fixtures drop and recreate tables — never use the app database)
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

### Route groups
| Router | Prefix | Auth | Purpose |
|--------|--------|------|---------|
| auth | `/api/v1/auth/` | None/JWT | Signup, login, email verify |
| admin | `/api/v1/admin/` | JWT (admin) | Approve/reject signups |
| galleries | `/api/v1/galleries/` | JWT (active) | CRUD, share links, export |
| images | `/api/v1/galleries/{id}/images/` | JWT (active) | Upload, list, delete |
| guest | `/g/` | Share token | Model-facing: view, select, complete |

### Database (11 tables)
users, galleries, images, image_previews, upload_cleanup_intents, selection_events (append-only), share_sessions, audit_log, notification_configs, notification_deliveries, pending_signups

### Key Design Decisions
- **Event-sourced selections:** `selection_events` is append-only (INSERT only, no UPDATE/DELETE). Current state materialized by replaying events. Enables audit trail, undo, change detection.
- **Guest routes:** HTML and JSON handlers share `/g/` path patterns. HTML is registered first; middleware must follow Starlette's first full match. Both use the shared token resolver, owner-status/expiry gates and password-session checks.
- **Share links:** SHA-256 + random salt for token lookups; token is also stored replayable for the owner UI. bcrypt is used for user passwords and optional gallery passwords.
- **Signed URLs:** HMAC-SHA256 for image delivery. Thumbnails 1h TTL, previews 15min TTL.
- **UUIDs as external IDs**; owner and gallery-session checks enforce authorization.

## Conventions & Patterns

- Atomic commits: one feature/fix per commit, referenced by beads issue ID
- Tenant isolation: every DB query on user-owned resources filters by `owner_id == current_user.id`
- Path traversal protection: `LocalStorage._full_path()` rejects absolute paths and `..` components
- Preview format: WebP at 3 sizes (thumb_sm=320px, thumb_md=640px, preview=1280px)
- Watermark format: `PREVIEW · {gallery_id[:8].upper()}` in bottom-right corner
- Rate limits: defaults 200/min per matched route template and client address; explicit decorators retain their own limits/scopes (signup 5/min, login 10/min, password verify 5/min). Default memory counters are process-local; arbitrary forwarding headers are not trusted.
- Security headers: CSP, HSTS, X-Frame-Options, Referrer-Policy. Outer admission rejections use small JSON 413/429 responses.

## Aktueller Stand

**Stand:** 2026-10-30. Backend und Editorial-Dark-Frontend mit DE/EN, Light/Dark, Gast-/Fotografen-Lightbox und Auswahl-Export sind umgesetzt. Paketversion weiterhin `0.1.0`. Alle öffentlichen GitHub-Issues #1–#6 sind abgearbeitet. Bestehenden Sprach-Switch beibehalten; Tagesende für Ablaufdaten ist UTC, Formularmaximum 9999-12-30, nicht endlich speicherbare API-Zeitstempel ergeben 422.

**Diese Übergabe:** `a1d2afa` (`picture-stage-bbq1`) begrenzt Nicht-Uploads vor CSRF/Parsing auf standardmäßig 1 MiB; die beiden Image-Upload-POST-Routen behalten das globale Budget. `791d1e2` (`picture-stage-z0ha`) aktiviert die Default-Limits vor Dispatch mit erstem Routenmatch, gemeinsamem Default-Zähler für Tokenvarianten und unveränderten Speziallimits. Konfiguration/Abschaltverhalten stehen in README und `.env.example`. Der eigene Request für die Default-Schlüsselbildung darf niemals den tatsächlich verarbeiteten Scope verändern. 66 fokussierte Regressionen unter Host- und Python-3.12-Runtime und Playwright mit vier echten Uploads bestanden. `1588ad8` ergänzt native effektive Include-Kontexte für die lazy Router aktueller FastAPI-Versionen. GitHub-Lauf `41123420000` für `1588ad8`: 757 Tests (4 Warnungen), Lint/Format/mypy erfolgreich. Playwright nach finalem App-Neustart und zusätzliche echte Media-Dekodierung bestanden. Multi-Arch-Image für amd64/arm64 unter `sha-1588ad8` veröffentlicht und direkt in der Registry verifiziert (Manifest `sha256:920322b0fd361dcddd5b16bdb1a30e65ce60d233fb4437e00cebe96c3cbc6149`).

**Vorherige Fixes:** `807767c` schließt veraltete CSRF-/BFCache-Seiten durch sicheren GET ohne POST-Replay; Session-HMAC nicht abschwächen. `4b37afe` führt atomare Upload-Batches mit Cleanup-Intents und Migration `0008` ein. `cb64261` hält Preview-Storagewrites bei Abbruch bis zum Abschluss unter der Lifecycle-Fence.

**Offene Arbeit/Freigabe:** Der Nutzer hat ausschließlich `bbq1` und `z0ha` sowie diesen Wachwechsel (`picture-stage-24lc`) zusätzlich freigegeben. Beide Security-Tickets sind geschlossen; keine weitere Implementierung ohne neue Freigabe beginnen. Die statische Neubewertung `picture-stage-rg1o` am Commit `414d997` ergab 24 bestätigte und 8 klärungsbedürftige Security-Befunde; mit den zwei neuen Fixes verbleiben 22 bestätigte und 8 klärungsbedürftige Befunde sowie zwei Maintenance-Tickets. Verbindlichen aktuellen Stand immer mit `bd ready`, `bd list --status=open --limit 0` und `bd list --status=in_progress` prüfen. Nächste vorgeschlagene Themen sind Galeriequota inkl. Duplizieren (`a10v`) und isolierte API-Bildverarbeitung (`9mtm`), bisher ohne Freigabe. Kontextfragen zu Backup-Vertrauen, Wasserzeichenumfang und Logout-Vertrag sind weiter offen.

**Storage-Invarianten:** `app/galleries/lifecycle.py`: geteilte Galerie-Fence für Upload/Worker/Single-Delete, exklusive Galerie-Fence für Gallery/Bulk-Delete, exklusive Image-Fence für Worker/Single-Delete; vor Audit-/FK-Writes erwerben, IDs sortieren. Cleanup vereinigt gespeicherte und kanonische Keys. Fehler erhalten DB-Referenzen und liefern retrybares 503. `UploadCleanupIntent` wird vor Writes persistiert und beim erfolgreichen Bild-Commit atomar entfernt; Kompensation schützt bereits committete Bilder. `complete_upload` wartet laufende Writes vor Cancel-Cleanup ab. Galerie-/Userdelete wiederholt offene Intent-Keys. Kein Janitor/historischer Orphan-Sweep und kein Live-S3-/SIGKILL-Nachweis. Verlorene Commitbestätigung kann referenzierte Pending-Bilder ohne erneuten Preview-Dispatch hinterlassen.

**Lokale Verifikation:** DB-Tests ausschließlich gegen eine separate Testdatenbank: Fixtures löschen/erzeugen Tabellen. `tests/conftest.py` erzwingt für Tests einen lokalen Memory-Rate-Limit-Backend und setzt Zähler pro Test zurück, ohne Enforcement pauschal abzuschalten. Host-Python ist 3.14, CI/Runtime 3.12; hängende TestClient-/Threadtests in der Sandbox mit Timeout und erforderlichem Zugriff ausführen. Frisches `ASSET_VERSION` für Browsertests verwenden; eingechecktes CSS ist ein Stub. Chrome: `/opt/google/chrome/chrome`, Playwright-Modul zuletzt `/tmp/picture-stage-playwright/node_modules/playwright/index.mjs`. Screenshots nur `.playwright/`, selbst sichten und sofort löschen. Testzugänge aus lokalen Beads-Memories beziehen; keine lokalen Credentials in Doku übernehmen. Beide Admission-Testdatenbanken und der Compose-Teststack sind entfernt, normale App-Daten/Volumes erhalten; keine Screenshots übrig.

**Einstieg nach Wachwechsel (`handover-2026-10-30-admission`):** `bd prime` und `bash scripts/verify-handover.sh`. Das Skript prüft Git-/Tool-/Dateizustand; den tatsächlichen Test-/CI-Nachweis hier bzw. in den Ticketnotizen prüfen. Alte Zeitstände und Aufforderungen sind im [Juni-Archiv](docs/archive/handovers-2026-06.md) ausschließlich historisch. Memories bleiben lokal in Beads, nicht in Git.
