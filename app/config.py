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

    watermark_text: str = "PREVIEW · {gallery_id}"
    watermark_position: str = "bottom-right"
    watermark_opacity: float = 0.3
    watermark_font_size: int = 0  # 0 = use watermark_font_size_ratio instead
    watermark_font_size_ratio: float = 0.05

    ratelimit_enabled: bool = True

    captcha_enabled: bool = True
    altcha_secret_key: str = ""

    legal_impressum_path: str = "/data/legal/impressum.md"
    legal_datenschutz_path: str = "/data/legal/datenschutz.md"

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
