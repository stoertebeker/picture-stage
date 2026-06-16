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

**Datum:** 2026-06-16
**Wachwechsel-Tag:** `handover-2026-06-16` (neuester Wachwechsel dieser Wache)
**Live:** `https://picture.stoertes.cloud` (Docker Hub, SMTP Mailjet produktiv). **Prod NICHT via Playwright/Headless-Browser testbar** — Cloudflares JS-Challenge/Turnstile blockt automatisierte Browser (siehe Memory `playwright-setup`). Visuelle Abnahme daher gegen den **lokalen Docker-Stack** (frischer Build inkl. Migrationen) oder durch den Kapitän selbst. **Admin-Login `stoertebeker@kkb-clan.de` bleibt für die Entwicklungsphase aktiv** (Kapitän-Entscheidung). Passwort NICHT im Repo — lokal in bd-Memory bzw. Passwort-Manager.
**Dev-Umgebung:** Ubuntu 26.04 x86_64, Sandbox deaktiviert, Docker direkt nutzbar. Chrome: `/opt/google/chrome/chrome`. Lokaler Stack: `docker compose up -d`. **Lokaler Admin: `testadmin@example.com` / `testpass123`** (NICHT `admin@local.test` — s. Memory `local-dev-admin-login`). **Frontend-Änderungen lokal abnehmen:** Stack mit frischer `ASSET_VERSION` neu bauen (`docker compose build --build-arg ASSET_VERSION=$(date +%Y%m%d%H%M%S) app && docker compose up -d app`), sonst stale Cache. **Integration-Tests lokal:** Test-DB `picstage_test` im DB-Container (separate von Dev-DB!), `DATABASE_URL=postgresql+asyncpg://picstage:picstage@<db-container-ip>:5432/picstage_test UPLOAD_DIR=/tmp/ps_uploads .venv/bin/pytest tests/integration/` (Container-IP via `docker inspect`, Port nicht host-gemappt).

### Erledigt in dieser Wache (2026-06-16) — Cleanup + 2 User-Findings + 2 externe Security-Reviews + 3 P2-Härtungen

**Cleanup & User-Findings:**
- **Cleanup-Audit:** loses Treibgut entsorgt (18 Root-Screenshots, 93 Playwright-MCP-Artefakte, `/tmp`-Reste) + **1 Sicherheitsfund** (JWT-Cookie-Jars in `/tmp`). 2 User-Findings aus `user-findings.txt` vor dem Löschen in Tickets überführt.
- **`vre` (bug P2, `fe74e38`, closed):** `de.json` orthografisch korrigiert (ASCII-Ersatz `ae/oe/ue` → echte Umlaute, 14 Werte) + komplett englisch gebliebenen `auth.*`-Block übersetzt + `nav.logout`→„Abmelden". Etablierte Begriffe (Dashboard/Dark/Light/Picture-Stage) bewusst belassen.
- **`pc6` (bug P2, `1f35793`, closed):** Galerie-Abschluss-Mail verlinkte auf den API-Endpunkt (`/api/v1/galleries/dashboard`, JWT-Bearer nötig → „Not authenticated" im Browser). Jetzt Frontend-Galerie `{app_url}/galleries/{id}`; Payload-Key `dashboard_url`→`gallery_url`, Wording „Zur Galerie".

**2 externe LLM-Security-Reports geprüft (Finding-für-Finding gegen echten Code) → 10 Tickets:**
- **miniax-report:** kein echtes „Kritisch"; K3.3 (Worker existiert) + H2-Beispiel widerlegt. → `0y7` (Login-Timing), `fbq` (Upload-Limits), `6bs` (Login-CSRF), `ccx` (Image-Bomb).
- **qwen-report:** 1 widerlegt (1.2 Admin-PW-Längencheck existiert in API+Web), 3 Dubletten. → `8ox` (Cookie-Security 1.5+1.1), `d7z` (Guest-Entdopplung 2.1), `bkw` (DRY 2.2+3.2), `e5n` (func.count 2.3), `ath` (Expiry 3.3), `1gd` (Betrieb 4.4+4.5). Beide Report-Dateien nach Auswertung gelöscht.

**3 P2-Security-Härtungen umgesetzt:**
- **`0y7` (`60ed6b1`, closed):** Login-Timing-Angleichung gegen Account-Enumeration — immer ein bcrypt-Verify (Dummy-Hash bei fehlendem User), konsistent zu Signup-Schutz (`42q`).
- **`6bs` (`a0cedd8`, closed):** Toten CSRF-exempten `POST /api/v1/auth/login-form` entfernt (Login-CSRF-Lücke + Duplikat von `login_submit`). Timing-Angleichung in `verify_password_or_dummy` (`app/auth/passwords.py`) zentralisiert → beide verbliebenen Login-Pfade (JSON-API `login` + UI `login_submit`); schloss eine in `0y7` übersehene Lücke in `login_submit`.
- **`fbq` (`d655a2f`, closed):** Upload-Größen-/Anzahl-Limit für beide Upload-Pfade. Guard `app/images/upload_limits.py` (`MAX_UPLOAD_FILE_MB`=50, `MAX_FILES_PER_UPLOAD`=500, `0`=off; pro Request aus settings → `.env`+Neustart gilt für ALLE Uploads inkl. bestehender Galerien). Follow-up `m4ct` (ASGI-Body-Limit).
- **Verifikation:** 300 Unit-Tests grün, ruff+mypy sauber, CHANGELOG (Security) + `.env.example` aktuell. Alles gepusht (`main` = `origin/main`). Backend-Härtungen — Kapitän deployt/nimmt ab.

### Erledigt in der Wache davor (2026-06-15) — Fotograf-Galerie: Filter-Chips + Lightbox-Filter, Admin-Limit-Modal
- **`3rl` (bug P2, `9921ed6` + `2d7e650` Doku, closed):** Owner-Grid Filter-Chips „Alle/Ausgewählt/Favoriten" funktionsfähig. Waren reine `<span>`-Deko → client-seitiger Filter im `galleryManager` (`activeFilter`, `setFilter`, `isVisible`, `chipClass`); Auswahldaten via `data-selections`-JSON am Root (`get_current_selections` jetzt auch im Detail-Context). Tiles per `x-show="isVisible(id)"`.
- **`ggx` (bug P3, `b04bb21` → `6b20e29` korrigiert + `d449e23` Doku, closed):** `Undefined variable: limitValue` auf `/admin/users`. Ursache: Limit-Modal-`<dialog>` lag direkt im `<tbody>` (invalides HTML → Parser foster-parented es aus der `<tr>`, Alpine-Scope weg). Fix: Modal in die „Limit"-`<td>` verschoben (gültig, bleibt im Row-Scope).
- **`ml0` (bug P3, `794a2db` + `35eb690` Doku, closed):** Owner-Lightbox (`x4o`) navigierte über ALLE Bilder trotz aktivem Grid-Filter. Neuer `visibleImages`-Getter (`images` gefiltert via `isVisible`); `next/prev/open/preload/currentImage` + Counter/Next-Pfeil/Swipe-Hint (`_owner_lightbox.html`) nutzen die gefilterte Sicht. `lightboxIndex` = Index in `visibleImages`.
- **Verifikation:** 62 Frontend-Unit-Tests grün; ml0 Playwright-abgenommen am lokalen Stack (Filter Ausgewählt 1/3..3/3, Favoriten 1/2..2/2, Alle 1/24; keine JS-/CSP-Fehler). `cxs` (Share-Sessions gesperrter User) als bereits-umgesetzt **code-verifiziert** (Owner-Status-Check im Guest-Resolver `app/guest/router.py:_resolve_gallery_by_token`). **Alles gepusht (`main` = `origin/main`); 3rl/ggx/ml0 lokal abgenommen, noch NICHT prod-deployed.**

### Erledigt in der Wache davor (2026-06-13 Spät) — Fotograf-Galerie: Lightbox + Auswahl-Export (User-Findings)
- **`x4o` (bug P2, `d295d28`, closed):** Read-only Bild-Lightbox in der Fotografen-Galerie. Ersetzt das alte Einzelbild-`previewModal` durch eine vollwertige Lightbox (Pfeile, Tastatur ←/→/ESC, Mobile-Swipe, Nachbar-Preload) — **ohne** Auswahl-Controls (Fotograf betrachtet nur). `galleryManager` (components.js) um Lightbox-State erweitert; `data-images`-JSON (nur `ready`-Bilder) am Root; neues Partial `_owner_lightbox.html`; Grid öffnet per UUID (`openLightboxById`). Dateiname XSS-sicher via `x-text`.
- **`r84` (feature P2, `a7a64da` Backend + `68c998b` Frontend, closed):** Auswahl-Ergebnis-Seite + Lightroom-Export. Neue owner-only Seite `/galleries/{id}/selection` listet alle markierten Bilder (selected ODER favorited) mit Vorschau/Symbol/Dateiname; verlinkt von der Detailseite bei `shared`/`completed`. **„Für Lightroom kopieren"** (Clipboard, komma-separiert) via neuer Alpine-Komponente `copyButton` + Downloads `.txt`/CSV mit `filter=marked`. Backend: Export um `format=txt` (komma-separierte Dateinamen mit Endung, einzeilig — Recherche: Lightroom Classic/Capture One brauchen genau das, NICHT eine pro Zeile) + `filter=marked` erweitert. CHANGELOG (`d81a82a`) + README aktualisiert.
- **286 Unit + 5 Integration-Tests grün**, ruff+mypy sauber. Alles gepusht (`main` = `origin/main`). **Noch NICHT prod-deployed** (Kapitän deployt + nimmt ab).
- **Dev-DB-Hinweis:** Die lokale „QA-Galerie" (`766dc953-…`) wurde für die r84-Abnahme auf `status=shared` gesetzt + 4 Test-Markierungen (3 select, 2 favorite) angelegt. Bei Bedarf zurücksetzen (`status=draft`, `selection_events` löschen).

### Erledigt in der Wache davor (2026-06-13 Abend) — v0.6-Epic + IP-Erfassung + Galerie-Limit-Override

**v0.6-Epic `3av` (alle 4 Tickets, Epic geschlossen):**
- **`tln` (`86627f0`, closed):** Setup-Onboarding auf Editorial Dark — `setup/index.html` auf `auth_base.html` umgestellt (Muster login/signup/verify). Macros `field`/`button`/`csrf_input`/`form_error`. Test-Drift behoben (war in Standalone-Liste, jetzt auth_base-Kinder).
- **`026` (`df20a14`, closed):** Admin Pending Signups auf Editorial Dark — `admin/pending.html` mit sticky Header, `bg-surface-base/90`-Blur, Token-Tabelle, `_signup_row.html` mit Approve/Reject via button-Macro + HTMX.
- **`atj` (`35086f0`, closed):** Audit-Log-Viewer auf Editorial Dark — sticky Header, semantischer Breadcrumb-Nav, Token-`<select>` für Eventfilter, `_audit_log_table.html` mit `overflow-x-auto`, Event-Badge `bg-accent/10 text-accent`, Token-Pagination.
- **`3av` (Epic):** ✅ geschlossen — alle Sub-Tickets durch (`gmo` Auth-Pages, `9ql` Expired-Page, `tln` Setup, `026` Pending Signups, `atj` Audit-Log).

**Standalone-Tickets:**
- **`1qa` (`c39f7ee`, closed):** IP-Adresse bei Pending Signups erfasst + angezeigt. Neuer Helper `app/auth/utils.py::get_client_ip()` — Priorität CF-Connecting-IP → X-Forwarded-For → request.client.host (Proxy-sicher). Migration `0006` (`ip_address String(45)`, nullable). Beide Signup-Pfade (API + Frontend). Admin-Tabelle: neue Spalte `hidden sm:table-cell`, `font-mono`. 6 Unit-Tests. **Prod-verifiziert: CF-Connecting-IP korrekt erfasst.**
- **`56k` (`ec9324f`, closed):** Per-User-Galerie-Limit-Override in Admin-UI. `User.gallery_limit_override` (nullable Integer, 0=unbegrenzt, None=global-Default). Migration `0007`. `assert_within_gallery_quota()` um `limit_override`-Parameter erweitert; beide Quota-Aufrufer (API + Frontend-Dashboard) übergeben `user.gallery_limit_override`. Neuer Service `set_gallery_limit_override()` + `POST /admin/users/{id}/gallery-limit`-Endpoint. Admin-Tabelle: Limit-Spalte + Limit-Modal (leeres Feld = zurücksetzen). AuditLog-Event `user_gallery_limit_changed`. 6 Unit-Tests.
- **286 Unit-Tests grün**, ruff+mypy sauber, alle Tickets gepusht und prod-deployed.

### Erledigt in der Wache davor (2026-06-13 Tag) — Guest-/UX-Findings, Infinite-Scroll, Expired-Page
- **`aku` (P2 bug, `4c123d4`, closed):** Dashboard-Stat-Kacheln auf Mobil unbenutzbar → `grid-cols-2` (2×2), scrollt mit, Desktop unverändert.
- **`8wv` (P2 bug, `2bc1b96`, closed):** Reaktivierender Button `completed→shared` → i18n-Key `transition_reopen` („Erneut öffnen"). Status-Pill in Aktions-Button-Gruppe.
- **`jwc` (P3 bug, `def9f5d`, closed):** Mobile-Lightbox-Swipe-Hint: Alpine-State `showSwipeHint`, selbstlöschender 3,5s-Timer.
- **`am9` (P2, `aae3e5b`, closed):** Guest-Grid Infinite-Scroll — 30 initiale Items, HTMX-`revealed`-Sentinel, Grid-Bindings auf Bild-id (O(1)-Map). Initiales HTML ~265 KB statt ~630 KB.
- **`9ql` (P3, `40bb388`, closed):** Guest-Expired-Page auf `auth_base.html` (Editorial Dark, dark+light).

### ✅ Betrieblicher Rest — alle Tickets prod-deployed und abgenommen
Kapitän hat nach Abend-Wache-Deploy bestätigt: alles live, IP-Erfassung funktioniert (CF-Connecting-IP), Abschluss-Mail-Kette (`16l`+`4gr`) prod-verifiziert.

### Offene Tickets — Stand 2026-06-16
**Ready-Queue: 8 offen** (alle aus den 2 externen Security-Reviews + 1 Follow-up; **kein aktiver Epic/Plan**). `bd ready`:
- **P2:** `d7z` (Guest-Logik entdoppeln — größter Brocken/Refactoring, Security-Wartbarkeit) · `8ox` (Cookie-Security: Secure-Flag/HSTS hinter Proxy + Logout-Löschung — **braucht Kapitän-Klärung**, s. Offene Punkte #1)
- **P3:** `m4ct` (ASGI-Body-Limit, Follow-up zu `fbq`) · `1gd` (Betrieb: Migration-Race + `selection_events`-Wachstum) · `ath` (Expiry-Vergangenheits-Check) · `e5n` (`func.count()` statt `len(all())`, 5 Stellen) · `bkw` (DRY: `purge_gallery` + `ALLOWED_TRANSITIONS`) · `ccx` (Image-Bomb `MAX_IMAGE_PIXELS`)
- Plus `9q3.6` deferred (Tastatur-Shortcuts).

### Offene Punkte für die nächste Wache
1. **`8ox` braucht Kapitän-Input vor Umsetzung:** Läuft Prod definitiv hinter Cloudflare/Caddy, und ist der App-Container **ausschließlich** über den Proxy erreichbar? Davon hängt ab, ob `trusted_hosts=["*"]` (bzw. uvicorn `--forwarded-allow-ips`) vertretbar ist. Beleg, dass der Container HTTP sieht: der `0hp`/`build_share_url`-Workaround existiert genau deswegen — derselbe Effekt lässt aktuell auch `secure`-Cookie-Flag + HSTS-Header in Prod ausfallen.
2. **Deploy-Stand:** Die 5 Fixes dieser Wache (`vre`/`pc6`/`0y7`/`6bs`/`fbq`) + die 3 aus 2026-06-15 (`3rl`/`ggx`/`ml0`) sind gepusht, aber Kapitän deployt + nimmt gegen Prod ab. `0y7`/`6bs`/`fbq` sind Backend-Härtungen (kein visueller Test nötig); `vre`/`pc6` betreffen Texte/Mail.
3. **Externe Security-Reports immer gegen echten Code prüfen** (s. neue Lesson in `docs/lessons-learned.md`) — beide Reports überschätzten Schweregrade und analysierten teils veraltete/falsche Pfade.
4. **QEMU-Flakiness:** Docker-Build gelegentlich `exit code: 132` (SIGILL) → `gh run rerun --failed`.
5. **Beads-Export vor Wachwechsel:** `bd export > .beads/issues.jsonl` + committen (nur **Issues** syncen via git, kein Dolt-Remote; **Memories** bleiben bewusst lokal — s. „Session Completion").

### Asset-Cache-Busting + Beads-Dedup (2026-06-10) — `picture-stage-d33`, closed
- **Cache-Busting (`d33`; Commits `9f1ef27` Code, `fd3b01b` Doku) — prod-verifiziert:** JS/CSS-Assets tragen jetzt `?v=<ASSET_VERSION>` via zentralem `asset()`-Jinja-Helper (`app/frontend/deps.py`) + Setting `asset_version` (`config.py`). `Dockerfile` ARG `ASSET_VERSION` (Default `dev`), CI (`docker-publish.yml`) setzt den **Build-Timestamp** (kein Git-SHA → kein Commit-Leak im HTML). 6 Templates umgestellt; **Fonts bewusst un-versioniert** (Preload/`url()`-Mismatch → Doppellading). Deploy-Runbook im README (`pull → up -d → grep version → curl ?v= → CF-Purge`), Fallstricke in `docs/lessons-learned.md`. Verifiziert: ruff+mypy+76 Frontend-Unit-Tests grün; **Prod-Live (2026-06-10, Playwright): Assets liefern `?v=20260610130149` (echter Build-Timestamp), HTTP 200.** **Betrieblicher Rest:** CF-Caching-Level einmalig auf „Standard" bestätigen (nicht „Ignore Query String"), damit der Edge-Bust bei künftigen Builds greift.
- **A11y `p07.1` (`16cccba`) — prod-verifiziert:** Theme-Toggle (`base.html`) mit `aria-label` (i18n `nav.toggle_theme`) + dynamischem `aria-pressed` (via `syncThemeToggleState()` in `app.js`) + Label-Spans `aria-hidden`. **Prod-Live (2026-06-10, Playwright): `aria-label`/`aria-pressed`/`aria-hidden` gesetzt, `data-theme` ↔ `aria-pressed` über Klicks korrekt gekoppelt.** `p07.2` (Kontrast) + `p07.3` (Signup-`role=alert`) als bereits-konform geschlossen (rechnerisch/Code-belegt). Voll-Audit am 2026-06-10 durchgeführt → siehe nächster Abschnitt.

### A11y-Voll-Audit + Guest-Viewer-Fixes (2026-06-10) — `picture-stage-p07` ✅ **CLOSED** (alle 8 Sub-Findings durch)
Prod-Voll-Audit (öffentlich + Guest, Playwright/haiku, **strukturell** via Accessibility-Snapshot + feste DOM-Skripte — **axe-core nicht nutzbar** wegen CSP `script-src 'self'`, siehe `docs/lessons-learned.md`). **Viele haiku-Fehlalarme gefiltert** (Select/Favorite-Buttons haben aria-labels, openLightbox hatte img-`alt`-Namen, Separatoren `|`/`·`/`*` dekorativ, Lightbox Tastatur+Buttons sauber). 3 echte Findings gefixt — **deployed + prod-verifiziert 2026-06-10 abends** (Playwright/haiku + manuelle Screenshot-Prüfung, Build `?v=20260610192441`):
- **`p07.4` (`2b2e9c7`):** Sort-Dropdowns `sort_by`/`sort_dir` (`viewer.html`) bekommen `aria-label` via i18n `guest.sort_by_label`/`sort_dir_label`. WCAG 4.1.2/3.3.2.
- **`p07.5` (`9d51e8d`) — war versteckter Doppel-Header-Bug (qdz-Rest):** `viewer.html` erbte den alten `guest_base`-Header UND hat einen eigenen Editorial-Dark-Header → 2× Header + 2× `<h1>`. Fix: `guest_base.html`-Header in `{% block guest_header %}` gekapselt (Default bleibt für `expired.html`), `viewer.html` leert ihn + übernimmt den Language-Switcher in den Editorial-Header. WCAG 1.3.1. ✅ **Visuelle Prod-Abnahme bestanden (2026-06-10):** DOM-Count genau 1 `header`/1 `<h1>`, Language-Switcher im Header funktional (EN-Wechsel getestet), Layout 1280px+375px optisch intakt, Konsole sauber.
- **`p07.6` (`92e80a7`):** openLightbox-Overlay-Button (`_image_grid.html`) bekommt `aria-label` „Bild öffnen: {filename}" (i18n `guest.open_image`). WCAG 2.4.4.
- **`p07.7` (`24b6f6a`) ✅ closed:** Kontrast rechnerisch geprüft (echte Token-Hexwerte, Alpha-Blends) — ALLE gemeldeten Token-Kombos bestehen AA deutlich (7.39–13.46:1), Audit-Werte 3.04–3.77 waren BG-Erkennungs-Fehlalarme. **Ein echter Verstoß gefixt:** Complete-Modal-Confirm-Button (altes v0.4-Styling, white auf green-600 = 3.30:1) → Tokens `bg-accent`/`text-text-on-accent` = 7.88:1. Chip-Border accent/40 (2.45:1) = Non-Issue (Komponente via BG+Text identifizierbar).
- **`p07.8` (`f15e471`) ✅ closed:** Skip-Link (`sr-only focus:not-sr-only`, Token-Styling) als erstes fokussierbares Element in `base.html` + `guest_base.html` + den 4 Standalone-Heads (login/signup/verify/setup); dort Karten-Wrapper zu `<main id="main">` → fehlende main-Landmark gleich mitgewonnen. i18n `common.skip_to_content` (DE/EN). 2 Regressions-Tests. WCAG 2.4.1.
- **Schirm `p07` geschlossen (2026-06-10):** alle 8 Sub-Findings durch und **vollständig prod-verifiziert** — `p07.7`/`p07.8` am 2026-06-10 spät mit Build `?v=20260610200248` live abgenommen (Skip-Link: erster Tab fokussiert, sichtbar als fixed-Pille, Enter springt auf `#main`, auf /login + Guest-Viewer; Modal-Button: computed `rgb(52,211,153)`/`rgb(2,44,34)` = Tokens live).
- **Nächster Schritt:** `dd1` (Light-Mode Guest) oder `qdz.15` (Password-Gate-Mockup).

### Guest Light-Mode-Toggle (2026-06-10) — `picture-stage-dd1`, closed, ⚠️ Deploy-Abnahme offen
- **`dd1` (`948669c`):** Theme-Toggle-Button (Sonne/Mond via `data-theme-label`-Spans) im Editorial-Header des Guest-Viewers + im `guest_base`-Header (Expired-Page). **Infrastruktur war komplett da** (app.js-Bootstrap/`toggleTheme`/localStorage liefen schon auf Guest-Seiten) — nur der Button fehlte. Rein additiv, kein neues JS, keine neuen i18n-Keys (`nav.toggle_theme` wiederverwendet), ARIA nach p07.1-Muster. 231 Unit-Tests grün.
- ⚠️ **Nach nächstem Deploy abnehmen (zusammen mit dxj):** Toggle im Guest-Viewer (Magic-Link nötig), Light-Lesbarkeit Grid/Chips/Counter, Persistenz über Reload, Icon-Wechsel Sonne↔Mond.

### Top-Nav-Umbau (2026-06-10) — `picture-stage-dxj`, closed, ⚠️ Deploy-Abnahme offen
- **`dxj` (`d8574e0`):** Photographer-Nav (`base.html`) als Flexbox — Brand links, rechts Dashboard/Admin+Badge/„Einstellungen"-Dropdown/Logout. Theme-Toggle + DE/EN-Switcher wandern ins neue Dropdown (i18n `nav.settings`). Neue CSP-konforme Alpine-Komponente `settingsMenu` (`components.js`: `toggle()`/`close()`-Methoden, `expanded`-String-Getter, damit `:aria-expanded` im Zu-Zustand nicht wegfällt), `aria-controls`, ESC + Click-outside schließen. Token-Styling. `data-theme-toggle`-Verdrahtung (app.js, HTMX-Swap-sicher) unverändert. 230 Unit-Tests grün (2 neue).
- ⚠️ **Nach nächstem Deploy abnehmen:** Dropdown öffnen/ESC/outside-close, Theme-Wechsel + Sprachwechsel aus dem Menü, Badge/HTMX unbeeinträchtigt, Optik (eingeloggt — Login-Zugang nötig, Playwright-Agent braucht Credentials vom Kapitän oder Kapitän prüft selbst).
- **Beads-Dedup (`f612ecc`):** Zwei parallele v0.5-Epics entwirrt — `qdz` (Reduced-Scope, Guest-Focused) ist **das einzige aktive v0.5-Epic**. 4 Dubletten geschlossen (`17o`/`jmq` Guest-Lightbox via `qdz.13/14` erledigt, `1p1`/`2sy` Guest-Password-Gate aktiv als `qdz.15/16`). `p07` (A11y-Audit, P1) aus dem alten Epic gelöst → **eigenständiges Querschnittsticket**. `3av` umgewidmet → „v0.6 UX-Redesign – Photographer-Pages" (P1→P2, Label `v0.5`→`v0.6`).

### CSP-Hardening abgeschlossen (2026-06-10) — Epic `picture-stage-u3s`, closed, prod-verifiziert
- **`unsafe-eval` aus der CSP entfernt:** `script-src 'self'` (vorher `'self' 'unsafe-eval'`, `app/security/middleware.py`). Vendored Alpine-Build von Standard auf **`@alpinejs/csp` 3.15.12** umgestellt (evaluiert Expressions ohne `Function`-Konstruktor → kein `unsafe-eval` mehr nötig). Defense-in-Depth gegen XSS (CHANGELOG → Security).
- **~20 Inline-Expressions migriert (u3s.1/4/5):** Globals (`document`/`localStorage`/`window`/`navigator`/`Math`) + Arrow-Functions + `$refs`/`$el`-DOM-Methoden raus aus Inline-Attributen → `Alpine.data()`-Komponenten (`langSwitcher`, `cookieBanner`, `auditFilter`, `shareUrl` + Methoden in `uploadZone`/`guestViewer`/`galleryManager`) + **delegierte `data-*`-Listener in `app.js`** (`data-open-dialog`/`data-close-dialog`/`data-backdrop-close`/`data-close-dialog-on-success`/`data-auto-open`). Modal-Macro (`_macros/modal.html`) entkoppelt von `$refs`. Läuft seit u3s.1 unter Standard-Build identisch (jeder Zwischenstand deploybar).
- **Stolpersteine in `docs/lessons-learned.md`:** (1) `@alpinejs/csp` 3.14.8 hatte einen restriktiven Parser (nur Property-/Methoden-Zugriff, KEINE Ternäre/`{}`); erst **3.15+** versteht Ternäre/Arithmetik/Objekt-Literale — context7-Doku spiegelt `main`, NICHT die gepinnte Version (gegen `gh api .../ref=v<VERSION>` prüfen). (2) **3-Schichten-Cache-Falle:** GHA-Layer-Cache (ARG-Default-Change bustet ihn NICHT → Version **inline in die RUN/URL** pinnen), Origin-Image (`docker compose up -d` **ohne `pull`** läuft alt weiter), Cloudflare-Cache (statische Assets `max-age=14400` = 4h → purgen). **Diagnose von innen nach außen: Build-Log → Container (`grep version`) → CDN-Header.**
- **Cloudflare-Insights CSP-Warnungen (`picture-stage-z4c`, closed):** externes `beacon.min.js` + inline-Loader (Hash wechselt pro Request) von `script-src 'self'` geblockt = **Non-Issue** (extern, nicht u3s; `script-src` war schon immer `'self'`). Lösung: CF Web Analytics für die Zone deaktivieren. CSP NICHT mit `unsafe-inline` aufweichen.
- **Erledigt (2026-06-10):** `picture-stage-d33` ✅ closed — Cache-Busting via `?v=<build>`-Query-String umgesetzt; Details im Abschnitt „Asset-Cache-Busting + Beads-Dedup" oben.

### Verifizierungsmail beim Signup (2026-06-10) — `picture-stage-x8t`, closed, CI-verifiziert
- Beide Signup-Pfade (`app/auth/router.py` API + `app/frontend/auth.py` Web-Form) verschicken jetzt eine Verifizierungsmail mit Bestätigungslink. Neue config-freie `send_verification_email()` (`app/notifications/service.py`) nach dem `notify_admins_signup`-Muster: gated über Setting `SEND_VERIFICATION_EMAIL_ENABLED` (default true) + gesetztem `SMTP_HOST`, per-Empfänger try/except (Mail-Fehler bricht Signup nie ab). Link aus `APP_URL` (HTTPS), Token im Klartext in der Mail / SHA-256+Salt in DB. **Mail nur im echten Neu-Signup-Pfad → Account-Enumeration-Guard (`42q`) bleibt intakt.** Templates `verify_email.{html,txt}`. 9 Unit-Tests. (Hinweis: alter TODO-Marker referenzierte fälschlich das geschlossene `ebm.7` = Share-Link-System.)

### SMTP-Inbetriebnahme + Admin-Signup-Mail + Enumeration-Fix (2026-06-08) — alles prod/CI-verifiziert
- **SMTP provisioniert (Mailjet):** `.env` befüllt (HOST=`in-v3.mailjet.com`, PORT=587, USER=API-Key,
  PASSWORD=Secret-Key, FROM=verifizierte Domain, STARTTLS=true). Smoke-Test-Tool `scripts/smtp_smoke.py`
  (interaktiv, maskiert Secrets, gleicher `aiosmtplib`-Pfad wie Prod-Mailer). Versand prod-getestet ✅.
  **Hinweis:** Mailjet-Egress ist aus der Sandbox NICHT erreichbar — SMTP-Tests laufen nur auf dem Server.
- **Signup-Mail an alle Admins (`picture-stage-ka6`, closed, prod-verifiziert):** Neue Funktion
  `notify_admins_signup()` in `app/notifications/service.py` — System-Override, **config-frei** (umgeht die
  per-User `NotificationConfig`, für die es kein UI gibt → config-gated wäre wirkungslos). Gated über Setting
  `NOTIFY_ADMINS_ON_SIGNUP` (default true) + gesetztem `SMTP_HOST`. Trigger an BEIDEN Signup-Pfaden
  (`app/frontend/auth.py` + `app/auth/router.py`), je in try/except → Mail-Fehler bricht Signup nie ab,
  Fehler pro Empfänger isoliert. Commit `e1bf3be`. Echter Signup → Admin-Mail empfangen.
- **Account-Enumeration-Fix (`picture-stage-42q`, P1 SECURITY, closed):** Existierende E-Mail (User ODER
  Pending) liefert jetzt dieselbe neutrale Erfolgsantwort wie ein frischer Signup — kein 409, kein neuer/
  überschriebener PendingSignup (Takeover-Vektor). Zwei versteckte Vektoren mitgeschlossen:
  (1) `verification_token` aus `SignupResponse` entfernt (**BREAKING API** — token-vs-null wäre selbst ein
  Leak; im CHANGELOG dokumentiert), (2) Timing-Angleich via bcrypt-Dummy (Best-Effort). i18n-Leak-Keys
  `auth.email_registered`/`auth.signup_pending` (DE+EN) entfernt. Commit `7a5ebb7`. 5 Unit- + 4 Integration-Tests.
- **CI-Reparatur:** Integration-Tests posteten `@test.local` → `EmailStr` lehnt reservierte TLDs ab → 422
  statt 201 (Commit `030b327` fixt auf `@example.com`). Path-Filter um `tests/**` erweitert (`873f741`),
  weil ein test-only-Commit sonst CI **überspringt** und trügerisch grün meldet. **CI final: 279 passed.**
  Beide Stolpersteine in `docs/lessons-learned.md` (Commit `af3d85f`).

### Async Multi-Upload (2026-06-08) — `picture-stage-o4d`, closed, live auf Prod verifiziert
- **Problem:** Upload vieler Bilder fror die UI ~20s ein — alle Previews (Thumbnails/Watermark via Pillow)
  liefen synchron im Request und blockierten den Event-Loop für ALLE Requests.
- **Lösung:** Upload speichert nur Originale + `Image`-Rows (`processing_status=pending`) und kehrt sofort
  zurück. Ein per-Bild `BackgroundTasks`-Worker (`app/images/preview_worker.py`) generiert die WebP-Varianten
  in `asyncio.to_thread` (Event-Loop bleibt frei), eigene `async_session()`, liest Original aus Storage
  zurück, setzt `ready` bzw. `failed` (separate Transaktion). Tenant-Isolation via `(image_id, gallery_id)`.
- **Frontend:** Grid rendert je `processing_status` Thumbnail/Spinner/Fehler-Kachel. Selbstterminierendes
  Polling: Wrapper trägt `hx-trigger="every 2s"` nur solange ein Bild `pending` ist → stoppt automatisch,
  sobald alle settled. i18n `gallery.processing` / `gallery.processing_failed` (DE+EN).
- **DB:** Migration `0004` — Enum `imageprocessingstatus` + Spalte. Backfill der Bestandsbilder auf `ready`
  via transientem `server_default` (danach gedroppt → ORM-Default `pending` für Neue). Siehe Stolperstein
  in `docs/lessons-learned.md`.
- **Verifiziert:** ruff+mypy+205 Unit-Tests grün; CI inkl. Migration 0004 gegen Postgres; **Live-Smoke auf
  Prod:** 12×12MP-Upload → Grid sofort mit Spinnern, kein Freeze, Polling im 2s-Takt, 12/12 ready, Polling
  stoppt selbst. 4 atomare Commits (`9eff000` DB, `ae9f306` Worker, `229ef00` Grid+Polling, `045429f` Tests).

### Neue offene Tickets aus User-Findings (2026-06-08)
- ~~`picture-stage-42q` (P1 SECURITY, Signup-Enumeration)~~ ✅ closed 2026-06-08 — siehe SMTP-Abschnitt oben.
- **`picture-stage-dxj` (P2):** Top-Nav umbauen — Brand links, Aktionen rechts, Theme-Toggle + Sprachwechsel
  in neues „Einstellungen"-Dropdown (`nav.settings`). Kein generisches Dropdown-Macro vorhanden → neu bauen.
- **Logischer nächster Schritt (`picture-stage-ebm.7`):** User-Verifizierungsmail verdrahten — beide
  `# TODO: send verification email`-Marker (`app/auth/router.py`, `app/frontend/auth.py`) stehen noch.
  SMTP läuft jetzt produktiv → spruchreif.

### Security-Härtung (2026-06-08) — Share-Link HTTPS + JWT-Invalidierung
- **Share-Links immer HTTPS (`picture-stage-0hp`, closed):** Zentraler Helper `build_share_url()`
  in `app/galleries/sharing.py` ersetzt drei `request.base_url`-Duplikate (API-Router + 2× Frontend).
  Hinter dem TLS-terminierenden Proxy (Cloudflare/Caddy) sah der Container nur HTTP → das replaybare
  Share-Token leakte über `http://`. Jetzt: URL aus `APP_URL` (Source of Truth), Scheme in Produktion
  zwingend `https://` (Defense-in-Depth bei fehlender/falscher Konfig). **Betrieb:** `APP_URL` muss
  in Prod auf die öffentliche HTTPS-Domain zeigen (in `.env.example`/README dokumentiert). 4 Unit-Tests.
- **JWT-Invalidierung bei PW-Reset/Sperren (`picture-stage-7kr`, closed):** Stateless-Tokens blieben
  bis zu 24h nach Reset/Sperre gültig. Neu: `iat`-Claim auf Tokens + per-User-Cut-off
  `users.tokens_valid_after` (Migration `0003`, nullable timestamptz). Zentraler Check `_token_revoked()`
  in `app/auth/dependencies.py` weist Tokens vor dem Cut-off ab — wirkt für API (`get_current_user`)
  UND Cookie-Frontend (`get_user_from_cookie`). `reset_user_password` + Sperren (`status→disabled`)
  setzen den Cut-off auf `now()`. NULL-Default = kein Massen-Logout beim Deploy. Zeitstempel rein
  server-seitig (kein Client-Clock-Skew). 6 Unit- + 4 Integration-Tests. **Beide Härtungen im CHANGELOG.**

### Guest-Persistenz + Lightbox (2026-06-08) — live auf Prod verifiziert
- **Gast-Auswahl galerie-weit persistent (`picture-stage-7ih`, closed):** Auswahl/Favoriten werden
  über ALLE Sessions der Galerie materialisiert (`get_current_selections(gallery_id, db)` in
  `app/selections/service.py` — Session-Filter entfernt). Magic-Link = ein Model → Auswahl überlebt
  Fenster-Schließen UND Geräte-Wechsel (Smartphone→PC). Read-only-Gate galerie-weit via
  `gallery.status == completed` (`app/guest/router.py`), nicht mehr pro Session. Frontend: Alpine
  `completed`-Flag sperrt `toggle*`/`submitComment`, Read-only-Banner. **Prod-Beweis:** Cookies
  gelöscht → neue Session-ID → Auswahl bleibt (nur `csrf_token`-Cookie, KEIN Session-Cookie).
- **Guest-Lightbox Editorial-Dark (`qdz.13` Spike + `qdz.14` Impl, beide closed):** Token-basiert
  (Dark+Light via `data-theme`), A11y (aria-label/aria-pressed/focus-ring), Inline-SVG statt Glyphen,
  theme-aware Scrim (`bg-surface-overlay`), Mobile-Pfeile ab `sm:` + Swipe. JS-Logik (Tastatur/Swipe)
  unberührt. **Prod-Test:** ←/→/ESC funktional, Select-Toggle, Dark UND Light lesbar. Spike unter
  `frontend/static/spikes/guest_lightbox.html`.
- **Stolpersteine dokumentiert** (`docs/lessons-learned.md`): eingechecktes `styles.css` ist nur
  Stub (echtes CSS im Docker-`css-builder`); `text-inverse`-Token ist theme-invertiert.
- **3 neue Tickets aus User-Findings:** `picture-stage-7ih` (Persistenz, ✅ done),
  `picture-stage-dd1` (Light-Mode-Toggle für Guest-Pages, P2 offen),
  `picture-stage-a15` (Cookie-Banner: Zweck klären — Gäste haben nur csrf, kein Session-Cookie, P3).

### CI/CD-Pipeline (Docker Hub, 2026-06-08)
Vollautomatischer Multi-Arch-Build (amd64+arm64) nach Docker Hub `stoertebeker2k/picture-stage`.
Zwei Workflows, gated über Job-Graph `changes → ci → build-and-push`:
- **`ci.yml`** ist reusable (`workflow_call`) + `pull_request` — **kein** direkter `push`-Trigger
  mehr (lief sonst doppelt). Enthält lint (ruff) + format-check + mypy + pytest (Postgres-Service).
- **`docker-publish.yml`** triggert auf `push → main` **und** Tags `v*`. Ruft `ci.yml` als
  vorgelagerten Job → `build-and-push` hat `needs: ci`. **Kein Image aus rotem Code, auch kein `:dev`.**
- **Tag-Strategie:** `main`-Commit → `:dev` + `:sha-<hash>` (Test-Server zieht `:dev`);
  Versions-Tag `v*` → semver + `:latest` (via `latest=auto`, nur bei Release-Tags). Saubere
  Trennung dev/stable über `is_default_branch`.
- **Path-Filter (`dorny/paths-filter`):** `main` baut nur bei Änderungen an `app/`, `alembic/`,
  `frontend/` (seit `00e9b94`, 2026-06-10 — fehlte vorher: frontend-only-Commits bauten still KEIN
  Image, siehe `docs/lessons-learned.md`), `tests/`, `Dockerfile`, `pyproject.toml` oder den
  Workflow-Dateien — spart Action-Minuten bei Doku-Commits.
  Tags `v*` bauen **immer** (Filter wird bei Tag-Push übersprungen via `startsWith(github.ref,…)`).
- **Verifiziert:** Live-Run auf `bb03bc6` grün (2m 22s, Cache-Hit 74%), Job-Graph wie geplant.

### Was ist fertig
- v0.1–v0.4 vollständig (API, Lifecycle, Compliance, Frontend funktional)
- 196 Unit-Tests grün (lokal, DB-frei); CI gegen Postgres-Service (Integration/Security); ruff format + ruff check + mypy strict grün
- DB-Migrationen produktionsreif; Migration↔ORM-Drift-Guard in CI
- i18n DE+EN vollständig — alle hardcoded Strings auf Keys (`auth.*`, `gallery.*`, `admin.*`)
- **v0.5 Foundation komplett:** Design-Tokens (`docs/design/tokens.md`), Tailwind-Config,
  Web-Fonts self-hosted (Inter + Fraunces WOFF2), Dark-Mode-Bootstrap, Layout-Primitives,
  Komponenten-Inventar
- **v0.5 Komponenten komplett:** Button (`_macros/buttons.html`), Form (`_macros/forms.html`),
  Modal/Dialog (`_macros/modal.html`)
- **v0.5 Guest-Viewer komplett:** Spike + Template auf Editorial Dark
- **Admin-User-Verwaltung komplett (Epic `picture-stage-uwy`, closed):** siehe nächster Abschnitt

### Admin-User-Verwaltung (Epic `picture-stage-uwy`, closed 2026-06-07)
Vollständige Verwaltung bestehender Accounts durch Admins — API **und** Frontend-UI.
- **Neuer User-Status `disabled`** (Migration `0002`, nativer PG-Enum via `ALTER TYPE … ADD VALUE`).
  Zentrale Whitelist `LOGIN_ALLOWED_STATUSES` (`app/db/models.py`) = `{active, admin}`; an allen vier
  Auth-Punkten geprüft (API-Login, Form-Login, `require_active_user`, `require_authenticated_page`).
- **Service-Schicht `app/admin/service.py`** = Single Source of Truth für Geschäftslogik +
  Sicherheits-Leitplanken; API-Router (`app/admin/router.py`) und Frontend-Router
  (`app/frontend/admin.py`) sind dünne Adapter darauf. Service wirft `AdminActionError`
  (status_code + i18n_key); jeder Aufrufer übersetzt selbst (HTTPException bzw. Toast).
- **Leitplanken:** S1 kein Self-Sabotage, S2 letzter-Admin-Schutz (Defense-in-Depth), S4 Audit-Log
  je Mutation (`user_status_changed`/`user_deleted`/`user_password_reset`), Rate-Limits, CSRF.
- **Storage-aware Delete:** `purge_gallery` (`app/galleries/deletion.py`, aus Gallery-Delete extrahiert)
  wird pro Galerie aufgerufen → keine verwaisten Bilddateien (DSGVO). Danach Core-`delete(User)`.
- **Frontend:** `/admin/users` (Tabelle, Status-Badges, Aktionen via HTMX, Delete-/PW-Reset-Modals),
  Admin-Nav-Menü + lazy Pending-Badge (`/admin/nav-badge`) nur für Admins. `current_user` global im
  Template-Context (gesetzt in `get_user_from_cookie`, injiziert in `app/frontend/deps.py`).
- **Tests:** 21 Integration-Tests (`tests/integration/test_admin_users.py`, CI/Postgres) +
  DB-freie Unit-Tests (`tests/unit/test_auth_disabled_status.py`, `test_frontend_admin_users.py`).

**Follow-ups:**
| Punkt | Beads-ID | Status |
|-------|----------|--------|
| Visuelle/Playwright-UI-Verifikation `/admin/users` | `picture-stage-52s` | ✅ closed 2026-06-12 |
| Share-Sessions gesperrter User invalidieren/prüfen | `picture-stage-cxs` | P3 offen |

> `picture-stage-7kr` (JWT-Invalidierung) ✅ closed 2026-06-08 — siehe Abschnitt „Security-Härtung" oben.

### v0.5 – ✅ ABGESCHLOSSEN (2026-06-11)

Alle Sub-Tickets erledigt und prod-abgenommen. Epic `picture-stage-qdz` geschlossen.

**Hinweis Frontend-Verifikation:** Lokal kein Tailwind-Build im eingecheckten `styles.css` (= Stub), aber der **lokale Docker-Stack** baut echtes JIT-CSS (`css-builder`) → visuelle Abnahme gegen den lokalen Stack (`http://localhost:8000`). Prod (`https://picture.stoertes.cloud`) ist via Playwright/Headless **nicht** erreichbar (Cloudflare-Challenge) — Prod-Abnahme nur durch den Kapitän.

### Kleinere offene Punkte
- ~~Docker-Build verifizieren~~ ✅ erledigt 2026-06-08 (Pipeline live, siehe CI/CD-Abschnitt oben)
- Noch nicht live getestet: Doku-only-Commit (Build-Skip) und `v*`-Tag (`:latest`-Build)
- GitHub Actions: Node-20-Deprecation — Frist Sept. 2026
- ~~WATERMARK_OPACITY Breaking-Change-Hinweis in Release-Notes~~ ✅ steht im CHANGELOG (`[Unreleased] → Changed`)

### Epics
| Epic | Beads-ID | Status |
|------|----------|--------|
| v0.1 Minimal Viable Picdrop | `picture-stage-ebm` | closed |
| v0.2 Lifecycle & Komfort | `picture-stage-9q3` | closed (1 deferred) |
| v0.3 Produktion & Compliance | `picture-stage-fbr` | closed |
| v0.4 Frontend (funktional) | `picture-stage-gza` | closed |
| v0.5 UX-Redesign – Editorial Dark (Guest-Focused) | `picture-stage-qdz` | ✅ **CLOSED** (2026-06-11) — alle Sub-Tickets erledigt und prod-abgenommen |
| Admin-User-Verwaltung (API + Frontend) | `picture-stage-uwy` | closed (`7kr` ✅ done; 2 Follow-ups offen: `52s`/`cxs`) |

### Verifikation für neue Sessions
`bash scripts/verify-handover.sh` prüft den Übergabe-Stand
(clean tree, Tag vorhanden, Tools verfügbar, Tests grün).
