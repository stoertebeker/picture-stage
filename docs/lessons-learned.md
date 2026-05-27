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
