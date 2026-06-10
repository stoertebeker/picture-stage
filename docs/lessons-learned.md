# Lessons Learned

Kuratierte Erkenntnisse aus der Entwicklung, die nicht im Code oder in Commit-Messages stecken.

## Frontend (v0.4)

### HttpOnly-Cookies sind per JavaScript nicht lesbar
**Kontext:** Gallery-Name inline editieren wollte den JWT aus dem Session-Cookie extrahieren und als Bearer-Token an die JSON-API senden (`document.cookie.split('session=')...`).
**Problem:** Der Session-Cookie hat `httponly=True` — JavaScript kann ihn nicht lesen. Der fetch-Call scheitert still.
**Loesung:** Fuer jede Aktion, die im Browser stattfindet, einen eigenen Frontend-Endpoint erstellen (HTMX-Form statt fetch+Bearer). Die JSON-API bleibt fuer programmatische Clients (Postman, CLI, Integrationen).
**Regel:** Keine Browser-Aktionen ueber die JSON-API routen, wenn Cookie-Auth aktiv ist.

### Subagenten koennen bei Session-Limits partial work hinterlassen
**Kontext:** 4 parallele Agenten fuer Wave 2 dispatcht. Alle 4 liefen in ein Session-Limit.
**Problem:** Die Agenten hatten bereits Dateien geschrieben (Router, Templates, Tests), konnten aber kein RESULT_START/RESULT_END zurueckliefern. Der Lead-Agent bekam nur "session limit hit" als Ergebnis.
**Loesung:** Nach Agent-Timeout/-Limit immer `git status` und die erzeugten Dateien pruefen. Fehlende Teile (Templates, main.py-Registrierung, Tests) manuell vervollstaendigen.
**Regel:** Agent-Ergebnisse nie blind vertrauen — immer den tatsaechlichen Datei-Stand verifizieren.

### Guest-Router vor API-Router registrieren
**Kontext:** Neuer Frontend-Guest-Router (`/g/{token}` fuer HTML) kollidiert mit bestehendem API-Guest-Router (`/g/{token}` fuer JSON).
**Loesung:** Frontend-Router in `main.py` VOR dem API-Router registrieren. Der Frontend-Router prueft via `_wants_html()` ob ein Browser-Request vorliegt (kein Authorization-Header, kein explizites `Accept: application/json`). Wenn nein, wirft er 406 und der API-Router uebernimmt.
**Regel:** Bei Route-Konflikten zwischen HTML- und JSON-Endpoints: Reihenfolge in main.py + Content-Negotiation.

## Architektur

### CSP `script-src 'self'` erzwingt Self-Hosting von JS
**Kontext:** Content-Security-Policy ist in `SecurityHeadersMiddleware` gesetzt mit `script-src 'self'`.
**Konsequenz:** Alle JavaScript-Libraries (HTMX, Alpine.js) muessen als `.min.js` unter `/static/js/` gevendort werden. CDN-Links werden vom Browser blockiert. Inline-`<script>`-Tags mit Code sind ebenfalls blockiert — nur `<script src="...">` funktioniert.
**Ausnahme:** `style-src 'self' 'unsafe-inline'` erlaubt Inline-Styles (fuer Tailwind/Alpine.js `:class` Bindings).

## v0.3 Produktion & Compliance

### `\s` im Regex-Character-Class matcht auch `\r\n`
**Kontext:** Header-Injection-Schutz für den Content-Disposition-Filename des CSV-Exports wurde gefixt mit `re.sub(r"[^\w\s-]", "", name).strip()`. Idee: nur Wortzeichen, Whitespace und Bindestrich erlauben.
**Problem:** `\s` matcht auch `\r` und `\n` — CRLF überlebt also in der Mitte des Strings, `.strip()` putzt nur die Enden. Header-Injection-Vektor blieb offen.
**Lösung:** Explizite Zeichenklasse `[^\w -]` (Wortzeichen, **einfaches Leerzeichen**, Bindestrich) — `\r\n\t` fallen raus.
**Regel:** Bei Sanitization für HTTP-Header niemals `\s` — immer explizit aufzählen, was erlaubt ist. Defense-in-Depth, auch wenn der ASGI-Server CRLF-Header oft selbst ablehnt.

### Async-SQLAlchemy: `expire_all` + Attribut-Zugriff = MissingGreenlet
**Kontext:** Integrationstests verifizierten Lösch-Effekte mit `db.expire_all()` gefolgt von `gallery.id`-Zugriff in der `db`-Session.
**Problem:** `expire_all()` markiert alle ORM-Objekte als expired. Der danach folgende `gallery.id`-Zugriff triggert einen Lazy-DB-Load — im sync Kontext, ohne Greenlet → `sqlalchemy.exc.MissingGreenlet`.
**Zweites Problem (verwandt):** `db.rollback()` als Ersatz ist ein No-op, wenn keine Transaktion offen ist — Identity-Map bleibt mit Stale-Reads bestückt.
**Lösung:** Für Post-Request-Verifikation **eine separate frische Session** öffnen (leere Identity-Map, frischer Snapshot). Plus IDs frühzeitig als Plain-UUID sichern, nicht über expired ORM-Attribute zugreifen.
**Regel:** In async-SQLAlchemy-Tests: Arrange-Session und Verify-Session **trennen**. Siehe `tests/integration/conftest.py` (`db` vs `verify_db`).

### Self-Hosted: Dependency vermeiden statt nachziehen
**Kontext:** Für die DSGVO-Markdown-Seiten war `markdown>=3.7` als Dependency vorgesehen.
**Problem:** Sandbox-Netzwerk blockierte den Install. Wichtiger: jede zusätzliche Dependency vergrößert die Supply-Chain-Fläche einer Self-Hosted App.
**Lösung:** ~30-Zeilen-Renderer in `app/frontend/legal.py` (`_minimal_md_to_html`) — Headings, Bold, Italic, Links, mit `html.escape` und `javascript:`/`data:`-URI-Filter.
**Regel:** Für statische Operator-kontrollierte Inhalte (Legal-Seiten, Onboarding-Texte) lieber Built-In statt Markdown-Library — kleinere Angriffsfläche, weniger CVE-Bewegung.

### Sandbox blockt localhost — DB-Tests laufen nur in CI
**Kontext:** Lokale Funktionstests gegen den laufenden Container schlugen fehl: `Immediate connect fail for 127.0.0.1: Operation not permitted`. Settings-Anpassung half nicht (managed-Policy überschreibt).
**Konsequenz:** Alle DB-gebundenen Tests (Galerie-CRUD, Lösch-Workflow, Audit-Log-Persistenz) sind **CI-only**. Lokal: In-Process via `TestClient` / `httpx.AsyncClient(ASGITransport(app))` ohne echtes TCP.
**Setup für CI-Integrationstests:** `tests/integration/conftest.py` mit `NullPool`-Engine (gegen `asyncpg`-Loop-Issues), per-Test `drop_all`/`create_all`, `dependency_overrides[get_db]`. `verify_db`-Fixture für stale-read-freie Verifikation.
**Regel:** Wenn ein Test eine DB braucht, gehört er nach `tests/integration/` — er läuft dort gegen den Postgres-Service-Container der CI.

## DB-Migrationen (2026-06-05)

### Tests mit `create_all` sind blind für Migrations-Drift
**Kontext:** `tests/integration/conftest.py` baut sein Schema per `Base.metadata.create_all` auf, nicht über die Alembic-Migration. Die App im Container migriert dagegen via Alembic.
**Problem:** Migration und ORM können beliebig auseinanderlaufen, ohne dass ein Test es merkt — die Tests sehen immer das frische ORM-Schema, nie das von der Migration erzeugte. Drei Drifts erreichten so die Produktion: falsche Tabellen-Reihenfolge (FK vor Ziel), `VARCHAR`-Spalten wo das ORM native ENUM-Types erwartet (`type "userstatus" does not exist` beim ersten INSERT), und ein redundanter `UniqueConstraint` zusätzlich zum unique Index auf `email`.
**Lösung:** Dedizierter Drift-Guard `tests/migrations/test_migration_drift.py` — fährt die echte Migration (`command.upgrade("head")`) gegen die CI-Postgres und difft das Ergebnis per `alembic.autogenerate.compare_metadata` gegen `Base.metadata`. Nur `modify_default` wird toleriert (einige Spalten tragen einen DB-seitigen Default, den das ORM Python-seitig setzt).
**Regel:** Wenn Tests das Schema per `create_all` aufbauen, brauchst du einen separaten Test, der die Migration tatsächlich ausführt und gegen das ORM vergleicht — sonst ist die Migration ungetestet.

### Handgeschriebene Migrationen driften vom ORM — strukturell prüfen
**Kontext:** Migration `0001` war handgepflegt, um das ORM-Schema „nachzubauen". Sie wich in dieser Session viermal ab (Reihenfolge, stamped-but-empty, ENUMs, unique-Index-vs-Constraint).
**Nicht-offensichtlich:** `mapped_column(unique=True, index=True)` erzeugt einen **einzelnen unique Index** und **keinen** separaten `UniqueConstraint`. Ein `Enum(StrEnum)` erzeugt einen **nativen PG-ENUM-Type** (Name = lowercase Klassenname), den die Migration explizit anlegen/droppen muss — `create_all` macht das automatisch, eine VARCHAR-Migration nicht.
**Lösung:** Migrations-Runner auf das offizielle Alembic-Async-Rezept umgestellt (`command.upgrade` über geteilte Connection via `cfg.attributes["connection"]`, `engine.connect()` damit `env.py` die Transaktion besitzt). Damit ist auch der „stamped-but-empty"-Zustand strukturell unmöglich: Alembic besitzt Versionstabelle und Migrations-Transaktion gemeinsam.
**Regel:** Eine handgeschriebene Migration, die ein ORM spiegeln soll, ist eine Drift-Quelle. Entweder per `--autogenerate` erzeugen oder per `compare_metadata`-Test absichern (siehe oben). Self-rolled Migrations-Runner mit manuellem Stamping vermeiden — `alembic.command` nutzen.

## Admin-User-Verwaltung (2026-06-07)

### CI prüft `ruff format --check` UND `ruff check` — lokal beides laufen lassen
**Kontext:** Lokal nur `ruff check .` (Linter) geprüft und committet. Die CI-Stufe `ruff format --check .` schlug danach fehl (`Would reformat: ...`) — mehrfach in Folge.
**Problem:** `ruff check` (Linter, Regeln/Imports) und `ruff format` (Formatter, Zeilenumbrüche) sind getrennte Werkzeuge. Grüner Linter heißt nicht formatkonform.
**Lösung/Regel:** Verifikations-Standard = `ruff format --check . && ruff check . && mypy app/ && pytest tests/unit/ -q`. Bei rotem Format-Check `ruff format <dateien>` anwenden.

### Async-SQLAlchemy: `db.delete(obj)` lazy-lädt Cascade-Relationships → `MissingGreenlet`
**Kontext:** Admin-User-Delete sollte einen User samt Galerien/Notifications löschen. `await db.delete(user)` triggert ORM-Cascade über `relationship(cascade="all, delete-orphan")`.
**Problem:** Sind die Collections (`galleries`, `notification_configs`) nicht eager geladen, lazy-lädt das ORM sie beim Flush — im async-Kontext → `MissingGreenlet`. Eager laden + parallele Core-Deletes (für Storage) kollidieren dagegen (Stale/StaleData).
**Lösung:** Storage-relevante Kinder (Galerien) zuerst explizit purgen (Files + Core-`delete()`), dann den User per **Core-`delete(User).where(...)`** entfernen. Die übrigen Abhängigen (`notification_configs` → `deliveries`) räumt die **DB-seitige `ON DELETE CASCADE`** ab — kein ORM-Lazy-Load nötig.
**Regel:** In async destruktive Multi-Table-Löschungen über Core-Statements + DB-`ondelete=CASCADE` fahren, nicht über `db.delete(orm_obj)` mit lazy Cascade.

### DB-Cascade löscht keine Storage-Dateien — User-Delete muss storage-aware sein
**Kontext:** `galleries.owner_id` / `images.gallery_id` haben `ondelete=CASCADE`. Ein User-Delete entfernt damit alle DB-Rows automatisch.
**Problem:** Die physischen Dateien (WebP-Previews, Originale in Local/S3-Storage) kennt die DB nicht — sie verwaisen. Bei einem gelöschten Account ist das nicht nur ein Leak, sondern ein DSGVO-Problem (Bilddaten bleiben liegen).
**Lösung:** Gemeinsame `purge_gallery(gallery, db, storage)` (`app/galleries/deletion.py`), die Gallery-Delete und User-Delete teilen — löscht Storage-Files best-effort + DB-Rows in FK-Reihenfolge + anonymisiert Audit-Log.
**Regel:** Jeder Lösch-Pfad, der indirekt Bilder entfernt (User, Bulk), muss explizit über den Storage-Backend gehen — DB-Cascade allein reicht nie.

### Native PG-ENUM erweitern: `ALTER TYPE ... ADD VALUE` im autocommit_block
**Kontext:** `UserStatus` (StrEnum) ist als nativer PG-Type `userstatus` gemappt. Neuer Wert `disabled` nötig.
**Lösung:** Migration `0002`: `op.execute("ALTER TYPE userstatus ADD VALUE IF NOT EXISTS 'disabled'")` innerhalb `with op.get_context().autocommit_block():` (ADD VALUE verträgt keinen offenen Transaktionsblock auf älteren PG). Downgrade ist ein No-op (PG kann Enum-Werte nicht in-place entfernen).
**Regel:** Enum-Werte ergänzen ≠ Spalte ändern — eigener `ALTER TYPE`-Schritt, idempotent (`IF NOT EXISTS`), autocommit, kein Downgrade-Drop.

## Frontend-Build & Prod-Verifikation (2026-06-08)

### Eingechecktes `frontend/static/css/styles.css` ist nur ein Stub — echtes CSS entsteht im Docker-Build
**Kontext:** Bei qdz.14 (Lightbox-Redesign) wollte ich die neuen Token-Klassen lokal visuell verifizieren. Viele Klassen (`bg-accent/15`, `from-black/80`, `tabular-nums`) fehlten im committeten `styles.css` — Verdacht auf Fehler.
**Problem:** Die ins Repo eingecheckte `styles.css` ist ein **Sicherheits-Stub** (Stand zuletzt PS-UX-03/Fonts). Der echte Tailwind-Build läuft erst in der Docker-Stage `css-builder` (`Dockerfile`), die `app/templates/**` + `frontend/static/spikes/*.html` scannt. Lokal ist kein `npx tailwindcss` möglich (Sandbox blockt npm-Registry). Dazu sind `alpine.min.js` und `htmx.min.js` lokal ebenfalls nur Stubs (echte Bundles kommen aus der `assets`-Docker-Stage). Siehe `docs/design/build.md`.
**Lösung/Regel:** Lokale visuelle Verifikation einer neuen Frontend-Komponente ist NICHT aussagekräftig — fehlende Klassen lokal heißen nicht „kaputt". Token-Treue per Source-Test absichern (keine Roh-Farben, Klassen aus `tailwind.config.js`), visuelle Abnahme gegen den abgenommenen Spike ODER live auf Prod (dort läuft das gebaute CSS). Prod ist via Playwright-Tools erreichbar (`https://picture.stoertes.cloud`).

### `text-inverse`-Token ist theme-invertiert — für Controls auf der Bühne `text-primary` nehmen
**Kontext:** Im Lightbox-Spike hatte ich Steuerelemente (Counter, Close, Pfeile) mit `text-text-inverse/70` gestylt. Sah im Spike richtig aus, weil der Spike-`<style>`-Block `text-inverse` hart als Weiß übersteuerte.
**Problem:** `--color-text-inverse` ist bewusst theme-INVERTIERT: dark = `#09090b` (fast schwarz), light = `#fafafa` (weiß). Auf der schwarzen Lightbox-Bühne (`surface-sunken` dark = `#000`) wäre `text-inverse` also schwarz-auf-schwarz = unsichtbar.
**Lösung/Regel:** Für lesbare Controls auf `surface-sunken` (Bühne) `text-primary` nehmen — schwingt korrekt mit (dark=weiß, light=dunkel). `text-inverse` nur für Text AUF einer Akzentfläche (z.B. `text-on-accent` auf `bg-accent`). Theme-aware Scrim = `bg-surface-overlay` + `backdrop-blur`, NICHT harter `from-black`-Gradient (im Light-Mode falsch).

## Async-Verarbeitung & BackgroundTasks (2026-06-08)

### FastAPI-BackgroundTasks brauchen eine EIGENE DB-Session — die Request-Session ist tot
**Kontext:** Bei o4d (async Multi-Upload) wurde die Preview-Generierung aus dem Upload-Request in einen `BackgroundTasks`-Worker verlagert, damit der Request sofort zurückkehrt.
**Problem:** Der Worker läuft NACH dem Response. Die per `Depends(get_db)` injizierte Request-Session ist da bereits geschlossen — ihre Wiederverwendung im Task wirft Fehler oder liefert stale Daten. Auch die Storage-Bytes des Uploads nicht im RAM festhalten (bei großen Batches Speicher-Druck).
**Lösung/Regel:** Der Worker öffnet eine frische Session via `async_session()` (`app/db/base.py`), nicht die Request-Session. Pillow blockiert → in `asyncio.to_thread()` wrappen, sonst friert der Event-Loop für ALLE Requests ein. Original aus dem Storage zurücklesen (`download_stream`) statt Bytes durchzureichen. Fehler-Status (`failed`) in einer SEPARATEN Session/Transaktion setzen, damit der Status-Write den Rollback der Haupt-Transaktion überlebt. Tenant-Isolation: Bild per `(image_id, gallery_id)` laden, nie nur per ID. Siehe `app/images/preview_worker.py`.

### Neue NOT-NULL-Enum-Spalte ohne Massen-Reprocessing der Bestandsdaten
**Kontext:** Migration 0004 fügte `images.processing_status` (`pending`/`ready`/`failed`) hinzu. Bestandsbilder haben bereits Previews — sie dürfen nicht als „pending" erscheinen und erneut verarbeitet werden.
**Problem:** Eine NOT-NULL-Spalte mit ORM-Default `pending` würde alle Altzeilen auf `pending` setzen → das Grid pollt endlos und der Worker würde theoretisch alles neu rendern.
**Lösung/Regel:** Spalte mit transientem `server_default="ready"` anlegen (Backfill der Altzeilen), direkt danach `server_default` wieder droppen. Neue Inserts kommen dann über den ORM-Default (`pending`). Gleiches Muster wie der NULL-Default bei `tokens_valid_after` (0003): Migrations-Defaults steuern Bestandsdaten, ORM-Defaults steuern Neuzugänge — die beiden bewusst entkoppeln. Native PG-ENUM weiterhin explizit `create_type=False` + `.create(bind, checkfirst=True)` (Muster aus 0001).

## CI/CD-Pipeline (2026-06-08)

### Ein „grüner" Run kann ein übersprungener Run sein — Job-Liste prüfen, nicht nur den Gesamtstatus
**Kontext:** Ein reiner Test-Fix-Commit (`030b327`, nur `tests/**` geändert) meldete in GitHub Actions „success" — in 8 Sekunden.
**Problem:** Der `ci`-Job (lint + mypy + pytest) wurde wegen des Path-Filters (`dorny/paths-filter`) **übersprungen**, weil `tests/**` nicht in der `build`-Filterliste stand. Übersprungene Jobs zählen als Erfolg → der Gesamt-Run ist grün, obwohl pytest nie lief. Der Test-Fix war damit unverifiziert, der „Erfolg" trügerisch.
**Lösung/Regel:** Bei verdächtig kurzen Läufen (Sekunden statt Minuten) immer `gh run view <id>` öffnen und die Job-Liste auf `- <job> in 0s` (= skipped) prüfen, statt dem grünen Gesamtstatus zu trauen. Und: Der Path-Filter, der CI gated, **muss `tests/**` enthalten** — ein Test-Commit, der die Tests nicht laufen lässt, ist schlimmer als kein Commit. `ci.yml` hat bewusst keinen `push`-Trigger (liefe sonst doppelt), nur `pull_request` + `workflow_call` → ein Test-only-Push auf `main` wird ausschließlich über den Filter in `docker-publish.yml` getriggert.

### `EmailStr` lehnt `.local`/`.test` als reservierte Namen ab — Integration-Tests brauchen echte Domains
**Kontext:** Die Signup-Enumeration-Integration-Tests (`42q`) posteten `@test.local`-Adressen an `POST /api/v1/auth/signup`. In CI: 3× `assert 422 == 201`.
**Problem:** `SignupRequest.email` ist ein Pydantic `EmailStr` (via `email-validator`), das reservierte TLDs wie `.local`/`.test` zurückweist → die Request-Validierung wirft 422, **bevor** der Handler läuft. Die DB-Insert-basierten Fixtures (`make_user`) gehen direkt an `EmailStr` vorbei, deshalb fiel es lokal/in Unit-Tests (die `@example.com` nutzten) nicht auf — nur der echte HTTP-Flow stolperte.
**Lösung/Regel:** In HTTP-Tests, die durch `EmailStr` gehen, **gültige Domains** (`@example.com`) verwenden, nie `@test.local`/`@foo.test`. Wenn ein „negativer" Test (z.B. zu kurzes Passwort → 422) zufällig grün ist, prüfen, ob der erwartete Statuscode wirklich aus dem getesteten Grund kommt und nicht aus einem vorgelagerten Validierungsfehler.

## CSP-Hardening & Deploy-/Cache-Schichten (2026-06-10, u3s)

### Library-Doku (context7/main) gilt evtl. NICHT für die gepinnte Version
**Kontext:** Bei der `@alpinejs/csp`-Migration zeigte die context7-Doku, dass Ternäre/Arithmetik/Objekt-Literale im CSP-Build unterstützt sind. Ich stufte alle `:class="x ? a : b"`-Ausdrücke als CSP-konform ein. Live auf Prod warf der CSP-Build aber „Alpine is unable to interpret … CSP-friendly build" für genau diese Ausdrücke.
**Problem:** Die context7/GitHub-`main`-Doku beschreibt den **neuen** CSP-Parser (ab 3.15). Wir pinnten **3.14.8**, dessen Parser nur Property-/Methoden-Zugriff konnte. Der Doku-Stand und der Release-Stand klafften auseinander.
**Lösung/Regel:** Doku-Aussagen gegen die **konkret gepinnte Version** prüfen, nicht gegen `main`. Konkret ging das per `gh api ".../contents/<datei>?ref=v<VERSION>"` — die `csp.md` in `v3.14.8` hatte die „What's Supported"-Sektion noch gar nicht, in `v3.15.12` schon. Fix war ein Versions-Upgrade (3.14.8 → 3.15.12), kein Auslagern von ~20 Ausdrücken.

### GHA-Layer-Cache bustet NICHT zuverlässig bei reiner ARG-Default-Änderung
**Kontext:** Das Versions-Upgrade `ARG ALPINE_VERSION=3.14.8` → `3.15.12` wurde gebaut & deployt — Prod zeigte trotzdem das alte 3.14.8-Verhalten.
**Problem:** Mit `cache-from/to: type=gha` hat BuildKit den `assets`-Stage-curl-Layer aus dem Cache wiederverwendet (Build-Log: `assets 4/5` = `CACHED`), obwohl der ARG-Wert sich änderte. Der RUN-Befehl-**Text** (`curl …@alpinejs/csp@${ALPINE_VERSION}/…`) blieb identisch → derselbe Cache-Key → 3.14.8 wurde erneut „geliefert".
**Lösung/Regel:** Asset-Versionen, die den Cache busten sollen, **inline in den RUN-Text** schreiben (`@alpinejs/csp@3.15.12/…`), nicht über ein ARG-Default. So ändert ein Versions-Bump den Layer-Hash und erzwingt einen echten Re-Download. Verifizieren: im Build-Log muss der curl-Step `DONE Xs` zeigen, **nicht** `CACHED`. Build-Logs liest man aus der Sandbox mit `XDG_CACHE_HOME="$TMPDIR" gh run view --job=<id> --log` (gh schreibt sonst nach `~/.cache`).

### Ein „Deploy" ohne `docker compose pull` läuft auf dem alten Image weiter
**Kontext:** Nach mehreren „deploys" lief Prod weiter auf einem tagealten Image — `window.Alpine.version` = `3.14.8`, und die `last-modified` der ausgelieferten `alpine.min.js` war über alle Deploys hinweg eingefroren auf die Bauzeit des allerersten Images.
**Problem:** `docker compose up -d` zieht ein bereits lokal vorhandenes Tag (`:dev`) **nicht** neu. Das Registry-`:dev` wird bei jedem Build überschrieben, der Server kennt das aber nicht — der alte Container lief einfach weiter.
**Lösung/Regel:** Deploy-Reihenfolge: `docker compose pull` **vor** `up -d`. Danach IM Container verifizieren, was wirklich drin liegt, bevor man weiter debuggt: `docker compose exec <svc> grep -o 'version:"[^"]*"' /app/frontend/static/js/alpine.min.js`. Die ausgelieferte `last-modified` ist ein guter „lebt das Image überhaupt?"-Indikator — friert sie über Deploys ein, wurde kein neues Image gezogen.

### Cache-Bugs von innen nach außen jagen: Build → Origin → CDN
**Kontext:** Derselbe Symptom-Stack (alte Alpine-Version) hatte nacheinander DREI Ursachen: GHA-Layer-Cache, nicht-gezogenes Origin-Image, Cloudflare-Edge-Cache (`cf-cache-status: HIT`, `max-age=14400` = 4h auf `/static/js/*`).
**Problem:** Wir haben mehrfach die äußerste Schicht (Cloudflare-Purge) behandelt, während die inneren (Build-Cache, Origin-Image) noch alt waren — jeder Purge holte sich prompt wieder die alte Datei vom alten Origin.
**Lösung/Regel:** Schichten **von innen nach außen** verifizieren: (1) Build-Log — lief der Schritt wirklich? (2) Origin/Container — `exec … grep version`. (3) CDN — `cf-cache-status`/`last-modified`/`age` am Response-Header. Erst wenn (1) und (2) den neuen Stand zeigen, ist ein CDN-Purge sinnvoll. Statische Assets ohne Versions-Hash im Namen sind hierfür eine Dauerfalle → Cache-Busting als Härtung (`picture-stage-d33`).

### Cache-Busting umgesetzt: `?v=<build>` statt Datei-Hash (2026-06-10, d33)
**Entscheidung:** Statt echtem Content-Hashing im Dateinamen (`alpine.<hash>.min.js`, bräuchte Build-Manifest + Bundler) werden JS/CSS-Assets per **Query-String** `?v=<ASSET_VERSION>` ausgeliefert. Zentraler Jinja-Helper `asset()` (`app/frontend/deps.py`), `ASSET_VERSION` im CI-Build auf den Zeitstempel gesetzt (`Dockerfile` ARG → ENV → Setting). Pragmatischer als ein Manifest, deckt alle drei Schichten + Browser-Cache, kein neuer Build-Schritt nach dem CSP-Build-Kampf.
**Fallstricke:** (1) **Fonts NICHT versionieren** — ein `?v=` am `<link rel=preload>`, das nicht exakt zur `url()` im CSS passt, lädt die Font doppelt (Preload-Mismatch-Warning). Nur Assets busten, die sich pro Build ändern. (2) **Cloudflare-Caching-Level „Standard"** — bei „Ignore Query String" wäre der `?v=`-Bust am Edge wirkungslos. (3) **Build-Timestamp statt Git-SHA** als Token: vermeidet, den exakten Commit öffentlich ins HTML zu schreiben (Info-Disclosure). Deploy-Runbook im README-Abschnitt „Update auf eine neue Version".
