# Changelog

All notable changes to Picture-Stage are documented in this file.

## [Unreleased]

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
