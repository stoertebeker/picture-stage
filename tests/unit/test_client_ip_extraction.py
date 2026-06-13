"""Unit tests for get_client_ip() helper (picture-stage-1qa)."""

from unittest.mock import MagicMock

from app.auth.utils import get_client_ip


def _make_request(headers: dict[str, str], client_host: str | None = "127.0.0.1") -> MagicMock:
    req = MagicMock()
    req.headers = headers
    if client_host is not None:
        req.client = MagicMock()
        req.client.host = client_host
    else:
        req.client = None
    return req


def test_cf_connecting_ip_takes_priority():
    req = _make_request(
        {"CF-Connecting-IP": "1.2.3.4", "X-Forwarded-For": "9.9.9.9, 10.0.0.1"},
        client_host="172.16.0.1",
    )
    assert get_client_ip(req) == "1.2.3.4"


def test_x_forwarded_for_first_ip_used_as_fallback():
    req = _make_request(
        {"X-Forwarded-For": "5.6.7.8, 10.0.0.1"},
        client_host="172.16.0.1",
    )
    assert get_client_ip(req) == "5.6.7.8"


def test_x_forwarded_for_strips_whitespace():
    req = _make_request({"X-Forwarded-For": "  5.6.7.8 , 10.0.0.1"})
    assert get_client_ip(req) == "5.6.7.8"


def test_falls_back_to_client_host():
    req = _make_request({}, client_host="192.168.1.1")
    assert get_client_ip(req) == "192.168.1.1"


def test_returns_none_when_no_client():
    req = _make_request({}, client_host=None)
    assert get_client_ip(req) is None


def test_cf_header_stripped_of_whitespace():
    req = _make_request({"CF-Connecting-IP": "  1.2.3.4  "})
    assert get_client_ip(req) == "1.2.3.4"
