"""Unit tests for build_share_url — share-token leak protection (picture-stage-0hp).

The share token is replayable and travels in the URL. Behind a TLS-terminating
proxy the container only sees plain HTTP, so the public URL must come from the
configured APP_URL and must never be http:// in production.
"""

from types import SimpleNamespace

import pytest

from app.config import settings
from app.galleries.sharing import build_share_url

TOKEN = "tok-abc123"


def _request(base_url: str) -> SimpleNamespace:
    """Minimal Request stub — build_share_url only reads request.base_url."""
    return SimpleNamespace(base_url=base_url)


def test_uses_app_url_over_request_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """APP_URL is the source of truth, not the (proxy-internal) request scheme."""
    monkeypatch.setattr(settings, "app_url", "https://picture.example.com")
    monkeypatch.setattr(settings, "environment", "production")

    url = build_share_url(_request("http://internal-container:8000/"), TOKEN)

    assert url == "https://picture.example.com/g/" + TOKEN


def test_forces_https_in_production_even_if_app_url_is_http(monkeypatch: pytest.MonkeyPatch) -> None:
    """Defense-in-depth: a misconfigured http APP_URL must not leak the token."""
    monkeypatch.setattr(settings, "app_url", "http://picture.example.com")
    monkeypatch.setattr(settings, "environment", "production")

    url = build_share_url(_request("http://internal-container:8000/"), TOKEN)

    assert url == "https://picture.example.com/g/" + TOKEN


def test_falls_back_to_request_base_url_and_still_forces_https(monkeypatch: pytest.MonkeyPatch) -> None:
    """If APP_URL is empty, the request base_url is used but https is enforced in prod."""
    monkeypatch.setattr(settings, "app_url", "")
    monkeypatch.setattr(settings, "environment", "production")

    url = build_share_url(_request("http://internal-container:8000/"), TOKEN)

    assert url == "https://internal-container:8000/g/" + TOKEN


def test_allows_http_in_non_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """Local dev / tests keep http://localhost so the link is reachable."""
    monkeypatch.setattr(settings, "app_url", "http://localhost:8000")
    monkeypatch.setattr(settings, "environment", "development")

    url = build_share_url(_request("http://testserver/"), TOKEN)

    assert url == "http://localhost:8000/g/" + TOKEN
