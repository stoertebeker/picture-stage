# Frontend-Assets bauen

Stand: 2026-11-09. Maßgeblich sind der [Dockerfile](../../Dockerfile),
[tailwind.config.js](../../tailwind.config.js) und die verlinkten Laufzeitdateien.
Das Frontend hat kein eigenes `package.json` und keinen npm-Lock im Repository.

## Build-Stages und Artefakte

| Stage | Aufgabe und Ergebnis |
| --- | --- |
| `builder` | Python 3.12: `pip install .` installiert das Projekt nach `/opt/venv`. `pyproject.toml`, `README.md` und `app/` werden zuvor kopiert. |
| `assets` | Alpine Linux 3.20 mit curl: HTMX **2.0.4**, **`@alpinejs/csp` 3.15.12** nach `/vendor/js/`; Fraunces/Inter WOFF2 nach `/vendor/fonts/`. |
| `css-builder` | Node 20 Alpine auf `$BUILDPLATFORM`: installiert Tailwind **3.4.17** und schreibt `/css/styles.css`. |
| `postgres-client` | Liefert PostgreSQL-16-Clientprogramme aus `postgres:16-trixie`. |
| Finale Stage | Python 3.12 Slim Trixie: kopiert Repository und Venv, überschreibt CSS/HTMX/Alpine/Fonts mit den Stage-Ergebnissen; läuft als Benutzer `app`. |

Die CSS-Stage kopiert `input.css`, Tailwind-Konfiguration, `app/templates/` und
`frontend/static/spikes/`. Tailwind scannt die beiden letztgenannten HTML-Pfade.
`--platform=$BUILDPLATFORM` vermeidet Node-Ausführung unter ARM64-QEMU;
das erzeugte CSS ist architekturunabhängig. JavaScript-Dateien sind derzeit
kein Tailwind-Content-Pfad.

Das lokale Image lässt sich aus dem Repository-Wurzelverzeichnis bauen:

```bash
docker build --build-arg ASSET_VERSION="$(date -u +%Y%m%d%H%M%S)" \
  -t picture-stage:local .
```

Änderungen an Templates, Tailwind-Konfiguration oder `input.css` erfordern
neues CSS. Die einzelnen Frontend-Stages lassen sich ohne App-Start bauen:

```bash
docker build --target assets -t picture-stage-assets:local .
docker build --target css-builder -t picture-stage-css:local .
```

Für einen Host-basierten Entwicklungsserver können die Ergebnisse dieser
beiden Images kopiert werden. Das überschreibt die lokalen Asset-Dateien:

```bash
(
  set -eu
  assets_container=$(docker create picture-stage-assets:local)
  trap 'docker rm -f "$assets_container" >/dev/null' EXIT
  docker cp "$assets_container:/vendor/js/." frontend/static/js/
  docker cp "$assets_container:/vendor/fonts/." frontend/static/fonts/
)
(
  set -eu
  css_container=$(docker create picture-stage-css:local)
  trap 'docker rm -f "$css_container" >/dev/null' EXIT
  docker cp "$css_container:/css/styles.css" frontend/static/css/styles.css
)
```

Die Container werden nur zum Kopieren angelegt und nicht gestartet. Generierte
Änderungen vor einem Commit prüfen; die Bundles werden beim Image-Build erneut
geladen. Das ist kein vollständig reproduzierbarer Dependency-Lock.

## Was eingecheckt ist

- `frontend/static/css/input.css` enthält Design-Tokens und Tailwind-Direktiven;
  `styles.css` ist bereits generiertes CSS, **kein Stub**. Der Docker-Build
  regeneriert es aus den aktuellen Eingaben.
- `frontend/static/js/htmx.min.js` und `alpine.min.js` sind **Platzhalter** mit
  `0.0.0-placeholder`; sie ersetzen die echten Bibliotheken nicht. Erst die
  `assets`-Stage liefert die vollständigen Bundles.
- Beide WOFF2-Dateien unter `frontend/static/fonts/` sind versioniert. Die
  `assets`-Stage lädt sie trotzdem neu von Fontsource mit `@latest`.
- `app.js`, `components.js`, `lookup-retry.js` und `signup-captcha.js` sind
  echte Anwendungsskripte und werden mit dem Repository ins Image kopiert.
- `frontend/static/vendor/altcha-3.5.0/` enthält echte CAPTCHA-Dateien: externes
  JS/CSS, den PBKDF2-Worker, deutsche Übersetzung, Lizenz und `integrity.json`.
  [signup-captcha.js](../../frontend/static/js/signup-captcha.js) lädt Module
  und Worker vom eigenen Ursprung; diese Dateien kommen nicht aus `assets`.

Die Download-Versionen stehen im Dockerfile; die Alpine-Version ist absichtlich
im `RUN`-URL-Text fixiert. HTMX/Alpine werden über HTTPS von unpkg/jsDelivr
bezogen, derzeit ohne separate Hash-Prüfung. Fontsource verwendet `@latest`;
auch Python-Abhängigkeiten und Basisimage-Tags sind nicht vollständig gesperrt.
Das versionierte `uv.lock` wird weder vom Dockerfile noch von CI verwendet.

[.dockerignore](../../.dockerignore) schließt Markdown generell aus, nimmt aber
**`README.md` ausdrücklich wieder auf**. Das ist für `COPY pyproject.toml
README.md ./` und die Paket-Metadaten nötig. `docs/`, Tests, Git-/Beads-Daten
und lokale Umgebungen sind vom Image-Kontext ausgeschlossen.

## Script-Reihenfolge und Alpine-Komponenten

[base.html](../../app/templates/base.html) lädt am Body-Ende nacheinander
`htmx.min.js`, `components.js`, `alpine.min.js`, `app.js`.
[guest_base.html](../../app/templates/guest_base.html) lädt davor zusätzlich
`lookup-retry.js`. `components.js` registriert im `alpine:init`-Listener:
`uploadZone`, `guestViewer`, `galleryManager`, `langSwitcher`, `auditFilter`,
`shareUrl`, `copyButton` und `settingsMenu` über `Alpine.data()`.

Komponentenlogik gehört nach
[components.js](../../frontend/static/js/components.js). Initialwerte stehen
als escaped `data-*`-Attribute am Wurzelelement und werden in `init()` über
`this.$root.dataset` gelesen, beispielsweise `x-data="shareUrl"` mit
`data-share-url="{{ share_url }}"`. Neue Komponenten ebenfalls vor Alpine-Start
registrieren; keine globale `window.myComponent`-Factory voraussetzen.
Siehe [Alpine.data](https://alpinejs.dev/globals/alpine-data) und den
[Alpine-CSP-Build](https://alpinejs.dev/advanced/csp).

## CSP und HTMX-Ereignisse

[SecurityHeadersMiddleware](../../app/security/middleware.py) setzt
**`script-src 'self'`**, ohne `unsafe-eval` oder `unsafe-inline` für Skripte.
Fonts fallen unter `default-src 'self'`; Styles erlauben separat
`style-src 'self' 'unsafe-inline'`.

Alpine-Attribute nutzen den CSP-Parser. Zustandszugriff und Methodenaufrufe
bleiben in den Templates; komplexe Logik und Browser-APIs stehen im externen JS.
Native `onclick`-Handler, Inline-`<script>` und auswertbarer `hx-on`-Code sind
kein geeignetes Muster für diese CSP. Für HTMX-Hooks verwendet die App externe
Listener; die [HTMX-Dokumentation](https://htmx.org/attributes/hx-on/) beschreibt
`hx-on` als eingebetteten Skriptcode.

[app.js](../../frontend/static/js/app.js) verdrahtet unter anderem:

| Ereignis / Attribut | Verhalten |
| --- | --- |
| `htmx:configRequest` | Aktualisiert Owner-CSRF für gleichursprüngliche Requests. |
| `htmx:afterSwap` | Synchronisiert Owner-CSRF, Theme-Schalter und automatisch geöffnete Dialoge. |
| `htmx:afterRequest` | Schließt/resetet bei Erfolg Elemente mit `data-close-dialog-on-success`; optional `data-remove-on-success`. |
| `htmx:xhr:progress` | Aktualisiert `#upload-progress`. |
| `data-open-dialog`, `data-close-dialog`, `data-backdrop-close` | Delegierte Klick-Listener steuern Dialoge. |

`uploadZone` und `guestViewer` registrieren außerdem eigene Request-/Swap-Hooks
und entfernen sie in `destroy()`. Details stehen im Komponenten-Code und in der
[HTMX-Ereignisreferenz](https://htmx.org/events/).
HTMX-Ziele müssen auch im Empty-State existieren. Nicht absendende Buttons in
Forms brauchen `type="button"`; bestehende Komponenten verwenden
`form.requestSubmit()` für programmatisches Absenden.

## Owner-CSRF und Gastzugang

Owner-Forms verwenden den lesbaren `csrf_token`-Cookie, `X-CSRF-Token` und
Hidden-Inputs. Bei angemeldeten Benutzern bindet der Server den Token an die
Session. `base.html` liefert einen initialen `hx-headers`-Wert; `app.js` liest
vor gleichursprünglichen HTMX-Anfragen und nativen Submits den aktuellen Cookie
und synchronisiert Header, Meta-Tag und Inputs. Ein eingefrorener Render-Wert
allein reicht nach Sessionwechseln nicht aus.

Die Middleware liest als Alternative zum Header nur URL-encoded Form-Bodies.
Für Multipart-Uploads ist deshalb der Header nötig. HTMX-CSRF-Fehler liefern
einen `HX-Redirect` auf eine bekannte GET-Seite; BFCache-Wiederherstellung nutzt
ebenfalls einen GET zur Wiederherstellung des aktuellen Owner-Zustands.

`/g/` ist von dieser **Owner-Middleware** ausgenommen, hat aber eigenen Schutz:
[guest/credentials.py](../../app/guest/credentials.py) hält Browser-Bearer in
HttpOnly-Cookies. Cookie-basierte Auswahl/Abschluss senden den separaten
`X-Guest-CSRF-Token`; Passwort-Forms nutzen `guest_csrf_token` mit Gate-Cookie.
Ein Share-Link allein ersetzt diese Nachweise nicht. Explizite API-Credentials
verwenden einen eigenen Pfad ohne Cookie-Fallback.

## CI und weitere Planung

[ci.yml](../../.github/workflows/ci.yml) installiert unter Python 3.12 mit
`pip install -e ".[dev]"` und prüft Ruff, Format, mypy und pytest gegen eine
isolierte PostgreSQL-16-Testdatenbank.
[docker-publish.yml](../../.github/workflows/docker-publish.yml) entscheidet
auf `main` per Pfadfilter über CI/Build; `v*`-Tags bauen immer. Nach erfolgreicher
CI werden AMD64/ARM64-Images gebaut und publiziert. `ASSET_VERSION` erhält dabei
einen Zeitstempel; `asset()` hängt ihn an JS-/CSS-URLs, Fonts bleiben unversioniert.
`README.md`, `tailwind.config.js`, `.dockerignore` und `alembic.ini` stehen derzeit
nicht im Build-Pfadfilter. Die Browser-Skripte unter `tests/e2e/` werden von
`ci.yml` nicht gesondert ausgeführt.

Weitere Bewertung läuft in Beads: `picture-stage-69jy` (Dependency-Locks und
CI-/Runtime-Parität), `picture-stage-p0na` (Build-Pfadfilter) und
`picture-stage-l0f6` (Browser-CI). Diese Entwürfe sind nicht umgesetzt.
