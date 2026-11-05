"""Guest browser credentials, deliberately separate from owner authentication.

Only the HttpOnly cookie carries the session bearer. DOM tokens prove CSRF
intent without revealing it. Explicit API credentials never fall back to cookies.
"""

import hashlib
import hmac
import secrets
import time
import uuid

from fastapi import HTTPException, Request
from pydantic import BaseModel
from starlette.responses import Response

from app.config import settings

GUEST_SESSION_TTL = 12 * 60 * 60
SESSION_HEADER = "X-Guest-Session"
CSRF_HEADER = "X-Guest-CSRF-Token"
PRIVATE_HEADERS = {"Cache-Control": "private, no-store"}


def _mac(purpose: str, gallery_id: uuid.UUID, value: str) -> str:
    return hmac.new(
        settings.secret_key.encode(),
        f"picture-stage:guest:{purpose}:v1:{gallery_id}:{value}".encode(),
        hashlib.sha256,
    ).hexdigest()


def cookie_name(gallery_id: uuid.UUID, *, gate: bool = False) -> str:
    return f"guest_{'gate' if gate else 'session'}_{gallery_id.hex}"


def _cookie_path(request: Request, token: str) -> str:
    return f"{request.scope.get('root_path', '').rstrip('/')}/g/{token}"


def _seal(gallery_id: uuid.UUID, value: str, *, gate: bool = False) -> str:
    payload = f"{value}.{int(time.time()) + GUEST_SESSION_TTL}"
    return f"{payload}.{_mac('gate' if gate else 'session', gallery_id, payload)}"


def _open(gallery_id: uuid.UUID, value: str | None, *, gate: bool = False) -> str | None:
    if not value or len(value) > 256:
        return None
    parts = value.split(".")
    if len(parts) != 3:
        return None
    payload = ".".join(parts[:2])
    if not hmac.compare_digest(parts[2].encode(), _mac("gate" if gate else "session", gallery_id, payload).encode()):
        return None
    try:
        if int(parts[1]) <= time.time():
            return None
    except ValueError:
        return None
    return parts[0]


def _set_cookie(
    response: Response, request: Request, token: str, gallery_id: uuid.UUID, value: str, *, gate: bool
) -> None:
    response.set_cookie(
        cookie_name(gallery_id, gate=gate),
        value,
        max_age=GUEST_SESSION_TTL,
        path=_cookie_path(request, token),
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )
    response.headers.update(PRIVATE_HEADERS)


def browser_cookie_session_id(request: Request, gallery_id: uuid.UUID) -> uuid.UUID | None:
    raw_id = _open(gallery_id, request.cookies.get(cookie_name(gallery_id)))
    if raw_id is None:
        return None
    try:
        return uuid.UUID(raw_id)
    except ValueError:
        return None


def browser_session(gallery_id: uuid.UUID, session_id: uuid.UUID, *, request: Request | None = None) -> tuple[str, str]:
    # Reusing the exact envelope keeps proofs in other open tabs valid. Its
    # signed deadline stays fixed; visiting again cannot extend the grant.
    cookie = request.cookies.get(cookie_name(gallery_id), "") if request is not None else ""
    if _open(gallery_id, cookie) != str(session_id):
        cookie = _seal(gallery_id, str(session_id))
    return cookie, _mac("csrf", gallery_id, cookie)


def set_browser_session(response: Response, request: Request, token: str, gallery_id: uuid.UUID, cookie: str) -> None:
    _set_cookie(response, request, token, gallery_id, cookie, gate=False)


def password_challenge(request: Request, gallery_id: uuid.UUID) -> tuple[str, str]:
    cookie = request.cookies.get(cookie_name(gallery_id, gate=True), "")
    if _open(gallery_id, cookie, gate=True) is None:
        cookie = _seal(gallery_id, secrets.token_urlsafe(32), gate=True)
    return cookie, _mac("gate-csrf", gallery_id, cookie)


def set_password_challenge(
    response: Response, request: Request, token: str, gallery_id: uuid.UUID, cookie: str
) -> None:
    _set_cookie(response, request, token, gallery_id, cookie, gate=True)


def verify_password_csrf(request: Request, gallery_id: uuid.UUID, submitted: str | None) -> None:
    cookie = request.cookies.get(cookie_name(gallery_id, gate=True))
    if (
        _open(gallery_id, cookie, gate=True) is None
        or not submitted
        or not hmac.compare_digest(submitted.encode(), _mac("gate-csrf", gallery_id, cookie or "").encode())
    ):
        raise HTTPException(status_code=403, detail="Guest CSRF token mismatch", headers=PRIVATE_HEADERS)


def request_session_id(request: Request, gallery_id: uuid.UUID, *, body: BaseModel | None = None) -> uuid.UUID | None:
    """Choose an explicit bearer or the cookie exactly once, before DB checks.

    Body presence matters: null is an invalid explicit credential, never a
    request to use ambient authentication. Conflicting sources also fail closed.
    Old query-string credentials are intentionally ignored.
    """
    body_present = body is not None and "session_id" in body.model_fields_set
    header_present = SESSION_HEADER.lower() in request.headers
    if body_present or header_present:
        body_id = getattr(body, "session_id", None) if body_present else None
        header_id = None
        if header_present:
            try:
                header_id = uuid.UUID(request.headers[SESSION_HEADER])
            except ValueError as exc:
                raise HTTPException(
                    status_code=403, detail="Invalid guest credential", headers=PRIVATE_HEADERS
                ) from exc
        if (body_present and body_id is None) or (body_present and header_present and body_id != header_id):
            raise HTTPException(status_code=403, detail="Invalid guest credential", headers=PRIVATE_HEADERS)
        return body_id if body_present else header_id

    cookie = request.cookies.get(cookie_name(gallery_id))
    session_id = browser_cookie_session_id(request, gallery_id)
    if session_id is None:
        return None
    if body is not None:
        submitted = request.headers.get(CSRF_HEADER)
        if not submitted or not hmac.compare_digest(
            submitted.encode(), _mac("csrf", gallery_id, cookie or "").encode()
        ):
            raise HTTPException(status_code=403, detail="Guest CSRF token mismatch", headers=PRIVATE_HEADERS)
    return session_id


async def private_guest_response(response: Response) -> None:
    """Mark every successful guest representation private, including API data."""
    response.headers.update(PRIVATE_HEADERS)
