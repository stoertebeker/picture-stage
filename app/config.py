from urllib.parse import urlsplit

from pydantic import Field, HttpUrl, SecretStr, field_validator, model_validator
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
    # Operator-held capability used only for the first administrator setup.
    bootstrap_token: SecretStr = SecretStr("")
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

    # Shared originals and derivatives, including durable failed-write claims.
    # 10 GiB accommodates photo galleries; 40,000 objects bounds small-file and
    # queued-original growth independently (about 10,000 complete images).
    max_storage_bytes_per_owner: int = Field(default=10 * 1024**3, ge=1, le=2**63 - 1)
    max_storage_objects_per_owner: int = Field(default=40_000, ge=1, le=2**63 - 1)

    # Global request-body size limit (picture-stage-m4ct). Defense-in-depth in
    # front of the per-file/count guards above: Starlette spools the whole
    # multipart body to a temp file before the in-handler checks run, so a huge
    # body could fill disk first. RequestBodySizeLimitMiddleware rejects an
    # over-sized body with 413 at the ASGI layer — via the declared
    # Content-Length before any byte is read, and by counting received bytes to
    # also catch a missing/lying Content-Length (chunked). The default (1024 MB)
    # sits well above any legitimate multi-upload request yet caps absurd bodies.
    # An outer reverse-proxy limit (Caddy request_body / Cloudflare) is the
    # recommended additional layer. 0 = no app-level limit.
    max_request_body_mb: int = 1024

    # Ordinary JSON/forms must not inherit the upload-sized body budget. This
    # additional ceiling is enforced before routing, CSRF and request parsing.
    # Only POSTs to the two image upload routes use the larger global budget.
    # 0 disables this additional ceiling; the global ceiling still applies.
    max_non_upload_request_body_kb: int = Field(default=1024, ge=0)

    # Per-gallery event log cap and per-comment character limit. Existing
    # history is retained even when a limit is lowered.
    max_selection_events_per_gallery: int = Field(default=20000, ge=1)
    max_selection_comment_chars: int = Field(default=2000, ge=1)

    # All retained visits count, including historical/completed sessions. Never
    # unlimited: cookie deletion or changing IPs must not bypass admission.
    max_share_sessions_per_gallery: int = Field(default=1000, ge=1, le=2**31 - 1)
    share_session_ttl_seconds: int = Field(default=43200, ge=1, le=2**31 - 1)

    # Decompression-bomb guard (picture-stage-ccx). Caps the decoded pixel count
    # Pillow will process before raising DecompressionBombError instead of
    # allocating hundreds of MB of RAM. Applied module-wide in
    # app/images/processing.py, so it covers every decode path. The default
    # (100 MP) sits far above real photos (~10-50 MP) yet well below the RAM a
    # crafted image could otherwise demand. 0 = no cap (Pillow default ~178 MP).
    max_image_pixels: int = 100_000_000

    # Preview-worker process isolation (picture-stage-q9td). Pillow processing
    # runs in a pebble ProcessPool so a per-file timeout can hard-kill a hung
    # worker process (a plain asyncio.wait_for around to_thread cannot — the
    # thread keeps running). The pixel cap (max_image_pixels) prevents OOM; this
    # bounds wall-clock time for a degenerate-but-under-cap image. Workers run
    # inside the app process, so with multiple uvicorn workers the total child
    # count is (uvicorn workers x image_processing_workers) — keep it small.
    # timeout 0 = no timeout; workers must be >= 1.
    image_processing_timeout_seconds: int = 60
    image_processing_workers: int = 2

    # Root/app log level (picture-stage-vblf). Without a central logging config the
    # app loggers fall back to Python's lastResort handler (WARNING threshold), so
    # INFO diagnostics from the background workers etc. are invisible in the
    # container log. configure_logging() (app/logging_config.py) wires this up.
    log_level: str = "INFO"

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
        Production startup requires a valid HTTPS ``APP_URL``. Fail closed even
        if settings are later mutated without validation: only an explicitly
        selected development/test environment with an HTTP URL may disable
        Secure cookies and HSTS.
        """
        return self.environment not in {"development", "test"} or not self.app_url.lower().startswith("http://")

    @field_validator("environment")
    @classmethod
    def _validate_environment(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"production", "development", "test"}:
            raise ValueError("ENVIRONMENT must be production, development, or test")
        return normalized

    @model_validator(mode="after")
    def _require_https_in_production(self) -> "Settings":
        if self.environment != "production":
            return self

        error = "APP_URL must be an absolute HTTPS base URL without credentials, query, fragment, or whitespace"
        # Both parsers normalize some malformed input (e.g. embedded newlines),
        # and HttpUrl repairs missing slashes. Check the original spelling first
        # and retain that string: callers append paths to this configured base.
        if (
            not self.app_url.lower().startswith("https://")
            or any(char.isspace() or ord(char) < 32 or 127 <= ord(char) <= 159 for char in self.app_url)
            or any(char in self.app_url for char in ("\\", "?", "#"))
        ):
            raise ValueError(error)
        try:
            parts = urlsplit(self.app_url)
            if not parts.netloc or not parts.hostname or "@" in parts.netloc or parts.netloc.endswith(":"):
                raise ValueError(error)
            # Validate port syntax/range and host/URL validity using the standard
            # parsers; neither parser's normalized representation replaces APP_URL.
            _ = parts.port
            HttpUrl(self.app_url)
        except ValueError as exc:
            raise ValueError(error) from exc
        return self

    @model_validator(mode="after")
    def _reject_default_secrets_in_production(self) -> "Settings":
        """Fail fast if crypto secrets are left at their insecure default in production.

        Default secrets would allow forging JWT access tokens and HMAC image-URL
        signatures (account takeover). Only enforced when ENVIRONMENT=production so that
        development, CI and tests keep working without a populated .env.
        """
        if self.environment == "production":
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
