# Picture-Stage – Plan & Release-Roadmap

**Stand:** 2026-05-24 (aktualisiert nach Research-Phase)
**Verantwortlich:** Kapitän Klaus Stoertebeker (Product Owner), Erster Offizier Claude (Implementierung)

## 1. Zielsetzung

Picture-Stage ist eine **self-hosted, Open-Source Web-App** für Fotografen, mit der sie Bildauswahlen mit ihren Foto-Models teilen. Models bewerten Bilder pro Stück (Auswahl zur Bearbeitung, Favorit, Freitext-Kommentar). Der Fotograf erhält am Ende eine exportierbare Auswahlliste, die er in Lightroom/Capture One weiterverarbeitet.

Picture-Stage ist die freie Alternative zu Picdrop, mit voller Datenhoheit, Container-Deploy und transparentem Source-Code.

## 2. Architektur-Überblick

### Stack

| Schicht | Technologie | Begründung |
|---|---|---|
| Backend | Python 3.12 + FastAPI | Wartbar, async, sicherheitsfreundlich, gute Doku |
| Validierung | Pydantic v2 | Strenge Typen an allen Eingaben |
| ORM/Migrationen | SQLAlchemy 2.x + Alembic | Sauberer Upgrade-Pfad zwischen Versionen |
| DB | PostgreSQL 16 | Robuste DB, separate Container-Instanz |
| Frontend | Vanilla JS + HTMX + Alpine.js + Tailwind | Keine schwere Build-Toolchain, gut wartbar |
| Bildverarbeitung | Pillow (MVP), pyvips bei Skalierung | Thumbnails (320/640/1280px WebP), dezentes Wasserzeichen |
| Async S3 | aioboto3 | Echte async S3-Ops statt boto3+Threadpool |
| Storage | Pluggable: Local-Volume ODER S3-kompatibel | ENV-konfiguriert, MinIO/AWS/B2/R2/Hetzner |
| Mail | aiosmtplib | Async-SMTP für Notifications |
| Rate-Limiting | slowapi | Schutz vor Brute-Force/Spam |
| Container | Multi-Stage Dockerfile | Schlankes Final-Image |
| CI/CD | GitHub Actions → Docker Hub | Multi-Arch Build (amd64 + arm64) |

### Komponenten

```
┌────────────────────┐    ┌──────────────┐    ┌───────────────────────┐
│  picture-stage     │───▶│  postgres    │    │  S3-Storage           │
│  (FastAPI + HTMX)  │    │  (DB)        │    │  (optional, falls     │
│                    │    │              │◀──▶│   STORAGE_BACKEND=s3) │
└─────────┬──────────┘    └──────────────┘    └───────────────────────┘
          │
          ▼ (oder LocalStorage)
   /data/images Volume
```

### Sicherheits-Säulen

1. **Tenant-Isolation auf DB-Ebene** – zentrale FastAPI-Dependency `current_user`, jede Query gefiltert nach `owner_id`. Pytest-Tests prüfen Isolation explizit.
2. **Share-Tokens** – `secrets.token_urlsafe(32)`, **SHA-256+Salt** gehasht in DB (nicht bcrypt — zu langsam für lookup-heavy Tokens; bcrypt bleibt für User-Passwörter), niemals im Klartext geloggt. Timing-Attack-Schutz via `hmac.compare_digest()`.
3. **Bild-Auslieferung** – ausschließlich über authentifizierten App-Endpoint mit **HMAC-SHA256-signierten** kurzlebigen URLs (Thumbnails 1h, Previews 15min). Originale werden Models **niemals** ausgeliefert.
4. **Wasserzeichen serverseitig** in Preview eingebrannt (Pillow RGBA-Overlay, Ecke unten rechts, enthält Token-Kurzcode zur Rückverfolgung), plus Frontend Speed-Bumps.
5. **Isolierte Guest-API** – separater FastAPI-Router nur für Model-Zugriff (`/g/`), keine Shared-Endpoints mit Admin-API. Minimale Angriffsfläche.
6. **CSP, HSTS, sichere Cookies, CSRF-Schutz** ab Tag 1 in Middleware.
7. **Rate-Limiting** auf Signup, Login, Token-Auflösung, Passwort-Versuche (20 req/10min per IP auf Token-Endpoint, Sliding Window).
7. **Secrets via ENV**, nie im Image; `.env.example` als Vorlage.
8. **E-Mail-Verifizierung** vor Admin-Freischaltung neuer Accounts.
9. **Captcha** (Altcha self-hosted oder hCaptcha) am Signup gegen Bot-Flut.
10. **Audit-Log** pro Galerie (Zugriffe, Bewertungen, IP, User-Agent).

## 3. Datenmodell (grob)

| Tabelle | Zweck | Wichtige Felder |
|---|---|---|
| `users` | Fotografen | id (UUID), email, password_hash (bcrypt), status (`pending`/`active`/`admin`), email_verified_at, locale |
| `galleries` | Pro User eine Galerie | id (UUID), owner_id, name, phase (`review`, später `delivery`), status (`draft`/`shared`/`completed`/`archived`), share_token_hash (SHA-256+Salt), share_token_salt, password_hash (nullable, bcrypt), expires_at (nullable), watermark_config |
| `images` | Bilder einer Galerie | id (UUID), gallery_id, storage_key, filename, exif, width/height, sha256 |
| `image_previews` | Generierte Web-Versionen (WebP) | image_id, variant (`thumb_sm`/`thumb_md`/`preview` → 320/640/1280px), storage_key |
| `selection_events` | **Event-sourced Bewertungen** (append-only) | id, image_id, share_session_id, action (`select`/`deselect`/`favorite`/`unfavorite`/`comment`), comment (nullable), timestamp. Aktueller Stand wird aus Event-Log abgeleitet → Audit-Trail, Undo, "was hat sich geändert" |
| `share_sessions` | Token-Auflösungen | id (UUID), gallery_id, started_at, ip, user_agent, completed_at (für „abgeschlossen"-Button) |
| `audit_log` | Sicherheitsereignisse | gallery_id, event_type, actor (user_id oder share_session_id), ip, ua, timestamp |
| `notification_configs` | Pro User | email_enabled, webhook_url, events (gallery_completed, signup_pending, ...) |
| `notification_deliveries` | Versand-Log | config_id, event, status, attempted_at |
| `pending_signups` | Wartende Registrierungen | email, password_hash, verification_token_hash, requested_at |

**Wichtige Architektur-Entscheidung (aus Research):**
- `selection_events` statt mutable `ratings`-Tabelle: Event-Sourcing erlaubt Audit-Trail, Undo und "was hat sich seit letztem Besuch geändert" ohne zusätzliche Logik. Der aktuelle Auswahl-Stand wird per View/Query aus dem Event-Log materialisiert.
- **UUIDs als externe IDs** auf allen Endpoints — verhindert IDOR-Angriffe durch sequentielle Enumeration. Intern können auto-increment IDs als FK bleiben.
- **SHA-256+Salt für Share-Tokens**, bcrypt für User-Passwörter — unterschiedliche Hash-Strategien für unterschiedliche Performance-Anforderungen.

**Migrations-Vorsorge für Post-MVP:**
- `galleries.phase` als Enum mit aktuell nur `review` – `delivery` wird später ergänzt für Voll-Auflösungs-Downloads.
- Storage-Layout trennt `originals/` und `previews/` schon jetzt sauber, `delivered/` kommt später dazu.

**API-Architektur (aus Research):**
- **Admin-API** (`/api/v1/...`): Volle CRUD, benötigt Auth-Token, Tenant-isoliert
- **Guest-API** (`/g/...`): Minimaler Router nur für Models — `GET /g/{token}`, `GET /g/{token}/images/{id}`, `POST /g/{token}/selections`, `POST /g/{token}/complete`. Keine Wiederverwendung von Admin-Endpoints.

## 4. Release-Roadmap

Drei gestaffelte Releases, damit der Kapitän früh testen und Feedback geben kann.

### v0.1 – „Minimal Viable Picdrop" (Kern-Workflow)

**Ziel:** Erste produktiv nutzbare Version. Galerie anlegen, Link verschicken, Bewertung kommt zurück, Auswahl exportierbar.

| Feature | Beschreibung |
|---|---|
| Auth: Registrierung + Login | Self-Signup, E-Mail-Verifizierung, Admin-Freischaltung, Captcha |
| Admin-Freischalt-UI | Pending-Liste, Freigabe per Klick |
| Galerie anlegen | Name, Wasserzeichen-Config |
| Bild-Upload | Drag & Drop, JPEG/PNG, Thumbnails + Web-Preview mit Wasserzeichen |
| Storage-Backend | Pluggable Local/S3 via ENV |
| Share-Link erzeugen | Token gehasht, optional Passwort (in v0.1 schon dabei) |
| Model-View (mobile-first) | Galerie-Ansicht, Vollbild, Auswahl + Favorit + Kommentar, Auto-Save |
| Export | CSV + JSON der ausgewählten/favorisierten Dateinamen |
| Tenant-Isolation | DB-Layer + Tests |
| Security-Middleware | CSP, HSTS, Cookies, CSRF, Rate-Limits |
| Docker-Setup | Dockerfile + docker-compose (App + Postgres) |
| GitHub Actions | Build + Multi-Arch + Push zu Docker Hub bei Tag |
| README + Quickstart | Installations- und Konfigurationsanleitung |

**Definition of Done für v0.1:**
- ✅ Fotograf kann sich registrieren, freigeschaltet werden, einloggen
- ✅ Galerie anlegen, Bilder hochladen, Share-Link an Model schicken
- ✅ Model kann mobil bewerten, Bewertungen werden gespeichert
- ✅ Fotograf kann Auswahl als CSV exportieren und in Lightroom filtern
- ✅ Deployment via `docker compose up -d` aus dem Hub-Image

### v0.2 – „Lifecycle & Komfort"

**Ziel:** App wird im Alltag administrierbar; Notifications schließen den Loop.

| Feature | Beschreibung |
|---|---|
| Galerie-Lifecycle | `draft → shared → completed → archived` mit Übergangs-UI |
| „Bewertung abgeschlossen"-Button für Model | Setzt Galerie auf `completed`, triggert Notification |
| Admin-Dashboard | Übersicht aller Galerien, Fortschritt (X/Y bewertet, Z favorisiert) |
| Bulk-Operationen | Galerie/Bilder löschen, Galerie duplizieren |
| Sortierung & Filter in Model-View | Nach Name, EXIF-Zeit; Filter Favoriten/Auswahl |
| Tastatur-Shortcuts (Desktop) | Pfeile, `j`/`f`/`c` im Vollbild |
| Notifications | SMTP-Versand + Webhook, pro User konfigurierbar; Events: gallery_completed, signup_pending |

**Definition of Done für v0.2:**
- ✅ Kapitän kann komplette Workflows ohne CLI/SQL administrieren
- ✅ Kapitän bekommt Notification, wenn Model fertig bewertet hat
- ✅ Galerie-Status klar erkennbar im Dashboard

### v0.3 – „Produktion & Compliance"

**Ziel:** Saubere Self-Hosting-Erfahrung mit Backup, DSGVO-Konformität und Mehrsprachigkeit.

| Feature | Beschreibung |
|---|---|
| Ablaufdatum pro Galerie (optional) | Default kein Ablauf; nach Ablauf Galerie unzugänglich |
| Audit-Log pro Galerie | Anzeige im Admin-UI, Export-Möglichkeit |
| DSGVO-Seiten | Impressum/Datenschutz als konfigurierbare Markdown-Dateien, Cookie-Hinweis |
| Galerie-Lösch-Workflow | Inkl. aller Bilder, Ratings, Sessions, Audit-Log-Anonymisierung |
| Backup/Restore-CLI | Container-Befehl: DB-Dump + Bilder-Manifest; Restore-Pfad dokumentiert |
| i18n | Deutsch + Englisch, locale pro User; Frontend + E-Mail-Templates |
| Erweiterte Wasserzeichen-Konfig | Pro Galerie überschreibbar |

**Definition of Done für v0.3:**
- ✅ App ist self-host-tauglich mit dokumentiertem Backup-/Restore-Pfad
- ✅ DSGVO-Pflicht-Seiten sind ausgeliefert und konfigurierbar
- ✅ App ist auf Deutsch und Englisch nutzbar

### Post-MVP (geplant, nicht im V1.0-Scope)

- **Voll-Auflösungs-Download fertig bearbeiteter Bilder** über separate Galerie-Phase `delivery` – End-to-End-Workflow (Auswahl → Bearbeitung → Auslieferung), ersetzt WeTransfer
- EXIF-Anzeige für Model (optional pro Galerie)
- Galerie-Vergleichsmodus (zwei Bilder nebeneinander)
- Anti-Screenshot-Speed-Bumps
- Tagging/Kategorien pro Bild für Fotograf
- Statistiken pro Model über mehrere Galerien
- Mehrere Models pro Galerie mit getrennten Bewertungen (Gruppen-Shootings)
- Event-API/Webhooks für Drittsysteme

## 5. Repo-Struktur (geplant)

```
picture-stage/
├── .github/
│   └── workflows/
│       ├── ci.yml              # Lint, Test, Type-Check bei jedem PR
│       └── docker-publish.yml  # Build + Push bei Tag
├── app/
│   ├── main.py                 # FastAPI-Einstieg
│   ├── config.py               # ENV-Parser (Pydantic Settings)
│   ├── db/                     # SQLAlchemy-Models + Alembic
│   ├── auth/                   # Registrierung, Login, Token
│   ├── admin/                  # Admin-API Router (/api/v1/...)
│   ├── guest/                  # Isolierte Guest-API (/g/...) – minimale Angriffsfläche
│   ├── galleries/              # CRUD, Lifecycle, Sharing
│   ├── images/                 # Upload, Preview-Generierung (WebP), Wasserzeichen
│   ├── selections/             # Event-sourced Selection-Log
│   ├── storage/                # StorageBackend ABC + Local/S3 Implementierungen
│   ├── notifications/          # SMTP + Webhook
│   ├── security/               # Middleware, CSRF, Rate-Limits, HMAC-Signing
│   └── templates/              # Jinja2/HTMX
├── frontend/
│   ├── static/                 # JS, CSS, Tailwind-Build-Output
│   └── i18n/                   # Sprach-Dateien
├── tests/
│   ├── unit/
│   ├── integration/
│   └── security/               # Tenant-Isolation-Tests
├── alembic/                    # DB-Migrationen
├── docs/
│   ├── deployment.md
│   ├── configuration.md
│   └── security.md
├── Dockerfile
├── docker-compose.yml          # App + Postgres + optional MinIO
├── .env.example
├── pyproject.toml
├── README.md
├── LICENSE                     # AGPLv3
├── CHANGELOG.md
└── PLAN.md                     # dieses Dokument
```

**Hinweis:** Alembic-Migrationen laufen als separater `migrate`-Service in docker-compose (nicht im App-Entrypoint), um Race Conditions in replizierten Setups zu vermeiden.

## 6. Sicherheits-TODOs (durchgehend)

- [ ] Captcha am Signup
- [ ] Rate-Limiting auf alle sensiblen Endpoints (Login, Token, Signup, Passwort)
- [ ] Tenant-Isolation mit dedizierten Pytest-Tests pro Endpoint
- [ ] Signierte kurzlebige URLs für Bild-Auslieferung
- [ ] Wasserzeichen serverseitig eingebrannt, Originale niemals ausliefern
- [ ] Security-Header-Middleware (CSP, HSTS, X-Frame-Options, Referrer-Policy)
- [ ] CSRF-Schutz auf allen state-changing Endpoints
- [ ] Secrets-Hygiene: keine im Image, keine in Logs
- [ ] Container läuft nicht als root
- [ ] Dependency-Audit via GitHub Dependabot

## 7. Offene Entscheidungen

- **Lizenz:** Vorschlag AGPLv3 (verpflichtet Forks, Modifikationen offenzulegen, schützt das Open-Source-Modell auch bei SaaS-Forks). Alternativ MIT, falls maximale Verbreitung wichtiger ist. → **Kapitän entscheidet**.
- **Captcha-Anbieter:** Altcha (selbst-gehostet, datenschutzfreundlich) vs. hCaptcha (komfortabler). → **Empfehlung Altcha** wegen DSGVO und Self-Hosting-Spirit.
- **Docker-Hub-Image-Name:** Vorschlag `<github-user>/picture-stage`. → **Kapitän nennt Account-Namen**.
- **Domain/Branding:** Name „Picture-Stage" final? Logo? → **später**.

## 8. Nächste Schritte

1. ✅ Plan freigegeben (dieses Dokument)
2. ⏳ GitHub-Repo anlegen, Lizenz wählen, README-Stub
3. ⏳ Repo-Skelett aufsetzen (Verzeichnisse, `pyproject.toml`, leeres Dockerfile, GitHub-Actions-Stubs)
4. ⏳ v0.1 Implementierung starten – atomar nach CLAUDE.md-Arbeitsweise (ein Feature, ein Review, ein Test, ein Commit)
