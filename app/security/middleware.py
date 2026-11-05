import base64
import hashlib
import hmac
import secrets
import uuid
from typing import ClassVar
from urllib.parse import parse_qs

from starlette._utils import get_route_path
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import ClientDisconnect, Request
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import settings


def _csrf_recovery_path(path: str) -> str:
    """Return a known GET route, never a POST-only URL or external Referer."""
    parts = path.split("/")
    if len(parts) >= 3 and parts[1] == "galleries":
        try:
            gallery_id = uuid.UUID(parts[2])
        except ValueError:
            pass
        else:
            return f"/galleries/{gallery_id}"
    if path in {"/login", "/signup"}:
        return path
    return "/dashboard"


def _mint_csrf_token(session_token: str | None) -> str:
    """Create a CSRF token, stable across concurrent GETs for one session.

    A returning authenticated browser can legitimately have a session cookie
    while its shorter-lived/readable CSRF cookie is missing. Multiple safe
    requests may then race (for example the gallery navigation and a dashboard
    HTMX request). Deriving the replacement from the opaque session makes every
    one of those responses render and set the same token. The domain separator
    keeps this HMAC independent from other uses of ``SECRET_KEY``.
    """
    if not session_token:
        return secrets.token_urlsafe(32)
    digest = hmac.new(
        settings.secret_key.encode(),
        b"picture-stage:csrf:v1\0" + session_token.encode(),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class CSRFMiddleware(BaseHTTPMiddleware):
    """Double-submit cookie CSRF protection.

    Skips Bearer API routes (/api/) and token-in-URL guest routes (/g/).
    API logout mutates a browser cookie and needs the same proof as frontend logout.
    """

    SAFE_METHODS: ClassVar[set[str]] = {"GET", "HEAD", "OPTIONS", "TRACE"}
    EXEMPT_PREFIXES: ClassVar[tuple[str, ...]] = ("/api/", "/g/")

    @staticmethod
    def _reject(request: Request, message: str) -> Response:
        # Old pages may still run the pre-rollout script and cannot repair
        # their stale token themselves. HTMX handles this header before its
        # error swap. Navigate to a known GET route: location.reload() could
        # repeat a prior native POST that originally rendered the document.
        headers = (
            {"HX-Redirect": _csrf_recovery_path(request.url.path)}
            if request.headers.get("HX-Request") == "true"
            else None
        )
        return Response(message, status_code=403, headers=headers)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Match the router's decoded, root-relative path, including its redirect
        # of any trailing slashes. A raw /api/ exemption can otherwise hide the
        # cookie-mutating logout route behind an API-shaped deployment prefix.
        path = get_route_path(request.scope)

        # Logout deletes a cookie even when no cookies or Bearer header arrive.
        # Other API routes retain their non-ambient authentication contract.
        cookie_logout = path.rstrip("/") == "/api/v1/auth/logout"
        if not cookie_logout and any(path.startswith(prefix) for prefix in self.EXEMPT_PREFIXES):
            return await call_next(request)

        if request.method in self.SAFE_METHODS:
            session_token = request.cookies.get("session")
            csrf_token = request.cookies.get("csrf_token")
            issue_cookie = False
            if request.method == "GET" and session_token:
                # Authenticated requests use a signed, session-bound token. In
                # addition to preventing cookie injection, this gives every
                # concurrent GET for the same session the same replacement.
                expected_token = _mint_csrf_token(session_token)
                issue_cookie = not csrf_token or not hmac.compare_digest(csrf_token, expected_token)
                csrf_token = expected_token
            elif request.method == "GET" and not csrf_token:
                # Generate before rendering so the response body and the cookie
                # carry the same token. Generating only after call_next left the
                # first rendered form with an empty/stale token, making its very
                # first POST fail with a 403 CSRF mismatch.
                csrf_token = _mint_csrf_token(None)
                issue_cookie = True
            if csrf_token:
                request.state.csrf_token = csrf_token

            response = await call_next(request)
            # Set CSRF cookie on GET requests if not already present
            if issue_cookie and csrf_token is not None:
                response.set_cookie(
                    key="csrf_token",
                    value=csrf_token,
                    httponly=False,
                    secure=settings.cookie_secure,
                    samesite="lax",
                    path="/",
                )
            return response

        # Unsafe method — verify CSRF token
        cookie_token = request.cookies.get("csrf_token")
        if not cookie_token:
            return self._reject(request, "CSRF token missing")
        request.state.csrf_token = cookie_token

        session_token = request.cookies.get("session")
        if session_token and not hmac.compare_digest(cookie_token, _mint_csrf_token(session_token)):
            return self._reject(request, "CSRF token mismatch")

        # Check header first, then form body (without consuming request.form())
        header_token = request.headers.get("X-CSRF-Token")
        form_token = None

        if not header_token and request.headers.get("content-type", "").startswith("application/x-www-form-urlencoded"):
            body = await request.body()
            params = parse_qs(body.decode("utf-8", errors="replace"))
            form_token = params.get("csrf_token", [None])[0]

        submitted_token = header_token or form_token

        if not submitted_token or not hmac.compare_digest(submitted_token, cookie_token):
            return self._reject(request, "CSRF token mismatch")

        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)

        if request.cookies.get("session") and response.headers.get("content-type", "").startswith("text/html"):
            # Revalidate authenticated documents and fragments after cookie or
            # session rotation; retain a stronger endpoint-specific no-store.
            no_store = "no-store" in response.headers.get("cache-control", "").lower()
            response.headers["Cache-Control"] = "private, no-cache" + (", no-store" if no_store else "")

        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-XSS-Protection"] = "0"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"

        csp_directives = [
            "default-src 'self'",
            "img-src 'self' blob: data:",
            "style-src 'self' 'unsafe-inline'",
            # No 'unsafe-eval': the @alpinejs/csp build evaluates expressions
            # without the Function constructor (all inline expressions migrated
            # to Alpine.data() registrations + delegated listeners in u3s).
            "script-src 'self'",
            "frame-ancestors 'none'",
            "base-uri 'self'",
            "form-action 'self'",
        ]
        response.headers["Content-Security-Policy"] = "; ".join(csp_directives)

        # Tie HSTS to the configured public scheme, not the (proxy-stripped or
        # spoofable) request scheme — see settings.cookie_secure (picture-stage-8ox).
        if settings.cookie_secure:
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"

        return response


class RequestBodySizeLimitMiddleware:
    """Reject an over-sized request body with 413 before it is fully spooled (picture-stage-m4ct).

    Defense-in-depth in front of the in-handler upload guards (picture-stage-fbq):
    Starlette spools the whole multipart body to a temp file *before* the handler's
    per-file/count checks run, so a huge body could fill disk first. This is a pure
    ASGI middleware (not ``BaseHTTPMiddleware``, which buffers the body itself and
    mishandles exceptions from the receive stream) and caps the body in two stages:

    1. Fast path — a declared ``Content-Length`` over the limit is rejected before
       any byte is read.
    2. Robust path — the received bytes are counted, so a missing or lying
       ``Content-Length`` (e.g. ``Transfer-Encoding: chunked``) is caught mid-stream.

    Registered outermost, before CSRF and parsers. The effective ceiling is the
    smaller enabled global/non-upload budget; only the two image-upload POST
    routes get the larger budget. Each setting is read per request, with 0
    disabling only that setting's ceiling.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    @staticmethod
    def _is_image_upload(scope: Scope) -> bool:
        if scope["method"] != "POST":
            return False
        path = scope["path"]
        root = scope.get("root_path", "").rstrip("/")
        if root and path.startswith(root + "/"):
            path = path[len(root) :]
        # The router redirects trailing slashes to these same upload handlers.
        parts = path.rstrip("/").split("/")
        if len(parts) == 4 and parts[1] == "galleries" and parts[3] == "upload":
            gallery_id = parts[2]
        elif len(parts) == 6 and parts[1:4] == ["api", "v1", "galleries"] and parts[5] == "images":
            gallery_id = parts[4]
        else:
            return False
        try:
            uuid.UUID(gallery_id)
        except ValueError:
            return False
        return True

    @staticmethod
    def _is_selection_write(scope: Scope) -> bool:
        if scope["method"] != "POST":
            return False
        path = scope["path"]
        root = scope.get("root_path", "").rstrip("/")
        if root and path.startswith(root + "/"):
            path = path[len(root) :]
        parts = path.rstrip("/").split("/")
        return len(parts) == 4 and parts[1] == "g" and bool(parts[2]) and parts[3] == "selections"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limits = []
        if settings.max_request_body_mb:
            limits.append(settings.max_request_body_mb * 1024 * 1024)
        if settings.max_non_upload_request_body_kb and not self._is_image_upload(scope):
            limits.append(settings.max_non_upload_request_body_kb * 1024)
        if self._is_selection_write(scope):
            # JSON can represent each non-BMP codepoint as two six-byte
            # escapes. Leave ample room for metadata, field names and spacing.
            limits.append(max(65536, settings.max_selection_comment_chars * 12 + 4096))
        if not limits:
            await self.app(scope, receive, send)
            return

        max_bytes = min(limits)

        # Fast path: reject on the declared Content-Length before reading a byte.
        for name, value in scope["headers"]:
            if name == b"content-length":
                try:
                    if int(value) > max_bytes:
                        await self._send_413(send)
                        return
                except ValueError:
                    pass  # Malformed header — fall through to the byte counter.
                break

        # Robust path: count received bytes; if the cap is breached, send 413 and
        # signal a disconnect so the inner handler stops reading the body. A
        # send-guard prevents a double response if the handler already started one.
        received = 0
        response_started = False
        limit_hit = False
        rejected = False

        async def guarded_send(message: Message) -> None:
            nonlocal response_started
            if rejected:
                # Parsing may translate our disconnect into an inner 400. The
                # outer 413 has already completed; never send a second response.
                return
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        async def limited_receive() -> Message:
            nonlocal received, limit_hit, rejected
            if limit_hit:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > max_bytes and not limit_hit:
                    limit_hit = True
                    if not response_started:
                        rejected = True
                        await self._send_413(send)
                    return {"type": "http.disconnect"}
            return message

        try:
            await self.app(scope, limited_receive, guarded_send)
        except ClientDisconnect:
            if not rejected:
                raise

    @staticmethod
    async def _send_413(send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b'{"detail":"Request body too large"}',
                "more_body": False,
            }
        )
