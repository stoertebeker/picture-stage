"""Guest language negotiation and unchanged photographer/admin preferences (GitHub #5)."""

from types import SimpleNamespace

import pytest
from starlette.requests import Request

from app.i18n import detect_locale


def _request(path="/g/token", language="", cookie=None, user_locale=None):
    headers = [(b"accept-language", language.encode())]
    if cookie is not None:
        headers.append((b"cookie", f"lang={cookie}".encode()))
    request = Request({"type": "http", "path": path, "headers": headers})
    if user_locale is not None:
        request.state.user = SimpleNamespace(locale=user_locale)
    return request


@pytest.mark.parametrize(
    ("language", "locale"),
    [
        ("de-DE,de;q=0.9,en;q=0.8", "de"),
        ("en-US,en;q=0.9", "en"),
        ("fr-FR,fr;q=0.9", "en"),
        ("ja-JP", "en"),
        ("", "en"),
        ("*", "en"),
        ("de;q=0.2,en;q=0.9", "en"),
        ("en;q=0.2,de-AT;q=0.9", "de"),
        ("fr-FR,de;q=0.8", "de"),
        ("de;q=0,en;q=0.8", "en"),
        ("en;q=0,de;q=0.8", "de"),
        ("de;q=0,en;q=0", "en"),
        ("de,en", "de"),
        ("en,de", "en"),
        ("DE-ch ; Q = 0.8 , en ; q=0.2", "de"),
    ],
)
def test_guest_uses_weighted_supported_browser_preferences(language, locale):
    assert detect_locale(_request(language=language)) == locale


@pytest.mark.parametrize("weight", ["NaN", "Inf", "-Inf", "-1", "1.1", "invalid", ""])
@pytest.mark.parametrize(("rejected", "fallback"), [("de", "en"), ("en", "de")])
def test_guest_ignores_invalid_language_weights(weight, rejected, fallback):
    request = _request(language=f"{rejected};q={weight},{fallback};q=0.5")
    assert detect_locale(request) == fallback


@pytest.mark.parametrize(("cookie", "language", "locale"), [("de", "en-US", "de"), ("en", "de-DE", "en")])
def test_explicit_guest_choice_overrides_browser(cookie, language, locale):
    assert detect_locale(_request(cookie=cookie, language=language)) == locale


def test_invalid_guest_cookie_does_not_suppress_browser_detection():
    assert detect_locale(_request(cookie="fr", language="en-US")) == "en"


@pytest.mark.parametrize("suffix", ["", "/verify-password", "/gallery-images", "/selections", "/complete"])
def test_all_guest_requests_share_the_international_fallback(suffix):
    assert detect_locale(_request(path=f"/g/token{suffix}", language="fr-FR")) == "en"


def test_guest_preference_is_independent_of_a_photographer_profile():
    assert detect_locale(_request(language="en-US", user_locale="de")) == "en"


@pytest.mark.parametrize("path", ["/dashboard", "/galleries/token", "/admin/users", "/login", "/api/v1/galleries"])
def test_non_guest_detection_retains_existing_preferences(path):
    assert detect_locale(_request(path=path, language="fr-FR")) == "de"
    assert detect_locale(_request(path=path, language="en-US")) == "en"
    assert detect_locale(_request(path=path, cookie="en", language="de-DE")) == "en"
    assert detect_locale(_request(path=path, user_locale="de", cookie="en", language="en-US")) == "de"
    # Existing non-guest header handling is intentionally unchanged by this ticket.
    assert detect_locale(_request(path=path, language="de;q=0.2,en;q=0.9")) == "de"
