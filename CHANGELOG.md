# Changelog

All notable changes to Picture-Stage are documented in this file.

## [Unreleased]

### Security

- **Gallery password verification via the web form is now rate-limited** (`picture-stage-a2d`). The HTML endpoint `POST /g/{token}/verify-password` had no rate limit (its JSON-API counterpart always had 5/minute), allowing unthrottled brute-force attempts against the bcrypt gallery password through the guest form. Both paths now enforce 5/minute.
- **CSP hardened: `'unsafe-eval'` removed from `script-src`** (`picture-stage-u3s`). The standard Alpine.js build compiles inline expressions with the `Function` constructor, which forced `'unsafe-eval'` in the Content-Security-Policy (a broad XSS-amplification surface). All inline Alpine expressions were migrated to `Alpine.data()` component registrations plus a delegated dialog/listener pattern in `app.js` (data-attributes instead of inline `$refs.x.showModal()` / `$el.closest('dialog').close()` / `hx-on`), and the build was switched to `@alpinejs/csp`, which evaluates expressions without `eval`. `script-src` is now `'self'` only — defense-in-depth against XSS.
- **Signup no longer reveals whether an account exists** (`picture-stage-42q`). Previously a repeated registration with an already-known email returned HTTP 409 with an explicit hint (account enumeration / information disclosure), on both the web form and the JSON API. A signup with an existing email (as user *or* pending) now returns the same neutral success response as a fresh one, creates no new pending signup, and never overwrites an existing account/password (which would otherwise be an account-takeover vector). Best-effort timing equalization (matching bcrypt cost) reduces the existing-vs-new timing signal.
  - **BREAKING (API):** `POST /api/v1/auth/signup` no longer returns `verification_token` in the response body — emitting it for fresh signups but `null` for existing emails would itself leak existence. The token is persisted server-side and is delivered by email (`picture-stage-x8t`). Clients that read the token from the response must change.
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

- **Gallery password can now be set, changed or removed after sharing** (`picture-stage-1y5`). Previously the password could only be chosen while creating the share link; changing it required revoking and re-creating the link, which rotated the token and invalidated the magic link already sent to the model. The share modal now offers a password section for active links, backed by `POST /galleries/{id}/password` (HTML frontend) — the share token stays untouched. Note: guests who already unlocked the gallery in an open tab keep their session; the gate applies from the next page load.
- **Guest password gate redesigned (Editorial Dark, `qdz.16`)**. The gate now shows the gallery name above the card, a friendlier error message and a privacy note, in dark and light theme. Verifying the password renders the full viewer page (the previous partial swap left the unlocked gallery without header, lightbox and selection state).
- **Verification email on signup** (`picture-stage-x8t`). A new registrant now receives an email with a confirmation link (built from `APP_URL`, HTTPS in production; the plaintext token travels in the link while the DB keeps only its SHA-256+salt hash). The mail is only sent on the genuine new-signup path, so it never fires for an already-registered email — keeping the account-enumeration guard (`picture-stage-42q`) intact. Config-free and gated by `SEND_VERIFICATION_EMAIL_ENABLED` (default true) plus a configured `SMTP_HOST`; delivery failures are logged and never abort a signup. Verification stays orthogonal to admin approval.
- v0.4 Frontend: Complete Jinja2 templates, HTMX router, Alpine.js interactivity, CSRF protection, Cookie-based auth, i18n (DE/EN)
- v0.3 Compliance: DSGVO pages + cookie banner, audit logging with CSV export, backup/restore CLI, encryption for sensitive fields
- v0.2 Features: Gallery lifecycle (draft → shared → completed → archived), expiry dates with guest enforcement, watermark customization
- v0.1 API: 27 REST endpoints, image upload/processing, selection event sourcing, share tokens, guest viewer

## [0.1.0] - Initial Release

- Minimal viable Picdrop alternative: photographers share galleries, models select/favorite images
- Self-hosted, zero-trust design: end-to-end encryption option, no external dependencies
- FastAPI backend + PostgreSQL + HTMX+Alpine frontend
- 181 tests (165 unit/security + 16 integration), CI/CD ready
