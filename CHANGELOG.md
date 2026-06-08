# Changelog

All notable changes to Picture-Stage are documented in this file.

## [Unreleased]

### Security

- **Stateless JWTs are invalidated on password reset and account lock** (`picture-stage-7kr`). Previously an already-issued access token stayed valid for up to 24 h after an admin reset a user's password or disabled the account. Tokens now carry an `iat` claim and are rejected once the per-user `tokens_valid_after` cut-off (set on reset/lock) is passed.
  - No mass-logout on upgrade: the cut-off defaults to NULL, so existing tokens keep working until the next reset/lock of that user.
  - The check compares server-side timestamps only, so client clock skew is irrelevant. Multi-instance deployments should keep server clocks in sync (NTP).
- **Share links are now always HTTPS in production** (`picture-stage-0hp`). Behind a TLS-terminating reverse proxy the container only sees plain HTTP, which caused share links to leak the replayable share token over `http://`. Links are now built from the configured `APP_URL` and the scheme is forced to `https://` in production.
  - **Action required:** set `APP_URL` to your public HTTPS domain (e.g. `https://photos.example.com`). A missing/default `APP_URL` falls back to the request host with the scheme corrected to https.

### Changed

- **BREAKING:** `WATERMARK_OPACITY` environment variable now expects **float (0.0-1.0)** instead of **int (0-255)**
  - Old format (v0.0): `WATERMARK_OPACITY=255` (opaque) → `WATERMARK_OPACITY=1.0`
  - Old format (v0.0): `WATERMARK_OPACITY=0` (transparent) → `WATERMARK_OPACITY=0.0`
  - Old format (v0.0): `WATERMARK_OPACITY=128` (50% opaque) → `WATERMARK_OPACITY=0.5`
  - Aligns with CSS/HTML5 opacity conventions (0.0-1.0)
  - Gallery-level `watermark_config.opacity` already uses float (0.0-1.0)
  - Conversion to PIL alpha (0-255) happens internally in `app/images/processing.py`

### Added

- v0.4 Frontend: Complete Jinja2 templates, HTMX router, Alpine.js interactivity, CSRF protection, Cookie-based auth, i18n (DE/EN)
- v0.3 Compliance: DSGVO pages + cookie banner, audit logging with CSV export, backup/restore CLI, encryption for sensitive fields
- v0.2 Features: Gallery lifecycle (draft → shared → completed → archived), expiry dates with guest enforcement, watermark customization
- v0.1 API: 27 REST endpoints, image upload/processing, selection event sourcing, share tokens, guest viewer

## [0.1.0] - Initial Release

- Minimal viable Picdrop alternative: photographers share galleries, models select/favorite images
- Self-hosted, zero-trust design: end-to-end encryption option, no external dependencies
- FastAPI backend + PostgreSQL + HTMX+Alpine frontend
- 181 tests (165 unit/security + 16 integration), CI/CD ready
