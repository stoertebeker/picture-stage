# Picture-Stage

**Self-hosted photo proofing for photographers and models.**

Picture-Stage lets photographers share image galleries with models for review. Models can select images for editing, mark favorites, and leave comments — all without creating an account. Self-hosted, open-source, and fully under your control.

> **Status (2026-11-09):** The implemented feature backlog and the agreed optimization round are complete. Further improvement proposals are deferred drafts in Beads (`bd list --status=deferred --label=draft`); `bd ready` shows work available now. See [CLAUDE.md](CLAUDE.md#aktueller-stand) for the current agent handover and verification evidence.

## Features

- **Share galleries via magic link** — no model login required, optional password protection
- **Select, favorite, and comment** on individual images
- **Auto-save** — selections persist immediately (event-sourced)
- **Export selections** as CSV/JSON, or a comma-separated filename list (TXT) ready to paste into Lightroom Classic / Capture One
- **Server-side watermarking** — originals are never exposed to models
- **Asynchronous previews with upload progress** — progress follows transfer and preview processing, including failed images
- **Editorial Dark frontend** — light/dark themes, DE/EN, keyboard and touch navigation in the gallery lightbox
- **Pluggable storage** — local Docker volume or S3-compatible (MinIO, AWS, Hetzner, R2, B2)
- **Multi-tenant** — multiple photographers with admin approval registration
- **Gallery deletion from dashboard or detail view** — a confirmation dialog with Cancel / Delete permanently; removes its images and previews, reports storage failures and retains references for retry
- **Admin user management** — list users, change roles, lock/unlock, delete, reset passwords
- **Multi-arch Docker image** — runs on amd64 and arm64 (Raspberry Pi, Synology)

## Installation (Docker)

```bash
# 1. Verzeichnis anlegen
mkdir picture-stage && cd picture-stage

# 2. Compose und Konfiguration herunterladen
curl -O https://raw.githubusercontent.com/stoertebeker/picture-stage/main/docker-compose.yml
curl -O https://raw.githubusercontent.com/stoertebeker/picture-stage/main/.env.example
cp -f .env.example .env

# 3. .env anpassen — diese Werte MÜSSEN von CHANGE_ME abweichen, sonst startet die App nicht:
#    SECRET_KEY         (generieren: python3 -c "import secrets; print(secrets.token_urlsafe(64))")
#    HMAC_SECRET_KEY    (generieren: python3 -c "import secrets; print(secrets.token_urlsafe(32))")
#    APP_URL            (öffentliche HTTPS-Domain, z.B. https://deine-domain.tld — sonst zeigen Share-Links auf den internen Host)
#    BOOTSTRAP_TOKEN    (für die Ersteinrichtung generieren: python3 -c "import secrets; print(secrets.token_urlsafe(32))")
#    Nur für lokale HTTP-Entwicklung: ENVIRONMENT=development und APP_URL=http://localhost:8000.
#
#    Optional — E-Mail-Benachrichtigungen (z.B. Mailjet, leer = aus):
#    SMTP_HOST/_PORT/_USER/_PASSWORD/_FROM   (Mailjet: HOST=in-v3.mailjet.com, PORT=587,
#                                             USER=API-Key, PASSWORD=Secret-Key; FROM-Domain
#                                             muss beim Provider verifiziert sein)
#    NOTIFY_ADMINS_ON_SIGNUP=true            (Mail an alle Admins bei Neuregistrierung)
#    NOTIFY_OWNER_ON_COMPLETION=true         (Mail an Fotograf, wenn Model die Galerie abschließt)
#    Credentials testen: python scripts/smtp_smoke.py

# 4. Starten
docker compose up -d

# 5. Öffnen — beim ersten Start erscheint der Setup-Assistent
#    Deine konfigurierte öffentliche HTTPS-Adresse (lokal im Development-Modus: http://localhost:8000).
#    Gib das BOOTSTRAP_TOKEN ein und erstelle den Admin-Account.
#    API-Docs: /docs unter derselben Adresse.
```

If all users have been removed, the public setup remains closed. An operator
with local container access can recreate the administrator interactively:

```bash
docker compose exec app python -m app.cli.recover_admin
```

The command accepts the password through an interactive prompt and requires the
user table to be empty. Database migration `0010` also guards direct admin
inserts so an older app process cannot claim first-admin authority during an
upgrade. Downgrading removes that guard and the setup marker. A full database
dump and restore was tested; a data-only restore into an existing schema is
not supported with this guard.

### Backup and restore

The `picture-stage` CLI and PostgreSQL 16 client tools are included in the image:

```bash
picture-stage backup --output /backups/picture-stage.tar.gz
picture-stage restore --input /backups/picture-stage.tar.gz --confirm
```

New backups contain a PostgreSQL custom-format `db.dump` and are authenticated
by an external HMAC sidecar, such as `picture-stage.tar.gz.sig`. Keep both files
together and securely retain the original `HMAC_SECRET_KEY` separately; it is
not included in the archive and must contain at least 32 bytes of random secret
material. Restoring on another host requires that original key. Key rotation
invalidates earlier backup signatures and existing signed media URLs. Existing
archive or sidecar paths are never overwritten; choose a new backup filename.
Restore verifies the complete compressed archive before extraction and restores
from a private verified snapshot using `pg_restore`.

Old, unsigned backups you created yourself can be restored only after checking
their provenance and explicitly choosing the legacy exception:

```bash
picture-stage restore --input /backups/old.tar.gz --confirm --trust-legacy-unsigned
```

This exception executes archive-controlled SQL through `psql`, which can run
commands as the restore process. It does not bypass an invalid or malformed
signature and applies only to the old unsigned format. Trusted database contents
remain necessary even with signed custom dumps, since PostgreSQL SQL can execute
server-side code.

Every restore, including the legacy exception, uses finite resource limits.
The defaults allow 100 GiB of compressed input, 512 GiB of expanded TAR bytes
(including headers/padding), 64 GiB per file, and 500,000 raw headers and output
objects including directories. Paths are limited to 16 components and 4,096
UTF-8 bytes. Parser extensions allow 64 KiB each, 16 MiB in total, and 16 chained
headers; metadata/manifest JSON is limited to 64 KiB. The compression-ratio cap
is 1,000 and the archive-processing deadline is 900 seconds through image
staging. The deadline is cooperative and does not interrupt kernel I/O or the
database subprocess.

`picture-stage restore --help` lists the positive, finite `--max-*` overrides
for larger existing archives. Zero never disables a limit. Peak temporary disk
use can include the compressed snapshot, expanded contents, and a second copy
of local images on the upload filesystem. Extraction rejects unsafe member
types, traversal, duplicate destinations, sparse formats and malformed trailing
data before changing the database.

Run restore with the application stopped and `DATABASE_URL` pointing to an
existing, empty database (created from `template0`). A database containing user
tables, views or sequences is rejected before changes; restore does not replace
an existing schema. Use a full archive produced by `picture-stage backup`;
data-only dumps are not supported. The command ignores psql startup files,
stops on SQL errors and restores the dump in one transaction. SQL failure
returns a nonzero exit status and rolls back database changes.

Local images are staged on the upload filesystem before restoring SQL, requiring
additional space for the image files. Existing images stay untouched if staging
or SQL restoration fails. After the database commits, each image is replaced
atomically. Database and filesystem changes cannot commit together: a later
image-publication failure returns a nonzero status and explicitly reports that
the database was restored but the images are incomplete. Keep the application
stopped and recover the files from the backup before using it. S3 archives
contain a manifest only; the bucket must be backed up and recovered separately.

### Update auf eine neue Version

Reihenfolge ist wichtig — `pull` **vor** `up -d`, sonst läuft das alte
Origin-Image weiter (eine der drei Cache-Schichten, siehe unten):

```bash
# 1. Neues Image ziehen (Tag in docker-compose.yml: :latest = stable, :dev = main)
docker compose pull
docker compose up -d

# 2. Origin verifizieren: fährt der Container wirklich den neuen Asset-Stand?
docker compose exec app grep -o "Alpine.js v[0-9.]*" /app/frontend/static/js/alpine.min.js

# 3. End-to-End verifizieren: welche Asset-Version liefert die Instanz aus?
#    (zeigt den ?v=-Token der laufenden Auslieferung — prüft Origin + CDN zugleich)
curl -s https://DEINE-DOMAIN/login | grep -o "/static/css/styles.css?v=[^\"]*"

# 4. Hinter Cloudflare: Edge-Cache für /static/* purgen, falls Schritt 3 noch alt ist
#    Dashboard -> Caching -> Configuration -> Purge (Everything oder per API /static/*)
```

> **Cache-Busting:** JS/CSS-Assets werden mit `?v=<ASSET_VERSION>` ausgeliefert
> (im CI-Build der Zeitstempel). Ein neues Image = neue URL = automatischer Bust
> von Browser- und CDN-Cache. Voraussetzung: Cloudflare-Caching-Level **Standard**
> (Query-String bleibt Teil des Cache-Keys). Fonts sind bewusst un-versioniert
> (vermeidet Preload/`url()`-Mismatch). Hintergrund: die drei Cache-Schichten in
> `docs/lessons-learned.md` (GHA-Layer / Origin-Image / Cloudflare).

> **Tag-Wahl:** `:latest` für die Produktion (nur stabile Releases), `:dev` für
> einen Test-Server (folgt jedem `main`-Commit). Details siehe [Docker Hub](#docker-hub).

## API Overview

### Authentication (`/api/v1/auth/`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/auth/signup` | Register (pending admin approval) |
| GET | `/api/v1/auth/captcha/challenge` | Obtain a signed signup challenge |
| POST | `/api/v1/auth/verify-email/{token}` | Verify email address |
| POST | `/api/v1/auth/login` | Login, receive JWT |
| GET | `/api/v1/auth/me` | Current user profile |
| POST | `/api/v1/auth/logout` | Compatible logout alias; requires browser CSRF proof |

Signup requires a fresh ALTCHA proof when `CAPTCHA_ENABLED=true` (the default).
Fetch a challenge from `/api/v1/auth/captcha/challenge`, solve it, then send the
Base64 payload as `altcha` in the signup JSON. The HTML form solves the same
challenge using self-hosted ALTCHA 3.5.0 assets, JavaScript, WebCrypto and a Web
Worker; HTTPS or localhost is required. Login and initial setup do not require it.
Challenges expire after five minutes and can be redeemed once across both
signup routes. Missing, invalid, expired or reused proofs return 422; unavailable
verification or a full replay ledger returns 503. A failed signup still consumes
its proof. The ledger retains at most 10,000 entries and reclaims up to 256
expired entries per redemption.

Set `ALTCHA_SECRET_KEY` to a random secret containing at least 32 UTF-8 bytes,
or leave it empty to derive a separate key from a sufficiently strong
`SECRET_KEY`. An explicitly invalid key fails startup instead of falling back.
`CAPTCHA_ENABLED=false` explicitly disables this signup gate.

Historical hash-only verification links may need several bounded lookup steps.
On a 503 with `X-Picture-Stage-Lookup: before-effects`, API clients repeat the
same verification POST with its cookie jar after `Retry-After`. The HTML link
continues automatically. Each resolver step checks at most 64 candidates;
successful consumption rechecks the current verifier under its row lock.
Verification links retain their existing lifetime; progress cookies expire
after 15 minutes and renew as lookup advances.

Browser logout uses `POST /logout`. The compatible API alias requires the same
CSRF cookie and matching header/form proof, even without an incoming session
cookie or when an `Authorization` header is present. Proofs are bound to the
owner session when one is present. Ordinary bearer-only API routes remain
exempt from browser CSRF checks.

Successful logout commits a per-credential revocation before deleting the cookie.
The same credential is then rejected by both bearer and browser authentication,
including equivalent signature encodings. Other logins remain valid; newly issued
tokens have independent random IDs even when created in the same second. Two
different valid header/cookie credentials return 409 without changing either.
Malformed or expired credentials can still be cleared locally.

Migration `0014` stores only a credential fingerprint and its retention deadline;
bounded cleanup removes entries only after their JWT can no longer authenticate.
The ledger is included in full database backups. Historical byte-identical JWTs
are one credential and cannot be distinguished. Already authenticated requests
are not interrupted retroactively.

### Galleries (`/api/v1/galleries/`)

The gallery detail form accepts an optional expiry **date**. A saved date expires
at 23:59:59.999999 UTC on that day, independent of the browser/server timezone;
today is allowed until that instant. Existing expiry timestamps stay unchanged
until explicitly saved. The JSON API continues to accept precise `expires_at`
timestamps with its existing future-time validation.
The form's latest supported date is `9999-12-30`: asyncpg reserves the following
day's final microsecond for its PostgreSQL infinity sentinel. Date-only frontend
values are bounded consistently in HTML and on the server; API codecs are unchanged.
The JSON API rejects the UTC maximum instant reserved by asyncpg for infinity,
and timestamps whose offsets overflow the representable UTC range, with HTTP 422.
Other future timestamps retain their exact precision and instant. Existing
database infinity values retain their access semantics without crashing guest links.

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/galleries` | Create gallery |
| GET | `/api/v1/galleries` | List own galleries |
| GET | `/api/v1/galleries/{id}` | Gallery details |
| PATCH | `/api/v1/galleries/{id}` | Update gallery |
| DELETE | `/api/v1/galleries/{id}` | Delete gallery + images |
| POST | `/api/v1/galleries/{id}/share` | Generate share link |
| DELETE | `/api/v1/galleries/{id}/share` | Revoke share link |
| GET | `/api/v1/galleries/{id}/export` | Export selections (CSV/JSON/TXT, `filter=all\|selected\|favorited\|marked`) |

CSV exports prefix spreadsheet formula-like text with an apostrophe, including
filenames and comments. Stored values and JSON/TXT exports are unchanged.

Dashboard summaries use the shared query service in `app/galleries/dashboard.py`:
five SQL statements for HTML with covers and four for the JSON API, excluding
authentication queries. Empty dashboards need one statement; the admin API adds
one pending-signup count. Progress counts distinct images that ever received a
select, favorite or comment event, including later undone actions. Guest review
state and exports instead use the current selection projection.

Owner lookup, share credentials and status transitions share commands in
`app/galleries/management.py`. HTML share/password/revoke fragments render only
the required gallery context, without loading images or signing media URLs.

### Images (`/api/v1/galleries/{id}/images/`)

Watermarks apply only to the large preview (up to 1280px wide). The 320px and
640px thumbnails intentionally remain unwatermarked. A gallery configuration of
`enabled: false` also disables the large-preview overlay. Guest views receive
signed thumbnail and preview URLs, never original-image URLs.

Multi-file uploads commit their image records as one batch. Migration `0008`
adds internal cleanup intents before storage writes, so failed uploads retain
their object keys even if immediate cleanup fails. Gallery/user deletion retries
those keys and retains the references while storage is unavailable. Original
storage keys use server-generated UUIDs and MIME-based extensions; uploaded
filenames remain metadata. There is no periodic janitor or automatic discovery
of historical objects without an image record, cleanup intent or storage
allocation. If a commit
acknowledgement is lost, committed files are preserved; the request still fails
and frontend preview dispatch is not automatically retried.

Migration `0013` adds durable storage reservations per owner. Originals, previews,
gallery copies and incomplete writes count toward `MAX_STORAGE_BYTES_PER_OWNER`
(default 10 GiB) and `MAX_STORAGE_OBJECTS_PER_OWNER` (default 40,000). Both values
must be positive. Reservations commit before backend writes and are released
only after confirmed deletion, including on failed uploads. Existing data is
retained when limits are lowered; reads and deletion remain available. Historical
objects with unknown sizes block further allocation for their owner until the
affected images or galleries are deleted. Failed compensation remains retryable
through gallery/user deletion; there is no automatic orphan sweep. S3 accounting
covers current keys, not provider-retained object versions.

Image bytes must decode as JPEG, PNG or WebP, regardless of the supplied MIME
type. Invalid or unsupported images are rejected with HTTP 422. Previously
stored images in other formats cannot be used to generate new previews.

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/galleries/{id}/images` | Upload images (multi-file) |
| GET | `/api/v1/galleries/{id}/images` | List images with preview URLs |
| DELETE | `/api/v1/galleries/{id}/images/{id}` | Delete image |

### Guest API (`/g/`) — isolated router, no auth required

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/g/{token}` | Access shared gallery |
| POST | `/g/{token}/verify-password` | Unlock password-protected gallery |
| GET | `/g/{token}/images` | List images with signed preview URLs |
| POST | `/g/{token}/selections` | Submit selection event |
| GET | `/g/{token}/selections` | Current selection state |
| POST | `/g/{token}/complete` | Mark review as done |

Image lists use 30-image pages, with the database bound applied before preview
hydration and URL signing. The JSON body remains a list; responses expose
`X-Next-Cursor`, `X-Has-More`, `X-Total-Images` and `X-Filtered-Images`.
Pass `cursor` with the same filter, sort and direction to continue. Nonzero
offsets are rejected. HTML scrolling and lightbox navigation load further pages
on demand. Migration `0018` backfills capture-date keys and current selection
state while retaining the append-only history; older full backups migrate on
startup, and new backups include the projection.

Guest browsers keep their session credential in a gallery-scoped, host-only
HttpOnly cookie with SameSite=Lax, the configured Secure flag, and a signed
12-hour server-checked expiry. The cookie path includes the application mount
prefix and share link. The bearer is absent from browser HTML, JavaScript and
generated URLs. Reads use the cookie or the explicit API header
`X-Guest-Session: <UUID>`; a `session_id` query parameter no longer authenticates.
API writes can still supply an explicit UUID in JSON or that header. Invalid,
null or conflicting explicit credentials never fall back to a browser cookie.

Cookie-based browser writes require `X-Guest-CSRF-Token`; the password form also
requires the challenge from its preceding GET. This guest proof is separate
from owner CSRF. Valid browser cookies and their exact CSRF/expiry context are
reused across tabs of the same gallery; different galleries remain separate.
Guest representations are private and non-cacheable.

`MAX_SHARE_SESSIONS_PER_GALLERY` limits all retained visits per gallery to 1,000
by default, including completed, historical and obsolete-generation sessions.
Admission is serialized across workers; dropping cookies does not bypass the
limit. New visits return 409 at capacity, while existing valid visits remain
usable. `SHARE_SESSION_TTL_SECONDS` expires new sessions after 43,200 seconds
(12 hours). Both settings require positive integers up to 2,147,483,647.

Each admission examines at most 100 expired, unfinished sessions for cleanup.
A persisted per-gallery cursor rotates past sessions referenced by selection
history or audit entries; those sessions are preserved. Cleanup also persists
when admission is refused, so a lowered limit can recover over several requests.
Migration `0015` leaves existing sessions without an expiry and preserves their
grants and history; these rows still count toward the limit. Cleanup is driven
by admission attempts rather than a background schedule.

Guest sessions carry the gallery's current authorization generation. Setting,
changing or removing a password, creating/rotating a share link and revoking a
link advance that generation atomically. Previous sessions can no longer write
selections or complete a review, and cannot read password-protected data. A
fresh password verification or passwordless visit creates a current session,
or reuses the browser's existing valid, unfinished session.
Removing password protection restores token-only reads. Historical sessions and
their selection events remain stored; password changes preserve the gallery
link. Migration `0011` initializes existing galleries and sessions at generation
zero, so invalidation starts with subsequent authorization changes.

Password verification, session issuance and guest data rendering are ordered
against authorization changes with gallery row locks. Previously signed image
URLs retain their existing validity: one hour for thumbnails and fifteen
minutes for previews. Session invalidation prevents issuing new URLs through
the protected guest routes; it does not revoke already issued media signatures.

Selection writes and review completion lock the specific gallery row until
commit, also ordering them against owner status changes. Once completed, new
selections return 403. Repeated completion requests, including fresh sessions
or concurrent requests, return 409 without another email/webhook attempt.
Completed galleries remain viewable. An owner can reopen a gallery for another
review while retaining selection history; a session that already completed a
review cannot complete again, but another session can complete the reopened
gallery. Only the successful completion transition attempts notifications after
commit. Delivery remains best effort, without a durable queue or crash recovery.

### Admin (`/api/v1/admin/`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/admin/pending-signups` | List pending registrations |
| GET | `/api/v1/admin/pending-signups/count` | Count pending registrations |
| POST | `/api/v1/admin/approve/{id}` | Approve registration |
| DELETE | `/api/v1/admin/reject/{id}` | Reject registration |
| GET | `/api/v1/admin/users` | List user accounts (paginated, status filter) |
| PATCH | `/api/v1/admin/users/{id}/status` | Promote/demote, lock/unlock (disabled) |
| DELETE | `/api/v1/admin/users/{id}` | Delete user incl. galleries + storage files |
| POST | `/api/v1/admin/users/{id}/reset-password` | Set a new password for a user |

## Security

- **Share links:** long random capability URLs are hashed for lookup and stored replayable for the owner UI; optional gallery passwords protect sensitive galleries
- **Token lookup:** new verification links and recoverable gallery links use indexed lookup keys. Older links remain valid through fixed lookup windows and signed, token-bound progress cookies. A gallery resolver checks at most 64 indexed and 64 legacy candidates plus an optional single hint; verification checks at most 64 candidates before its locked consumption recheck. A first legacy lookup can span several requests. Browsers retry only explicitly marked, pre-effect lookup failures; API clients retain cookies and repeat after `Retry-After`. Each process additionally admits at most two legacy lookups of each kind concurrently. Hints always require current salted verification and access checks; encoded and mounted links retain their cookie scope.
- **Signed URLs:** HMAC-SHA256 with configurable TTL (thumbnails 1h, previews 15min)
- **Webhook delivery:** destinations must resolve entirely to public unicast addresses. Delivery connects directly to a checked address with the original hostname for TLS verification; environment HTTP proxies are not used for webhooks. Redirects are rejected and response bodies are not read.
- **Rate limiting:** undecorated application routes use 200/min per matched route template and client address, before endpoint parsing. Dynamic token values share that default budget. Explicit limits retain their existing scopes (signup 5/min, login 10/min, password verification 5/min). Counters use process-local memory by default; `RATELIMIT_ENABLED=false` disables enforcement. Forwarding headers are not used directly as client identity. Static mounts and unmatched/redirect requests retain their normal handling.
- **Security headers:** CSP, HSTS, X-Frame-Options, Referrer-Policy
- **CSRF recovery:** authenticated forms use session-bound HMAC tokens and synchronize them before submission. Restored browser pages and rejected stale clients recover through a safe GET; POSTs rejected for stale CSRF proof are never automatically replayed.
- **Request bodies:** ordinary JSON/forms are capped at 1 MiB before CSRF and parsing. The two image-upload POST routes retain the global upload budget. Declared lengths and received bytes are checked; the smaller enabled ceiling applies.
- **Image processing admission:** each app process admits at most `IMAGE_PROCESSING_WORKERS` uploads or preview jobs at once, with at most twice that number waiting for up to five seconds. Busy uploads return HTTP 503 with `Retry-After`; accepted frontend uploads keep their slot through preview processing. This limit is per process and does not bound multipart parsing before admission.
- **Tenant isolation:** owner-scoped queries, covered by authorization and query-boundary tests
- **Non-root container:** app runs as unprivileged user

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12, FastAPI, Pydantic v2 |
| Database | PostgreSQL 16 |
| ORM | SQLAlchemy 2.x + Alembic (async) |
| Frontend | HTMX, Alpine.js, Tailwind CSS |
| Image processing | Pillow (WebP thumbnails at 320/640/1280px, watermarks) |
| Storage | Local filesystem or S3-compatible (via aioboto3) |
| Container | Multi-stage Docker, docker-compose |
| CI/CD | GitHub Actions (lint, test, multi-arch Docker Hub publish) |

## Configuration

All configuration is done via environment variables. See [`.env.example`](.env.example) for the full list with descriptions.

`ENVIRONMENT` accepts only `production` (the default), `development` or `test`;
case and surrounding whitespace are normalized. Production requires a complete
HTTPS `APP_URL` with a valid host and optional port/path prefix. Credentials,
query strings, fragments, backslashes, whitespace and control characters are
rejected. For local HTTP, explicitly set `ENVIRONMENT=development` and
`APP_URL=http://localhost:8000`. Cookie security and HSTS use this configuration,
independently of the request scheme or forwarded headers.

Key variables:

| Variable | Description |
|----------|-------------|
| `SECRET_KEY` | JWT signing key (must change from default) |
| `HMAC_SECRET_KEY` | Image URL signing key (must change from default) |
| `APP_URL` | Public base URL; production requires your valid HTTPS address, independently of reverse-proxy headers |
| `DATABASE_URL` | PostgreSQL connection string |
| `STORAGE_BACKEND` | `local` or `s3` |
| `MAX_REQUEST_BODY_MB` | Global body ceiling in MiB, default `1024`; `0` disables this ceiling |
| `MAX_NON_UPLOAD_REQUEST_BODY_KB` | Additional ceiling for non-upload requests in KiB, default `1024`; `0` disables this additional ceiling |
| `MAX_SELECTION_COMMENT_CHARS` | Maximum Unicode characters in a new guest comment; default `2000` |
| `MAX_SELECTION_EVENTS_PER_GALLERY` | Maximum retained selection events per gallery; default `20000` |

Selection limits must be positive. Existing comments and events remain readable
if a limit is lowered; new writes are rejected once the applicable limit is reached.

## Development

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install with dev dependencies
pip install -e ".[dev]"

# Run linter
ruff check .
ruff format --check .

# Run type checker
mypy app/

# Run DB-free tests
pytest tests/unit/ tests/security/

# Run the full suite (requires a separate PostgreSQL test database;
# fixtures rebuild schemas and truncate data — never use the app database)
pytest --cov=app --durations=30 --junitxml=test-results/pytest.xml

# Run tenant isolation tests
pytest tests/security/ -v
```

Restore tests in `tests/cli/` additionally require PostgreSQL 16 `psql`,
`pg_dump` and `pg_restore` on `PATH`, and a test database role with `CREATEDB`. They create and
drop uniquely named temporary databases from `template0`; without the client
tools these tests are skipped. CI installs the matching tools explicitly.

The checked-in `frontend/static/css/styles.css` is a generated Tailwind snapshot;
Docker regenerates it from the current inputs. The tracked HTMX/Alpine files are
placeholders replaced by real pinned bundles during the Docker asset build.
Fonts and the local CAPTCHA assets are already tracked.
For visual checks, build the local app with a fresh `ASSET_VERSION` and use the
local Compose stack. CI runs lint, formatting, type checks and the full test
suite against PostgreSQL, including migration checks.

Integration tests rebuild the ORM schema at module boundaries and truncate all
ORM tables before each test. Requests and concurrent sessions still use real
commits; they are not wrapped in an outer rollback transaction. Fixed unused
fixture passwords reuse a real bcrypt hash without changing production hashing.
CI reports the slowest setup, call and teardown phases and retains JUnit timing
results as the `pytest-results` artifact, including after test failures.

The last full source verification was on 2026-11-06 at `c3554e6`:
[GitHub Actions run 42236256200](https://github.com/stoertebeker/picture-stage/actions/runs/42236256200)
passed all 1,809 tests with no skips in 368.19 seconds, 82% line coverage, Ruff,
formatting, mypy and the AMD64/ARM64 image build. Subsequent issue/documentation
commits do not represent a fresh full test run. The browser scripts in
`tests/e2e/` remain outside normal CI; integrating a selected set is draft
`picture-stage-l0f6`.

## Docker Hub

Multi-arch images (amd64 + arm64) are built and pushed automatically via GitHub Actions ([`docker-publish.yml`](.github/workflows/docker-publish.yml)).

| Tag | When it's built | Use for |
|-----|-----------------|---------|
| `:latest` | On every version tag `v*` | **Production** — stable releases only |
| `:1.2.3`, `:1.2`, `:1` | On every version tag `v*` (semver) | Pinning a specific version |
| `:dev` | On build-triggering pushes to `main` | **Staging / test server** — latest development build |
| `:sha-<short>` | On build-triggering pushes to `main` | Pinning a specific commit |

```bash
# Production / stable
docker pull stoertebeker2k/picture-stage:latest

# Test server / bleeding edge
docker pull stoertebeker2k/picture-stage:dev
```

### How the pipeline is gated

The Docker build runs only when both conditions hold, so no image is ever published from broken or irrelevant code:

1. **CI must be green.** [`ci.yml`](.github/workflows/ci.yml) is a reusable workflow (`workflow_call`) invoked as a prerequisite job — `build-and-push` has `needs: ci`. Lint (ruff), type check (mypy) and tests (pytest) all run before any image is built. This applies to `:dev` builds too.
2. **Relevant files changed** (push to `main` only). A `dorny/paths-filter` gate skips the build for doc-only commits. The current filter includes `app/`, `alembic/`, `frontend/`, `tests/`, `Dockerfile`, `pyproject.toml`, and the CI/publish workflow files. Completing its coverage of other build inputs is draft `picture-stage-p0na`. **Version tags `v*` always build**, regardless of the path filter.

## License

[GNU Affero General Public License v3.0](LICENSE) — Copyright 2026 Norbert Schramm

If you modify Picture-Stage and offer it as a service, you must publish your changes under the same license.
