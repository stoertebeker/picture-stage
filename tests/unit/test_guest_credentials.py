"""Browser credentials never expose the bearer or silently change auth source."""

import uuid

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response

from app.config import settings
from app.guest import credentials as creds
from app.guest.router import CompleteReviewRequest


def _request(gallery_id, cookie=None, headers=None, *, root_path=""):
    headers = dict(headers or {})
    if cookie is not None:
        headers["cookie"] = f"{creds.cookie_name(gallery_id)}={cookie}"
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/g/share/complete",
            "root_path": root_path,
            "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()],
        }
    )


def test_cookie_and_csrf_are_independently_bound_to_the_gallery():
    gallery_id, session_id = uuid.uuid4(), uuid.uuid4()
    cookie, csrf = creds.browser_session(gallery_id, session_id)
    assert str(session_id) not in csrf
    assert creds.request_session_id(_request(gallery_id, cookie), gallery_id) == session_id
    foreign_id = uuid.uuid4()
    assert creds.request_session_id(_request(foreign_id, cookie), foreign_id) is None
    assert (
        creds.request_session_id(
            _request(gallery_id, cookie, {creds.CSRF_HEADER: csrf}), gallery_id, body=CompleteReviewRequest()
        )
        == session_id
    )


@pytest.mark.parametrize("submitted", [None, "", "wrong", "ümlaut"])
def test_cookie_mutation_requires_its_own_csrf_even_with_owner_header(submitted):
    gallery_id = uuid.uuid4()
    cookie, csrf = creds.browser_session(gallery_id, uuid.uuid4())
    headers = {"X-CSRF-Token": csrf, "Authorization": "Bearer unrelated-owner", "Content-Type": "application/json"}
    if submitted is not None:
        headers[creds.CSRF_HEADER] = submitted
    with pytest.raises(HTTPException, match="Guest CSRF"):
        creds.request_session_id(_request(gallery_id, cookie, headers), gallery_id, body=CompleteReviewRequest())


@pytest.mark.parametrize(
    "headers,body",
    [
        ({creds.SESSION_HEADER: ""}, {}),
        ({creds.SESSION_HEADER: "null"}, {}),
        ({creds.SESSION_HEADER: "garbage"}, {}),
        ({}, {"session_id": None}),
        ({creds.SESSION_HEADER: str(uuid.uuid4())}, {"session_id": None}),
        ({creds.SESSION_HEADER: str(uuid.uuid4())}, {"session_id": str(uuid.uuid4())}),
    ],
)
def test_invalid_explicit_credentials_never_fall_back_to_valid_cookie(headers, body):
    gallery_id = uuid.uuid4()
    cookie, csrf = creds.browser_session(gallery_id, uuid.uuid4())
    with pytest.raises(HTTPException, match="Invalid guest credential"):
        creds.request_session_id(
            _request(gallery_id, cookie, {**headers, creds.CSRF_HEADER: csrf}),
            gallery_id,
            body=CompleteReviewRequest.model_validate(body),
        )


@pytest.mark.parametrize("source", ["body", "header", "both"])
def test_explicit_api_bearers_do_not_need_ambient_csrf(source):
    gallery_id, session_id = uuid.uuid4(), uuid.uuid4()
    body = CompleteReviewRequest(session_id=session_id) if source != "header" else CompleteReviewRequest()
    headers = {creds.SESSION_HEADER: str(session_id)} if source != "body" else {}
    assert creds.request_session_id(_request(gallery_id, "bad-cookie", headers), gallery_id, body=body) == session_id


def test_server_enforces_expiration_and_integrity(monkeypatch):
    gallery_id = uuid.uuid4()
    monkeypatch.setattr(creds.time, "time", lambda: 100)
    cookie, csrf = creds.browser_session(gallery_id, uuid.uuid4())
    assert creds.request_session_id(_request(gallery_id, cookie + "x"), gallery_id) is None
    monkeypatch.setattr(creds.time, "time", lambda: 100 + creds.GUEST_SESSION_TTL)
    assert creds.request_session_id(_request(gallery_id, cookie, {creds.CSRF_HEADER: csrf}), gallery_id) is None


@pytest.mark.parametrize("app_url,secure", [("https://example.test", True), ("http://localhost", False)])
def test_cookie_is_host_only_httponly_and_scoped_under_mount(monkeypatch, app_url, secure):
    monkeypatch.setattr(settings, "app_url", app_url)
    response = Response()
    gallery_id = uuid.uuid4()
    cookie, _csrf = creds.browser_session(gallery_id, uuid.uuid4())
    creds.set_browser_session(response, _request(gallery_id, root_path="/proofs"), "share", gallery_id, cookie)
    header = response.headers["set-cookie"]
    assert "HttpOnly" in header and "SameSite=lax" in header
    assert "Path=/proofs/g/share" in header and "Domain=" not in header
    assert ("Secure" in header) is secure
    assert response.headers["cache-control"] == "private, no-store"


def test_non_ascii_cookie_signature_and_password_csrf_fail_closed():
    gallery_id = uuid.uuid4()
    cookie, _csrf = creds.browser_session(gallery_id, uuid.uuid4())
    assert creds._open(gallery_id, cookie.rsplit(".", 1)[0] + ".ümlaut") is None
    challenge, _csrf = creds.password_challenge(_request(gallery_id), gallery_id)
    request = Request(
        {"type": "http", "headers": [(b"cookie", f"{creds.cookie_name(gallery_id, gate=True)}={challenge}".encode())]}
    )
    with pytest.raises(HTTPException):
        creds.verify_password_csrf(request, gallery_id, "ümlaut")


def test_reusing_a_browser_visit_keeps_its_exact_cookie_csrf_and_deadline(monkeypatch):
    gallery_id, session_id = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(creds.time, "time", lambda: 100)
    cookie, csrf = creds.browser_session(gallery_id, session_id)
    request = _request(gallery_id, cookie)
    monkeypatch.setattr(creds.time, "time", lambda: 200)
    assert creds.browser_session(gallery_id, session_id, request=request) == (cookie, csrf)
    assert creds.browser_cookie_session_id(request, gallery_id) == session_id
    replacement, replacement_csrf = creds.browser_session(gallery_id, uuid.uuid4(), request=request)
    assert replacement != cookie and replacement_csrf != csrf
    # Keeping an expired cookie is forbidden even when its DB visit still exists.
    monkeypatch.setattr(creds.time, "time", lambda: 100 + creds.GUEST_SESSION_TTL)
    assert creds.browser_cookie_session_id(request, gallery_id) is None
    assert creds.browser_session(gallery_id, session_id, request=request) != (cookie, csrf)
