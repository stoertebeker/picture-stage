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
   Note: there is NO external Dolt remote. Beads issues + memories sync solely
   via `.beads/issues.jsonl`, which ships with the git push. Do NOT run
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

**Datum:** 2026-06-10
**Wachwechsel-Tag:** `handover-2026-06-10` (zeigt auf `765762a`, letzter grüner Stand: CSP-Hardening komplett — `unsafe-eval` raus, `@alpinejs/csp` 3.15.12 live + prod-verifiziert)
**Live:** Eine produktive Instanz läuft online (`https://picture.stoertes.cloud`, via Docker-Hub-Image). **Prod ist via Playwright-Tools erreichbar** — Live-Tests möglich ohne Netzwerk-Freischaltung. **SMTP ist seit 2026-06-08 produktiv konfiguriert (Mailjet) — Mailversand prod-verifiziert.**

### CSP-Hardening abgeschlossen (2026-06-10) — Epic `picture-stage-u3s`, closed, prod-verifiziert
- **`unsafe-eval` aus der CSP entfernt:** `script-src 'self'` (vorher `'self' 'unsafe-eval'`, `app/security/middleware.py`). Vendored Alpine-Build von Standard auf **`@alpinejs/csp` 3.15.12** umgestellt (evaluiert Expressions ohne `Function`-Konstruktor → kein `unsafe-eval` mehr nötig). Defense-in-Depth gegen XSS (CHANGELOG → Security).
- **~20 Inline-Expressions migriert (u3s.1/4/5):** Globals (`document`/`localStorage`/`window`/`navigator`/`Math`) + Arrow-Functions + `$refs`/`$el`-DOM-Methoden raus aus Inline-Attributen → `Alpine.data()`-Komponenten (`langSwitcher`, `cookieBanner`, `auditFilter`, `shareUrl` + Methoden in `uploadZone`/`guestViewer`/`galleryManager`) + **delegierte `data-*`-Listener in `app.js`** (`data-open-dialog`/`data-close-dialog`/`data-backdrop-close`/`data-close-dialog-on-success`/`data-auto-open`). Modal-Macro (`_macros/modal.html`) entkoppelt von `$refs`. Läuft seit u3s.1 unter Standard-Build identisch (jeder Zwischenstand deploybar).
- **Stolpersteine in `docs/lessons-learned.md`:** (1) `@alpinejs/csp` 3.14.8 hatte einen restriktiven Parser (nur Property-/Methoden-Zugriff, KEINE Ternäre/`{}`); erst **3.15+** versteht Ternäre/Arithmetik/Objekt-Literale — context7-Doku spiegelt `main`, NICHT die gepinnte Version (gegen `gh api .../ref=v<VERSION>` prüfen). (2) **3-Schichten-Cache-Falle:** GHA-Layer-Cache (ARG-Default-Change bustet ihn NICHT → Version **inline in die RUN/URL** pinnen), Origin-Image (`docker compose up -d` **ohne `pull`** läuft alt weiter), Cloudflare-Cache (statische Assets `max-age=14400` = 4h → purgen). **Diagnose von innen nach außen: Build-Log → Container (`grep version`) → CDN-Header.**
- **Cloudflare-Insights CSP-Warnungen (`picture-stage-z4c`, closed):** externes `beacon.min.js` + inline-Loader (Hash wechselt pro Request) von `script-src 'self'` geblockt = **Non-Issue** (extern, nicht u3s; `script-src` war schon immer `'self'`). Lösung: CF Web Analytics für die Zone deaktivieren. CSP NICHT mit `unsafe-inline` aufweichen.
- **Offen:** `picture-stage-d33` (P2) — **Cache-Busting für statische Assets + Deploy-Runbook**. Verhindert die 3-Schichten-Falle bei künftigen Frontend-Updates (Versions-Hash im Dateinamen ODER CF-Page-Rule + Runbook `pull → up -d → grep version → CF-Purge`). **Vor dem nächsten Frontend-Deploy angehen.**

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
  `Dockerfile`, `pyproject.toml` oder den Workflow-Dateien — spart Action-Minuten bei Doku-Commits.
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

**Offene Follow-ups (Beads):**
| Punkt | Beads-ID | Prio |
|-------|----------|------|
| Visuelle/Playwright-UI-Verifikation `/admin/users` | `picture-stage-52s` | P3 |
| Share-Sessions gesperrter User invalidieren/prüfen | `picture-stage-cxs` | P3 |

> `picture-stage-7kr` (JWT-Invalidierung) ✅ closed 2026-06-08 — siehe Abschnitt „Security-Härtung" oben.

### v0.5 – Noch offen (Epic `picture-stage-qdz`)

**Scope:** Nur Guest-Pages. Photographer-Pages (Dashboard, Auth, Admin, Audit-Log etc.) → v0.6+.

| Ausstehend | Beads-ID |
|------------|----------|
| ~~Guest-Lightbox Mockup + Impl~~ ✅ done (2026-06-08) | ~~`qdz.13`, `qdz.14`~~ |
| Guest-Password-Gate Mockup + Impl | `qdz.15`, `qdz.16` |
| i18n-Lücken schließen | `qdz.17` |
| Mobile-Tuning Guest-Pages (375/768/1280px) | `qdz.18` |

Einstieg: `bd ready` → `qdz.15` (PS-UX-22a, Guest-Password-Gate Mockup) oder `dd1` (Light-Mode-Toggle).
**Hinweis Frontend-Verifikation:** Lokal kein Tailwind-Build (styles.css = Stub) → visuelle Abnahme
gegen Spike oder live auf Prod (`https://picture.stoertes.cloud`, via Playwright erreichbar).

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
| v0.5 UX-Redesign – Editorial Dark (Guest-Focused) | `picture-stage-qdz` | Foundation + Komponenten + Viewer + **Lightbox** ✅, PW-Gate/i18n/Mobile offen |
| Admin-User-Verwaltung (API + Frontend) | `picture-stage-uwy` | closed (`7kr` ✅ done; 2 Follow-ups offen: `52s`/`cxs`) |

### Verifikation für neue Sessions
`bash scripts/verify-handover.sh` prüft den Übergabe-Stand
(clean tree, Tag vorhanden, Tools verfügbar, Tests grün).
