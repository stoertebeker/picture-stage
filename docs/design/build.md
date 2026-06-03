# Frontend-Assets bauen

Picture-Stage hat ein dünnes Frontend-Asset-Setup: keine Node-Toolchain im Repo,
kein npm-Lock. Alle Build-Schritte laufen **im Docker-Image** (Multi-Stage):

- **Stage `assets`** (Alpine + curl) – lädt HTMX 2.0.4, Alpine.js 3.14.8 (CSP-Build)
  und die Variable-Fonts Fraunces + Inter (Fontsource WOFF2).
- **Stage `css-builder`** (Node 20 Alpine) – `npx tailwindcss` baut
  `frontend/static/css/styles.css` aus `tailwind.config.js` + `input.css`.
- **Stage 3 (final)** – Python-Slim mit App; übernimmt alle Asset-Artefakte
  per `COPY --from=…`.

## Schneller Weg (Docker)

```bash
docker compose build --pull
docker compose up -d
# → http://localhost:8000
```

Bei Änderungen an `tailwind.config.js`, `input.css` oder an Templates muss das
Image neu gebaut werden, damit `styles.css` regeneriert wird:

```bash
docker compose build app && docker compose up -d --force-recreate app
```

## Lokal ohne Docker bauen

Die ins Repo eingecheckte `frontend/static/css/styles.css` ist nur ein
Sicherheits-Stub (siehe Kommentar in der Datei). Für ein vollständiges Frontend
ohne Docker brauchst du die Tailwind-CLI. Schnellweg via Docker-Run als
Einmal-Builder:

```bash
docker run --rm -v "$PWD":/work -w /work node:20-alpine sh -c "\
  npm install --no-save tailwindcss@3.4.17 && \
  npx tailwindcss -c tailwind.config.js \
    -i ./frontend/static/css/input.css \
    -o ./frontend/static/css/styles.css --minify"
```

HTMX, Alpine.js und die Fonts kannst du analog herunterladen (Versionen siehe
`Dockerfile`, Stage `assets`):

```bash
curl -fsSL -o frontend/static/js/htmx.min.js \
  "https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js"
curl -fsSL -o frontend/static/js/alpine.min.js \
  "https://cdn.jsdelivr.net/npm/@alpinejs/csp@3.14.8/dist/cdn.min.js"
mkdir -p frontend/static/fonts
curl -fsSL -o frontend/static/fonts/fraunces-variable.woff2 \
  "https://cdn.jsdelivr.net/fontsource/fonts/fraunces:vf@latest/latin-wght-normal.woff2"
curl -fsSL -o frontend/static/fonts/inter-variable.woff2 \
  "https://cdn.jsdelivr.net/fontsource/fonts/inter:vf@latest/latin-wght-normal.woff2"
```

## Was im Repo bleibt, was nicht

- **Versioniert:** `tailwind.config.js`, `input.css`, der Stub `styles.css`,
  die Stub-`htmx.min.js`/`alpine.min.js` (mit `TODO:`-Header), keine Fonts.
- **Build-Artefakt (regeneriert):** `styles.css` (im Docker-Image), die echten
  HTMX/Alpine-Bundles und die Fonts (alle im Docker-Image).
- **Lieferketten-Pins** (im `Dockerfile`):
  - `tailwindcss@3.4.17`
  - `htmx.org@2.0.4`
  - `alpinejs@3.14.8` (standard build; requires `'unsafe-eval'` in CSP — see security notes)
  - Fonts: Fontsource latest VF (OFL)

## Sicherheits-Hinweise

- HTMX/Alpine werden via HTTPS von etablierten CDNs (unpkg, jsDelivr) gezogen.
  Das ist eine Lieferketten-Vertrauensentscheidung; Pin-Versionen mindern,
  ersetzen aber keine SRI-Prüfung.
- Alpine wird im **Standard-Build** verwendet, weil die Templates flächendeckend
  Inline-Expressions nutzen (`x-data="…"`, `@click="…"`, `:class="…"`).
  Standard-Alpine baut diese Expressions zur Laufzeit via `new Function(...)`,
  was eine CSP mit `script-src 'self' 'unsafe-eval'` voraussetzt. Die restliche
  Härtung bleibt scharf: kein `'unsafe-inline'`, kein Remote-JS, kein `eval`
  aus User-Input. **TODO (`picture-stage-p07` / PS-UX-40):** Migration auf
  `@alpinejs/csp`-Build + `Alpine.data()`-Registrierungen, dann
  `'unsafe-eval'` wieder aus der CSP entfernen.
- Fonts liegen unter `/static/fonts/` und werden über `default-src 'self'`
  geladen. Falls eine separate `font-src`-Direktive in der CSP eingeführt
  wird (PS-UX-04), muss sie `'self'` enthalten.

## Frontend-Interaktions-Hygiene (CSP-konform)

Die CSP erlaubt `script-src 'self' 'unsafe-eval'` – aber bewusst **kein**
`'unsafe-inline'`. Daraus folgt eine harte Regel für Templates:

| Mechanismus | Erlaubt? | Wofür |
|-------------|----------|-------|
| `onclick="…"`, `onchange="…"`, `onsubmit="…"`, `onload="…"` (Inline-DOM-Handler) | **NEIN** – wird von CSP blockiert | – |
| Alpine `@click="…"`, `@change="…"`, `@submit="…"` (Inline-HTML-Attribute) | **JA** – via standard Alpine eval | UI-Reaktionen, lokaler State |
| Alpine `x-data="…"`, `x-show="…"`, `:class="…"` | **JA** | Lokaler State, conditional rendering |
| HTMX `hx-post`, `hx-get`, `hx-target`, `hx-swap`, `hx-trigger` | **JA** | Server-Roundtrips |
| HTMX `hx-on::after-request="…"`, `hx-on::before-send="…"` | **JA** | HTMX-Lifecycle-Hooks |
| `<form method="post" action="…">` (klassisch) | **JA** | Auth, Setup, Sprach-Switch |
| `<script>` inline im Template | **NEIN** – wird von CSP blockiert | – |
| Externes JS in `<script src="…">` aus `/static/` | **JA** | Globaler App-Code (`app.js`, `components.js`) |

**Hinzu kommen zwei Rule-of-Thumb-Hinweise**, die uns in v0.5 bereits Bugs
beschert haben:

1. **HTMX-Targets müssen IMMER im DOM existieren** – auch im Empty-State.
   Ein `<div id="grid">` darf nicht conditional gerendert werden, wenn ein
   HTMX-Swap dorthin zielt. Conditional ist nur der Inhalt (Cards vs. Empty-State).
2. **Buttons in `<form>` immer `type="button"` setzen**, wenn sie nicht
   Submit auslösen sollen. Default ist `submit`, was unbeabsichtigte
   Form-Submissions verursacht.
3. **Alpine-Komponenten-Code gehört in `frontend/static/js/components.js`**,
   niemals als `<script>`-Block im Template. Server-gerenderte Initialwerte
   wandern als `data-*`-Attribute aufs `x-data`-Wurzelelement, JavaScript liest
   sie in der `init()`-Methode aus `this.$root.dataset`. Beispiel:

   ```html
   <div x-data="myComponent()"
        data-token="{{ token }}"
        data-items="{{ items | tojson | e }}">
   ```

   ```js
   window.myComponent = function () {
       return {
           token: '',
           items: [],
           init() {
               this.token = this.$root.dataset.token || '';
               this.items = JSON.parse(this.$root.dataset.items || '[]');
           },
       };
   };
   ```

   `components.js` wird in `base.html` und `guest_base.html` **vor**
   `alpine.min.js` geladen, damit die globalen Funktionen bei
   Alpine-Initialisierung verfügbar sind.
4. **Programmatisches Form-Submit** in einer Komponente immer mit
   `form.requestSubmit()`, niemals mit `htmx.trigger(form, 'submit')`.
   `htmx.trigger` dispatched ein `CustomEvent`, das HTMX nicht zuverlässig
   abfängt – parallel läuft die native Form-Submission und führt zu einem
   Page-Reload (oft sichtbar als „etwas blitzt auf und ist weg").
   `requestSubmit()` triggert einen echten `SubmitEvent`, den HTMX sauber
   abfängt und mit `preventDefault()` behandelt.

## TODO

- [ ] SRI-Hashes für HTMX/Alpine-Downloads im `Dockerfile` ergänzen
  (verifiziert die Integrität der CDN-Antwort).
- [ ] Font-Subset auf tatsächlich genutzte Glyphen reduzieren (PS-UX-03).
- [ ] CI-Stufe: `docker build` als Quality-Gate (verhindert kaputte Configs).
