from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Sentinel default for crypto secrets. A production instance MUST override these
# via environment; the validator below refuses to start otherwise.
_INSECURE_SECRET_DEFAULT = "CHANGE_ME"  # noqa: S105


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Picture-Stage"
    app_url: str = "http://localhost:8000"
    # Secure-by-default: production unless explicitly overridden (e.g. ENVIRONMENT=development).
    # A forgotten override fails loud in dev/CI rather than silently shipping insecure prod.
    environment: str = "production"
    secret_key: str = _INSECURE_SECRET_DEFAULT
    debug: bool = False

    database_url: str = "postgresql+asyncpg://picstage:picstage@db:5432/picstage"

    storage_backend: str = "local"
    upload_dir: str = "/app/uploads"

    s3_endpoint_url: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_bucket_name: str = ""
    s3_region: str = ""

    hmac_secret_key: str = "CHANGE_ME"  # noqa: S105

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "noreply@example.com"
    smtp_starttls: bool = True

    # System notification: email all admins when a new user registers. Unlike the
    # per-user NotificationConfig opt-in, this is a guaranteed operational alert
    # sent directly to every admin's address. Requires smtp_host to be set.
    notify_admins_on_signup: bool = True

    # Send a verification email to the registrant on signup. Config-free (no
    # NotificationConfig opt-in), gated only by this flag and a configured
    # smtp_host. The link carries the plaintext token (DB stores it hashed).
    send_verification_email_enabled: bool = True

    # Email the gallery owner (photographer) when a model completes their review.
    # Config-free like the alerts above (bypasses the per-user NotificationConfig,
    # for which there is no UI); gated only by this flag and a configured smtp_host.
    notify_owner_on_completion: bool = True

    watermark_text: str = "PREVIEW · {gallery_id}"
    watermark_position: str = "bottom-right"
    watermark_opacity: float = 0.3
    watermark_font_size: int = 0  # 0 = use watermark_font_size_ratio instead
    watermark_font_size_ratio: float = 0.05

    # Maximum number of galleries a single user may own. Enforced only at
    # creation time (never deletes or locks existing galleries), so raising or
    # lowering it cannot strand a user — someone already above a lowered limit
    # simply cannot create more until they drop below it. 0 = unlimited.
    max_galleries_per_user: int = 5

    # Upload input limits (picture-stage-fbq). Both are read per request from
    # settings, so changing them in .env and restarting the container applies to
    # ALL uploads — including existing galleries — because the limit is enforced
    # at upload time, not stored per gallery. Per-file size is the primary guard
    # against OOM; the file-count cap bounds a single multipart request. 0 = off.
    max_upload_file_mb: int = 50
    max_files_per_upload: int = 500

    # Decompression-bomb guard (picture-stage-ccx). Caps the decoded pixel count
    # Pillow will process before raising DecompressionBombError instead of
    # allocating hundreds of MB of RAM. Applied module-wide in
    # app/images/processing.py, so it covers every decode path. The default
    # (100 MP) sits far above real photos (~10-50 MP) yet well below the RAM a
    # crafted image could otherwise demand. 0 = no cap (Pillow default ~178 MP).
    max_image_pixels: int = 100_000_000

    ratelimit_enabled: bool = True

    captcha_enabled: bool = True
    altcha_secret_key: str = ""

    legal_impressum_path: str = "/data/legal/impressum.md"
    legal_datenschutz_path: str = "/data/legal/datenschutz.md"

    # Cache-busting token appended as ?v=<asset_version> to static asset URLs
    # (JS/CSS). Set to a per-build value (build timestamp) via the ASSET_VERSION
    # env in the Dockerfile so every image ships a fresh query string — busting
    # GHA-layer, origin-image and CDN caches at once (the u3s.7 deploy trap).
    # The static default still varies per release for local/dev builds.
    asset_version: str = "0.1.0"

    @property
    def cookie_secure(self) -> bool:
        """Whether to set the Secure flag on cookies and emit the HSTS header.

        Derived from the operator-configured public ``APP_URL`` rather than the
        request scheme (picture-stage-8ox): behind a TLS-terminating proxy the
        container only sees plain HTTP, and the port may be reachable directly,
        so trusting ``X-Forwarded-Proto`` would be spoofable and proxy-specific.
        ``APP_URL`` is the existing source of truth for the public scheme (see
        ``build_share_url`` / picture-stage-0hp), and any HTTPS deployment must
        set it to https anyway (else share tokens leak), so the Secure flag
        tracks it automatically. A local http:// deployment yields False, so the
        plain-HTTP dev stack keeps working.
        """
        return self.app_url.lower().startswith("https://")

    @model_validator(mode="after")
    def _reject_default_secrets_in_production(self) -> "Settings":
        """Fail fast if crypto secrets are left at their insecure default in production.

        Default secrets would allow forging JWT access tokens and HMAC image-URL
        signatures (account takeover). Only enforced when ENV=production so that
        development, CI and tests keep working without a populated .env.
        """
        if self.environment.lower() == "production":
            insecure = [
                name
                for name, value in (("SECRET_KEY", self.secret_key), ("HMAC_SECRET_KEY", self.hmac_secret_key))
                if value == _INSECURE_SECRET_DEFAULT
            ]
            if insecure:
                raise ValueError(
                    f"Insecure default secret(s) in production: {', '.join(insecure)}. "
                    "Set them to strong random values, e.g. "
                    'python -c "import secrets; print(secrets.token_urlsafe(64))"'
                )
        return self


settings = Settings()
