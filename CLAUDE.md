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
# fixtures rebuild schemas and truncate data — never use the app database)
pytest --cov=app --durations=30 --junitxml=test-results/pytest.xml

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

### Database (18 tables)
setup_state, users, galleries, images, image_previews, upload_cleanup_intents, storage_usage, storage_allocations, selection_events (append-only), current_selections, share_sessions, share_session_cleanup_state, audit_log, notification_configs, notification_deliveries, pending_signups, revoked_tokens, captcha_redemptions

### Key Design Decisions
- **Event-sourced selections:** `selection_events` is append-only. The transactional `current_selections` projection keeps independent selection, favorite and comment versions ordered by `(created_at, id)`, including backdated transactions. Guest pages and owner exports read this projection; history remains available for audit.
- **Guest routes:** HTML and JSON handlers share `/g/` path patterns. HTML is registered first; middleware must follow Starlette's first full match. Both use the shared token resolver, owner-status/expiry gates and password-session checks.
- **Share links:** Indexed SHA-256 locator followed by the salted verifier. Legacy links advance through bounded ID windows using signed progress cookies; a first resolution can span several requests. Cookies preserve actual encoded/mounted URL paths. Gallery hints are locators only; current credentials, owner status, expiry and authorization epoch still govern access. Replayable tokens remain available to the owner UI. User and gallery passwords use bcrypt.
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

**Stand:** 2026-11-09. Anwendungscode zuletzt geändert in `c3554e6`; der folgende Stand trennt abgeschlossene Umsetzung von zurückgestellten Entwürfen. Backend und Editorial-Dark-Frontend mit DE/EN, Light/Dark, Gast-/Fotografen-Lightbox und Auswahl-Export sind umgesetzt. Paketversion `0.1.0`; öffentliche GitHub-Issues #1–#6 sind geschlossen. Sprach-Switch beibehalten; Ablaufdatum endet am gewählten UTC-Tag, Formularmaximum 9999-12-30.

**Letzte Sicherheitswellen:** `a10v` setzt die Galeriequote atomar für Erstellen und Duplizieren durch; `9mtm` und `yxk9` isolieren Bildverarbeitung und begrenzen Zulassung pro Prozess. `e1nt`, `jijh` und `xlek` behandeln Mail-Empfänger, begrenztes Passwort-Hashing und Auswahlbudgets. Webhooks (`fx8n`, `4llu`) prüfen und pinnen öffentliche Ziele ohne Umgebungs-Proxys und lesen keine Antwortkörper; CSV-Ausgaben (`wc5w`) schützen formelartige Werte. Migration `0009` indexiert neue Verify-Tokens und rückfüllbare Galerietokens; `0017`/`0019` ergänzen indexierte Legacy-Fenster und signierte Fortsetzung statt unbeschränkter Arbeit je Anfrage. Der Nutzer verlangt ausdrücklich den Erhalt alter Links. Migration `0010` schützt die Erstinstallation mit `BOOTSTRAP_TOKEN`, Singleton und DB-Guard; `python -m app.cli.recover_admin` stellt bei leerer User-Tabelle lokal einen Admin wieder her. Pillow >=12.3 und Decoder-Allowlist JPEG/PNG/WebP (`9pzk`) weisen ungültige Bilder mit 422 zurück.

**Restore und Logging:** `myru` macht den Restore vollständiger eigener Archive in eine leere DB verbindlich: Preflight lehnt vorhandene User-Relationen in allen Schemas ab; Prüf-/SQL-Fehler liefern Exit 1. `psql` ignoriert Startup-Dateien und nutzt `ON_ERROR_STOP` plus eine einzelne Transaktion. Lokale Bilder werden vor SQL im Upload-Dateisystem gestaged und nach SQL-Commit pro Datei atomar ersetzt. Spätere Dateifehler melden ausdrücklich eine bereits wiederhergestellte DB mit unvollständigen Bildern; keine atomare DB-/Dateisystem-Transaktion. App beim Restore gestoppt halten. Image und CI nutzen PostgreSQL-16-Clients, passend zum Server; pg_dump 17 erzeugte im Test das auf Server 16 unbekannte `transaction_timeout`. `k00e` reaktiviert vor Alembic importierte `app.*`-Logger bei erneuter Logging-Konfiguration, ohne doppelte Uvicorn-Ausgaben.

**Galerieabschluss:** `nnvq`/`x8fp` verwenden `app.guest.service.lock_gallery_for_review`: gezieltes `FOR UPDATE` auf genau eine Galerie, danach separate frische Prüfung von Token, Owner-Status, Galerie-Status und Ablauf. Auswahl sperrt nach Lifecycle-Gallery/Image und Eventbudget; Gastabschluss nach geteilter Lifecycle-Gallery-Fence. Owner-Status-UPDATEs beider Oberflächen konkurrieren automatisch mit der Zeilensperre. Auswahl nach abgeschlossenem Review: 403; erneuter Abschluss mit gleicher, anderer oder frischer Sitzung: 409 vor Seiteneffekten. Nur der erfolgreiche `shared -> completed`-Übergang setzt die Session und stößt nach Commit E-Mail/Webhook an. Bewusstes Wiederöffnen und bestehende Auswahlhistorie bleiben erhalten; bereits abgeschlossene Sitzungen können weiterhin nicht erneut abschließen. Benachrichtigungen bleiben best effort ohne dauerhafte Queue/Crash-Recovery. Keine Schemaänderung.

**Gast-Sitzungen:** `vhrn` ergänzt Migration `0011`: `Gallery.authorization_epoch` und `ShareSession.authorization_epoch` als BIGINT NOT NULL DEFAULT 0. Bestehende Datensätze starten gemeinsam bei 0; spätere Passwort-/Freigabeänderungen erhöhen die Galerieversion atomar. Alle fünf Owner-Schreiber nutzen `lock_owned_gallery_authorization` nach Hashing (Lifecycle-Gallery shared -> Gallery FOR UPDATE -> frisch laden -> genau einmal erhöhen). Gastaufnahme verwendet Lifecycle-Gallery shared -> Admission-Advisory-Fence -> Gallery FOR UPDATE; nie einen bestehenden FOR-SHARE-Lock upgraden. Sensible Reads bleiben unter Lifecycle-Gallery shared -> Gallery FOR SHARE bis zur vollständigen Materialisierung; Commit vor Rückgabe. Passwortprüfung und Session-Stamp erfolgen unter derselben Aufnahmesperre. Bereits gültige Browser-Grants rendern geschützte GETs ohne erneute Aufnahme. `current_share_session` prüft ID, Galerie und Generation; nach Review-Lock erneut prüfen. Alte Sitzungen bleiben für Historie erhalten, alte Grants liefern 403 bei geschützten Reads/Auswahl bzw. 404 beim Abschluss. Passwortlose Reads bleiben per Token möglich; bewusstes Owner-Reopen erhöht die Generation nicht. Migration erhält alte Links, Sitzungen und Events. Bereits signierte Medien-URLs behalten 1h/15min TTL; keine sofortige Signatur-Revokation.

**Registrierungsfreigabe:** `13vn` ergänzt Migration `0012`: nullable `PendingSignup.email_verified_at`, ohne Default oder Backfill. Beide Bestätigungswege nutzen `consume_pending_signup`: bestehender Token-Lookup, gezieltes `FOR UPDATE`, frischer Verifier-Abgleich, tatsächlicher Bestätigungszeitpunkt und Tokenverbrauch in einem Commit. Beide Freigabe-/Ablehnungswege verwenden denselben Row-Lock im gemeinsamen Admin-Service. Freigabe ohne Nachweis: 409 ohne Kontenerstellung oder Löschung der Anfrage; mit Nachweis wird dessen Zeitpunkt unverändert kopiert. Ein gezieltes PostgreSQL-`ON CONFLICT` auf E-Mail erhält bestehende Konten auch bei parallelem Insert. Adminliste zeigt den Bestätigungsstatus und Freigabe nur für bestätigte Anfragen; Zähler enthalten weiterhin alle offenen Anfragen. Alte unverbrauchte Links bleiben gültig. Bereits verbrauchte Alttokens liefern keinen belastbaren historischen Nachweis: Anfrage ablehnen und erneut registrieren lassen. Bestehende aktive Benutzer bleiben unverändert.

**Arbeitsstand und Entwürfe:** Nach Abschluss dieses Doku-Syncs sind 302 Tickets geschlossen und neun Ideen als `deferred` mit `draft`/`needs-evaluation` zurückgestellt; keine Implementierung ist offen oder in Bearbeitung. Ursprünglich zehn Entwürfe wurden mit `1ceda91` exportiert; das Doku-Ticket `picture-stage-zqs0` wird durch diesen Nutzerauftrag erledigt. `bd ready` ist deshalb leer. Für die Entwürfe `bd list --status=deferred --label=draft` und anschließend `bd show <id>` verwenden. Jeder enthält Ausgangslage, Code-Einstieg, Bewertungsfragen, Risiken, Akzeptanzkriterien und Bezüge. Ein späterer Arbeitsauftrag bestimmt Bewertung und mögliche Umsetzung; der Draft-Status allein startet keine Arbeit.

| Entwurf | Thema |
|---------|-------|
| `picture-stage-73j3` | Fotografenansicht: Polling und Pagination |
| `picture-stage-j0tq` | Gemeinsame Dekodierung für Vorschauen bewerten |
| `picture-stage-69jy` | Nachvollziehbare gemeinsame Build-Abhängigkeiten |
| `picture-stage-p0na` | Build-Pfadfilter vervollständigen |
| `picture-stage-l0f6` | Browser-Regressionsprüfungen in CI |
| `picture-stage-v67u` | Vorschau-/Cleanup-Wiederanlauf |
| `picture-stage-ge4w` | Betriebsmetriken und optionale Readiness |
| `picture-stage-j61j` | Weitere Test-Redundanz und Warnungsquellen |
| `picture-stage-em1g` | Gast-Zähler vor möglichem Caching profilieren |

**Feststehende Produktentscheidungen:** Wasserzeichen nur auf der großen 1280px-Vorschau, 320/640px-Thumbnails bewusst unverändert. Logout sperrt sofort das verwendete JWT, andere Anmeldungen bleiben gültig. Neue Backups sind signiert; eigene alte unsignierte Archive benötigen ausdrücklich `--trust-legacy-unsigned`. Alle alten Galerie-/Verifikationslinks einschließlich Hash-only- und älterer Tokenformen erhalten. Sprach-Switch und Ende-des-UTC-Tages beim Ablaufdatum beibehalten. Das SMTP-Anmelde-Timing-Risiko `lt98` wurde ausdrücklich akzeptiert; kein weiterer Fix daraus beauftragt.

**Optimierungsrunde 2026-11-06:** `naxr`, `1nt8`, `ls1k`, `n8wi`, `95n5` und `a136` sind abgeschlossen. `app/galleries/dashboard.py` bündelt Besitzer-Summaries auf fünf HTML-/vier API-Abfragen (ohne Authentisierung), leere Liste eine, Admin-API plus eine; historische ever-positive Zähler bleiben von aktuellen Gast-/Exportzuständen getrennt. `app/galleries/management.py` vereinheitlicht Owner-Lookup, Freigabe-/Passwortmutationen und Statusregeln; Adapter behalten Hashing vor Locks, Commit und Antwortformat. Share-Fragmente laden/signieren keine Bilder mehr (mit 1/501 Bildern geprüft). Zwei tote interne Aufrufketten mit 68 Produktionszeilen wurden entfernt; Tests prüfen die lebenden Gastseiten-/Preview-Pfade. Mehrere Quellenprüfungen wurden durch Verhaltenstests ersetzt, vier unbenutzte Fixtures entfernt und zwei zufällige Bildreihenfolge-Annahmen in Tests korrigiert. Je Thema neuer Implementierungsagent und danach frischer unabhängiger Test-Agent.

**Messung und aktueller Gesamtnachweis:** Mit identischer Umgebung brauchten 78 ausgewählte Fälle vorher 251,83 und danach 198,24 Sekunden; elf weitere Fälle vorher 33,01 und danach 22,74 Sekunden. Die 78er-Läufe enthielten beide denselben anschließend behobenen Testfehler zur Bildreihenfolge; beide 11er-Läufe waren grün. Das sind 21–31 % für diese Auswahlen, keine garantierte Beschleunigung jeder Suite. Der letzte vollständige Quellcode-Lauf [42236256200](https://github.com/stoertebeker/picture-stage/actions/runs/42236256200) auf `c3554e6` bestand am 2026-11-06 mit 1.809 Tests, null übersprungenen Fällen, 368,19 Sekunden und 82 % Zeilenabdeckung. Ruff/Format (251 Dateien), mypy (87 Anwendungsmodule) und AMD64-/ARM64-Build/Publish erfolgreich; 81 Warnungen, weitere Einordnung in `j61j`. 82 % beschreibt im Lauf erreichte ausführbare Codezeilen, keine Fehlerfreiheitsgarantie. Lokal bestand der abschließende Unit-/Security-Lauf mit 1.079 Fällen; zusätzliche unabhängige DB-, Browserzustands-, Pixel- und Paritätsprüfungen stehen in den jeweiligen Ticketnotizen. JUnit wird von CI als `pytest-results` gespeichert.

**Image-Nachweis vom 2026-11-06:** `stoertebeker2k/picture-stage:dev` und `:sha-c3554e6` wurden für AMD64/ARM64 mit identischem OCI-Index `sha256:0070caaa41d620ac472d4d79711c977887d7777457d7ee93327ed3ddcb58e2b9` geprüft. Das ist ein datierter Nachweis. Dokumentations-/Beads-Commits können CI und Build über den Pfadfilter überspringen; die Release-Tags sind davon unabhängig.

**Historische Verifikation:** Prüfstände mit 895–980 Fällen sowie Head `0010`–`0012` stammen aus einzelnen früheren Fixes am 2026-11-05. Sie sind keine aktuellen Gesamtzahlen; Details verbleiben in Git-Historie und Ticketnotizen. Der nachfolgende Sammelauftrag ist separat datiert.

**Weitere Invarianten:** Nicht-Uploads sind standardmäßig vor CSRF/Parsing auf 1 MiB begrenzt; Default-Rate-Limits greifen pro Routen-Template und Clientadresse. Der Request zur Schlüsselbildung darf den tatsächlich verarbeiteten Scope nicht ändern. CSRF-/BFCache-Recovery verwendet einen sicheren GET ohne Replay eines wegen CSRF abgewiesenen POST. Lookup-Fortsetzungen dürfen ausschließlich ausdrücklich markierte, vor Seiteneffekten abgewiesene Anfragen wiederholen. Auswahlereignisse bleiben append-only; standardmäßig maximal 20.000 Ereignisse pro Galerie und 2.000 Unicode-Zeichen pro Kommentar, konfigurierbar. Bestehende Historie bleibt lesbar.

**Storage-Invarianten:** `app/galleries/lifecycle.py`: geteilte Galerie-Fence für Upload/Worker/Single-Delete, exklusive Galerie-Fence für Gallery/Bulk-Delete, exklusive Image-Fence für Worker/Single-Delete; vor Audit-/FK-Writes erwerben, IDs sortieren. Cleanup vereinigt gespeicherte und kanonische Keys. Fehler erhalten DB-Referenzen und liefern retrybares 503. `UploadCleanupIntent` wird vor Writes persistiert und beim erfolgreichen Bild-Commit atomar entfernt; Kompensation schützt bereits committete Bilder. `complete_upload` wartet laufende Writes vor Cancel-Cleanup ab. Galerie-/Userdelete wiederholt offene Intent-Keys. Kein Janitor/historischer Orphan-Sweep und kein Live-S3-/SIGKILL-Nachweis. Verlorene Commitbestätigung kann referenzierte Pending-Bilder ohne erneuten Preview-Dispatch hinterlassen.

Seit Migration `0013` reserviert jeder Speicher-Schreiber Originale, Previews und Kopien vor dem Schreiben im dauerhaften Owner-Ledger. Standardgrenzen: 10 GiB und 40.000 Objekte pro Owner. Accounting verwendet unabhängige kurzlebige Verbindungen, damit belegte Caller-Pools den Fortschritt nicht blockieren. Rückgabe erfolgt erst nach bestätigtem Löschen; unbekannte historische Größen verhindern weitere Zuteilung, ohne alte Referenzen zu löschen.

**Verifikation Sammelauftrag 2026-11-05:** Alle 14 Resttickets sind abgeschlossen; `teis` bestätigt ausdrücklich die gewünschte Wasserzeichen-Policy nur für 1280px, mit unveränderten 320-/640px-Thumbnails. Der vollständige CI-Lauf [42062967866](https://github.com/stoertebeker/picture-stage/actions/runs/42062967866) auf Quell-/Teststand `4924ea0` bestand mit 1.781 Tests, 80 % Coverage, Ruff, Formatprüfung und mypy (85 Quelldateien); der anschließende Build für amd64/arm64 war erfolgreich. Nativer lokaler App-Build, echte CAPTCHA-/Gast-Browserkontrollen und 18 JS-Zustandsszenarien sind ebenfalls grün. Neue Migrationen enden bei `0019`, das Schema umfasst 18 Tabellen. Unabhängige Voruntersuchung und genau ein frischer Kandidatenreview je Security-Fix; bestätigte Befunde wurden im selben Zyklus korrigiert und nachgeprüft. Nachweise und Abschlussbericht liegen in der targetPath-Artifact-Collection unter `artifacts/batch-20261105/`. Tests nutzten ausschließlich isolierte Testdatenbanken und temporäre Uploads; keine Live-S3-/SMTP-/Webhook-/SIGKILL-Ausführung behauptet.

**Lokale Verifikation:** Integrationsfixtures erzeugen/löschen das ORM-Schema pro Modul und leeren vor jedem Test alle ORM-Tabellen mit eigenem Commit; Triggerzustand wird zurückgesetzt. NullPool, unabhängige Sitzungen und echte konkurrierende Commits bleiben erhalten. Ein echter gecachter Hash wird nur für das feste unbenutzte Fixture-Passwort wiederverwendet; produktive bcrypt-Kosten bleiben unverändert. Migrationstests prüfen die echten Alembic-Wege separat. Ausschließlich dedizierte Wegwerf-Testdatenbank und eigenen Uploadpfad verwenden; niemals App-Daten/Volumes. `tests/conftest.py` erzwingt für Tests lokalen Memory-Rate-Limit-Storage und setzt Zähler zurück, ohne Enforcement pauschal abzuschalten. Maßgeblich sind Python 3.12/PostgreSQL 16 mit Node für die JS-Zustandsprüfungen; Host-Python 3.14 kann abweichen. Früher lokal verfügbare Chrome-/Playwright-Pfade sind keine garantierte Toolchain. Frisches `ASSET_VERSION` bei Browserkontrollen, echte vendorte JS-Dateien und frisch erzeugtes CSS verwenden. Getracktes styles.css ist ein generierter Tailwind-Snapshot; HTMX-/Alpine-Dateien sind Platzhalter, Fonts und CAPTCHA-Assets sind getrackt. Screenshots ausschließlich `.playwright/`, nach Sichtung sofort löschen. Keine Credentials in Doku übernehmen. Alle von den abgeschlossenen Aufträgen erzeugten Testcontainer, DB-Volumes, Netze und Browserprozesse sind bereinigt; normale App-Daten/Volumes wurden nicht berührt.

**Einstieg nach Wachwechsel:** `bd prime`, `bd ready`, `bd list --status=deferred --label=draft` und `bash scripts/verify-handover.sh`. Das Skript prüft Git-/Tool-/Dateizustand, führt aber keine Tests und keine Live-GitHub-/Registry-Prüfung aus. Aktuelle Anwendungseinstiege: `app/galleries/{dashboard.py,management.py}`, Gast-Pagination in `app/guest/service.py`, Build-/Frontend-Verträge in [docs/design/build.md](docs/design/build.md). Weitere Implementierung nach dem nächsten Nutzerauftrag; die aktuelle Übergabe ist abgeschlossen. Issue-Sync über `.beads/issues.jsonl` und Git; Memories ausschließlich lokal über `bd remember`, kein Dolt-Remote.
