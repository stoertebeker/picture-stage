import secrets
from typing import ClassVar
from urllib.parse import parse_qs

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import settings


class CSRFMiddleware(BaseHTTPMiddleware):
    """Double-submit cookie CSRF protection.

    Skips API routes (/api/) that use Bearer auth and guest routes (/g/) that use token-in-URL.
    """

    SAFE_METHODS: ClassVar[set[str]] = {"GET", "HEAD", "OPTIONS", "TRACE"}
    EXEMPT_PREFIXES: ClassVar[tuple[str, ...]] = ("/api/", "/g/")

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path

        # Skip CSRF for exempt routes
        if any(path.startswith(prefix) for prefix in self.EXEMPT_PREFIXES):
            return await call_next(request)

        if request.method in self.SAFE_METHODS:
            response = await call_next(request)
            # Set CSRF cookie on GET requests if not already present
            if request.method == "GET" and not request.cookies.get("csrf_token"):
                csrf_token = secrets.token_urlsafe(32)
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
            return Response("CSRF token missing", status_code=403)

        # Check header first, then form body (without consuming request.form())
        header_token = request.headers.get("X-CSRF-Token")
        form_token = None

        if not header_token and request.headers.get("content-type", "").startswith("application/x-www-form-urlencoded"):
            body = await request.body()
            params = parse_qs(body.decode("utf-8", errors="replace"))
            form_token = params.get("csrf_token", [None])[0]

        submitted_token = header_token or form_token

        if not submitted_token or submitted_token != cookie_token:
            return Response("CSRF token mismatch", status_code=403)

        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)

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

    Registered as the outermost middleware so it runs before CSRF (which itself reads
    ``request.body()``). The limit is read from ``settings`` per request; ``0`` disables it.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        max_mb = settings.max_request_body_mb
        if not max_mb:
            await self.app(scope, receive, send)
            return

        max_bytes = max_mb * 1024 * 1024

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

        async def guarded_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        async def limited_receive() -> Message:
            nonlocal received, limit_hit
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > max_bytes and not limit_hit:
                    limit_hit = True
                    if not response_started:
                        await self._send_413(send)
                    return {"type": "http.disconnect"}
            return message

        await self.app(scope, limited_receive, guarded_send)

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
