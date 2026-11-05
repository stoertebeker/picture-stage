"""Production transport validation and proxy-independent cookie policy."""

import pytest
from pydantic import ValidationError

from app.config import Settings


def _settings(app_url: str, environment: str = "production") -> Settings:
    return Settings(
        _env_file=None,
        app_url=app_url,
        environment=environment,
        secret_key="unit-test-secret",
        hmac_secret_key="unit-test-hmac-secret",
    )


@pytest.mark.parametrize("environment", ["development", "test", " DEVELOPMENT ", " Test "])
def test_explicit_nonproduction_allows_http(environment: str) -> None:
    config = _settings("http://localhost:8000", environment)
    assert config.cookie_secure is False
    assert config.environment == environment.strip().lower()


@pytest.mark.parametrize("environment", ["production", "development", "test", " Production "])
@pytest.mark.parametrize(
    "app_url",
    [
        "https://photos.example.com",
        "HTTPS://Photos.Example.COM",
        "https://photos.example.com:8443/photos",
        "https://photos.example.com:443/photos/",
        "https://[2001:db8::1]:8443/photos",
        "https://127.0.0.1/photos",
        "https://photos.example.com/photos%20and%20more",
    ],
)
def test_https_remains_secure_without_rewriting_url(app_url: str, environment: str) -> None:
    config = _settings(app_url, environment)
    assert config.cookie_secure is True
    assert type(config.app_url) is str
    assert config.app_url == app_url


@pytest.mark.parametrize(
    "app_url",
    [
        "http://photos.example.com",
        "",
        "photos.example.com",
        "//photos.example.com",
        "/photos",
        "https:photos.example.com",
        "https:/photos.example.com",
        "https:///photos.example.com",
        "https:////photos.example.com",
        "https://",
        "https:///",
        "https://:443",
        "ftp://photos.example.com",
        "https://user@photos.example.com",
        "https://user:password@photos.example.com",
        "https://@photos.example.com",
        "https://photos.example.com?query=value",
        "https://photos.example.com?",
        "https://photos.example.com#fragment",
        "https://photos.example.com#",
        "https://photos.example.com\\evil",
        "https://photos.example.com/path\\evil",
        " https://photos.example.com",
        "https://photos.example.com ",
        "https://photos.example.com/path with space",
        "https://photo s.example.com",
        "https://photos.example.com\n",
        "https://photos.example.com/\rpath",
        "https://pho\ttos.example.com",
        "https://photos.example.com/\x00path",
        "https://photos.example.com/\x7fpath",
        "https://photos.example.com/\x85path",
        "https://photos.example.com:\n443",
        "https://photos.example.com:",
        "https://photos.example.com:abc",
        "https://photos.example.com:-1",
        "https://photos.example.com:65536",
        "https://[not-an-ipv6-address]",
        "https://[::1",
        "https://photos%20example.com",
    ],
)
def test_production_rejects_unsafe_public_url(app_url: str) -> None:
    with pytest.raises(ValidationError, match="APP_URL"):
        _settings(app_url)


@pytest.mark.parametrize("environment", ["", " ", "prod", "staging", "testing", "production-typo"])
def test_unknown_environment_fails_closed(environment: str) -> None:
    with pytest.raises(ValidationError, match="ENVIRONMENT"):
        _settings("https://photos.example.com", environment)


def test_environment_defaults_to_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    config = Settings(
        _env_file=None,
        app_url="https://photos.example.com",
        secret_key="unit-test-secret",
        hmac_secret_key="unit-test-hmac-secret",
    )
    assert config.environment == "production"
    assert config.cookie_secure is True


def test_normalized_production_still_rejects_default_secrets() -> None:
    with pytest.raises(ValidationError, match="Insecure default secret"):
        Settings(
            _env_file=None,
            environment=" Production ",
            app_url="https://photos.example.com",
            secret_key="CHANGE_ME",
            hmac_secret_key="CHANGE_ME",
        )


@pytest.mark.parametrize("environment", ["production", "unknown", "", " Production "])
@pytest.mark.parametrize("app_url", ["http://photos.example.com", "", "https:broken"])
def test_cookie_policy_stays_secure_after_unsafe_settings_mutation(environment: str, app_url: str) -> None:
    config = _settings("https://photos.example.com")
    config.environment = environment
    config.app_url = app_url
    assert config.cookie_secure is True


@pytest.mark.parametrize("app_url", ["", "https:broken", "ftp://photos.example.com"])
def test_nonproduction_unknown_scheme_does_not_disable_secure(app_url: str) -> None:
    config = _settings("http://localhost:8000", "development")
    config.app_url = app_url
    assert config.cookie_secure is True
