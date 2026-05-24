from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Picture-Stage"
    app_url: str = "http://localhost:8000"
    secret_key: str = "CHANGE_ME"
    debug: bool = False

    database_url: str = "postgresql+asyncpg://picstage:picstage@db:5432/picstage"

    storage_backend: str = "local"
    upload_dir: str = "/app/uploads"

    s3_endpoint_url: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_bucket_name: str = ""
    s3_region: str = ""

    hmac_secret_key: str = "CHANGE_ME"

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "noreply@example.com"
    smtp_starttls: bool = True

    watermark_text: str = "PREVIEW"
    watermark_opacity: int = 80
    watermark_font_size_ratio: float = 0.05

    ratelimit_enabled: bool = True

    admin_email: str = ""
    admin_password: str = ""

    captcha_enabled: bool = True
    altcha_secret_key: str = ""


settings = Settings()
