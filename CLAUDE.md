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

### Database (12 tables)
setup_state, users, galleries, images, image_previews, upload_cleanup_intents, selection_events (append-only), share_sessions, audit_log, notification_configs, notification_deliveries, pending_signups

### Key Design Decisions
- **Event-sourced selections:** `selection_events` is append-only (INSERT only, no UPDATE/DELETE). Current state materialized by replaying events. Enables audit trail, undo, change detection.
- **Guest routes:** HTML and JSON handlers share `/g/` path patterns. HTML is registered first; middleware must follow Starlette's first full match. Both use the shared token resolver, owner-status/expiry gates and password-session checks.
- **Share links:** Indexed SHA-256 locator when possible, followed by the salted verifier. Legacy hash-only links still use a bounded-concurrency linear scan. Replayable tokens are stored for the owner UI. bcrypt is used for user passwords and optional gallery passwords.
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

**Stand:** 2026-11-05. Backend und Editorial-Dark-Frontend mit DE/EN, Light/Dark, Gast-/Fotografen-Lightbox und Auswahl-Export sind umgesetzt. Paketversion `0.1.0`; öffentliche GitHub-Issues #1–#6 sind geschlossen. Sprach-Switch beibehalten; Ablaufdatum endet am gewählten UTC-Tag, Formularmaximum 9999-12-30.

**Letzte Sicherheitswellen:** `a10v` setzt die Galeriequote atomar für Erstellen und Duplizieren durch; `9mtm` und `yxk9` isolieren Bildverarbeitung und begrenzen Zulassung pro Prozess. `e1nt`, `jijh` und `xlek` behandeln Mail-Empfänger, begrenztes Passwort-Hashing und Auswahlbudgets. Webhooks (`fx8n`, `4llu`) prüfen und pinnen öffentliche Ziele ohne Umgebungs-Proxys und lesen keine Antwortkörper; CSV-Ausgaben (`wc5w`) schützen formelartige Werte. Migration `0009` indexiert neue Verify-Tokens und rückfüllbare Galerietokens; `uaxo` und `fzxd` bleiben offen, weil ungültige und historische Tokens weiter lineare Scans auslösen können. Der Nutzer verlangt ausdrücklich den Erhalt alter Links. Migration `0010` schützt die Erstinstallation mit `BOOTSTRAP_TOKEN`, Singleton und DB-Guard; `python -m app.cli.recover_admin` stellt bei leerer User-Tabelle lokal einen Admin wieder her. Pillow >=12.3 und Decoder-Allowlist JPEG/PNG/WebP (`9pzk`) weisen ungültige Bilder mit 422 zurück.

**Restore und Logging:** `myru` macht den Restore vollständiger eigener Archive in eine leere DB verbindlich: Preflight lehnt vorhandene User-Relationen in allen Schemas ab; Prüf-/SQL-Fehler liefern Exit 1. `psql` ignoriert Startup-Dateien und nutzt `ON_ERROR_STOP` plus eine einzelne Transaktion. Lokale Bilder werden vor SQL im Upload-Dateisystem gestaged und nach SQL-Commit pro Datei atomar ersetzt. Spätere Dateifehler melden ausdrücklich eine bereits wiederhergestellte DB mit unvollständigen Bildern; keine atomare DB-/Dateisystem-Transaktion. App beim Restore gestoppt halten. Image und CI nutzen PostgreSQL-16-Clients, passend zum Server; pg_dump 17 erzeugte im Test das auf Server 16 unbekannte `transaction_timeout`. `k00e` reaktiviert vor Alembic importierte `app.*`-Logger bei erneuter Logging-Konfiguration, ohne doppelte Uvicorn-Ausgaben.

**Galerieabschluss:** `nnvq`/`x8fp` verwenden `app.guest.service.lock_gallery_for_review`: gezieltes `FOR UPDATE` auf genau eine Galerie, danach separate frische Prüfung von Token, Owner-Status, Galerie-Status und Ablauf. Auswahl sperrt nach Lifecycle-Gallery/Image und Eventbudget; Gastabschluss nach geteilter Lifecycle-Gallery-Fence. Owner-Status-UPDATEs beider Oberflächen konkurrieren automatisch mit der Zeilensperre. Auswahl nach abgeschlossenem Review: 403; erneuter Abschluss mit gleicher, anderer oder frischer Sitzung: 409 vor Seiteneffekten. Nur der erfolgreiche `shared -> completed`-Übergang setzt die Session und stößt nach Commit E-Mail/Webhook an. Bewusstes Wiederöffnen und bestehende Auswahlhistorie bleiben erhalten; bereits abgeschlossene Sitzungen können weiterhin nicht erneut abschließen. Benachrichtigungen bleiben best effort ohne dauerhafte Queue/Crash-Recovery. Keine Schemaänderung.

**Offene Arbeit und Entscheidungen:** Stand dieser Übergabe: 16 offene Tickets, keines in Bearbeitung; maßgeblich sind `bd ready` und `bd list --status=in_progress`. `nnvq`/`x8fp` für konsistenten Galerieabschluss sind abgeschlossen. Nächste Empfehlung ist `vhrn` für Gastsitzungen nach Passwortwechsel. `rkra` betrifft kumulative Speicherquoten; `teis` benötigt eine fachliche Klärung zum Wasserzeichenumfang. Der Nutzer hat das SMTP-Anmelde-Timing-Risiko `lt98` ausdrücklich akzeptiert; Ticket geschlossen, kein Fix übernommen. Keine neue Ticketumsetzung aus dieser Übergabe ableiten.

**Verifikation Galerieabschluss 2026-11-05:** Vor dem Fix reproduzierten fünf Fälle die Fehler (zwei Viewer-Revisits, drei Abschlusswege). Danach 18 neue Regressionen und 67 fokussierte Tests erfolgreich, vollständige Suite: 924 bestanden unter Python 3.12/PostgreSQL 16. Ruff, Formatprüfung, mypy und nativer Image-Build erfolgreich; frische unabhängige Voruntersuchung und ein frischer read-only Kandidatenreview ohne Befund. Originale Scan-PoCs waren nicht mehr vorhanden. Dauerhafte neue Laufzeitbelege liegen in der targetPath-Artifact-Collection unter `artifacts/fix-nnvq-x8fp/`. SMTP/Webhook werden an der Transportgrenze gemockt; keine Live-Zustellung oder Crash-Recovery behauptet.

**Verifikation 2026-11-05:** Python 3.12/PostgreSQL 16 im isolierten Teststack: 904 Tests bestanden; zwei JS-Tests scheiterten zunächst ausschließlich am fehlenden Node im Testimage und bestanden nach dessen Ergänzung separat. Damit alle 906 Tests geprüft, einschließlich 10 neuer Restore-Fälle und einer neuen Logger-Regression. Ruff, Format und mypy erfolgreich. Natives App-Image gebaut; vollständiger Backup-/Restore-Roundtrip einschließlich Admin-Guard und Bildern mit den eingebauten PostgreSQL-16-Clients als App-UID 999 bestanden. Unabhängiger read-only Review ohne Befund. Diese Nachweise sind lokal; ältere CI-Nachweise folgen separat.

**Verifikation:** GitHub Actions `41236978915` für `32d2d08` meldete 895 bestandene Tests, Lint, Format, mypy und Multiarch-Build erfolgreich. Der spätere Beads-Commit `d49daa8` änderte nur die Risikoakzeptanz; sein Workflow `41245760936` war erfolgreich und übersprang Tests/Build. CI- und Ticketnachweise gelten für die jeweiligen Commits. LibreOffice Calc 25.2.3.2 wurde für den CSV-Import samt Save/Reopen mit Positivkontrolle geprüft; Excel und andere Importmodi nicht. Keine Live-S3- oder Lastprüfung.

**Weitere Invarianten:** Nicht-Uploads sind standardmäßig vor CSRF/Parsing auf 1 MiB begrenzt; Default-Rate-Limits greifen pro Routen-Template und Clientadresse. Der Request zur Schlüsselbildung darf den tatsächlich verarbeiteten Scope nicht ändern. CSRF-/BFCache-Recovery verwendet einen sicheren GET ohne POST-Replay. Auswahlereignisse bleiben append-only; standardmäßig maximal 20.000 Ereignisse pro Galerie und 2.000 Unicode-Zeichen pro Kommentar, konfigurierbar. Bestehende Historie bleibt lesbar.

**Storage-Invarianten:** `app/galleries/lifecycle.py`: geteilte Galerie-Fence für Upload/Worker/Single-Delete, exklusive Galerie-Fence für Gallery/Bulk-Delete, exklusive Image-Fence für Worker/Single-Delete; vor Audit-/FK-Writes erwerben, IDs sortieren. Cleanup vereinigt gespeicherte und kanonische Keys. Fehler erhalten DB-Referenzen und liefern retrybares 503. `UploadCleanupIntent` wird vor Writes persistiert und beim erfolgreichen Bild-Commit atomar entfernt; Kompensation schützt bereits committete Bilder. `complete_upload` wartet laufende Writes vor Cancel-Cleanup ab. Galerie-/Userdelete wiederholt offene Intent-Keys. Kein Janitor/historischer Orphan-Sweep und kein Live-S3-/SIGKILL-Nachweis. Verlorene Commitbestätigung kann referenzierte Pending-Bilder ohne erneuten Preview-Dispatch hinterlassen.

**Lokale Verifikation:** DB-Tests ausschließlich gegen eine separate Testdatenbank: Fixtures löschen/erzeugen Tabellen. `tests/conftest.py` erzwingt für Tests einen lokalen Memory-Rate-Limit-Backend und setzt Zähler pro Test zurück, ohne Enforcement pauschal abzuschalten. Host-Python ist 3.14, CI/Runtime 3.12; hängende TestClient-/Threadtests in der Sandbox mit Timeout und erforderlichem Zugriff ausführen. Frisches `ASSET_VERSION` für Browsertests verwenden; eingechecktes CSS ist ein Stub. Chrome: `/opt/google/chrome/chrome`, Playwright-Modul zuletzt `/tmp/picture-stage-playwright/node_modules/playwright/index.mjs`. Screenshots nur `.playwright/`, selbst sichten und sofort löschen. Testzugänge aus lokalen Beads-Memories beziehen; keine lokalen Credentials in Doku übernehmen. Beide Admission-Testdatenbanken und der Compose-Teststack sind entfernt, normale App-Daten/Volumes erhalten; keine Screenshots übrig.

**Einstieg nach Wachwechsel:** `bd prime`, `bd ready` und `bash scripts/verify-handover.sh`. Das Skript prüft Git-/Tool-/Dateizustand; Test- und CI-Nachweise stehen hier bzw. in den Ticketnotizen. Alte Zeitstände im [Juni-Archiv](docs/archive/handovers-2026-06.md) sind historisch. Memories bleiben lokal in Beads, nicht in Git.
