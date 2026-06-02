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
