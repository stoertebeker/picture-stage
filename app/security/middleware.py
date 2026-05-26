import secrets

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response


class CSRFMiddleware(BaseHTTPMiddleware):
    """Double-submit cookie CSRF protection.

    Skips API routes (/api/) that use Bearer auth and guest routes (/g/) that use token-in-URL.
    """

    SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
    EXEMPT_PREFIXES = ("/api/", "/g/")

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
                    httponly=False,  # JS needs to read this
                    secure=True,
                    samesite="lax",
                    path="/",
                )
            return response

        # Unsafe method — verify CSRF token
        cookie_token = request.cookies.get("csrf_token")
        if not cookie_token:
            return Response("CSRF token missing", status_code=403)

        # Check form field first, then header
        form_token = None
        header_token = request.headers.get("X-CSRF-Token")

        if request.headers.get("content-type", "").startswith("application/x-www-form-urlencoded") or \
           request.headers.get("content-type", "").startswith("multipart/form-data"):
            form = await request.form()
            form_token = form.get("csrf_token")

        submitted_token = form_token or header_token

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
            "script-src 'self'",
            "frame-ancestors 'none'",
            "base-uri 'self'",
            "form-action 'self'",
        ]
        response.headers["Content-Security-Policy"] = "; ".join(csp_directives)

        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"

        return response
