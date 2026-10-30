from slowapi import Limiter
from slowapi.middleware import _should_exempt, async_check_limits
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Match

limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])


class DefaultRateLimitMiddleware(BaseHTTPMiddleware):
    """Apply SlowAPI defaults to the handler Starlette will actually dispatch.

    SlowAPI's route finder keeps the last match. Our HTML/JSON guest aliases
    intentionally share paths, so selection must stop at the first full match,
    including mounts. Defaults share a route-template bucket so rotating token
    values cannot reset admission. Explicit decorators still see the original
    request and preserve their existing scopes. BaseHTTPMiddleware preserves
    multi-chunk media/export responses.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        active_limiter: Limiter = request.app.state.limiter
        if not active_limiter.enabled:
            return await call_next(request)

        for route in request.app.routes:
            match, _ = route.matches(request.scope)
            if match == Match.FULL:
                handler = getattr(route, "endpoint", None)
                break
        else:
            handler = None

        if _should_exempt(active_limiter, handler):
            return await call_next(request)

        # Use a separate view for default-key calculation only. Never rewrite the
        # scope dispatched to the router, CSRF, parsing or endpoint decorators.
        # Share state so native rate-limit headers still describe this request.
        request.scope.setdefault("state", {})
        limit_scope = dict(request.scope, path=route.path)
        limit_request = Request(limit_scope, receive=request.receive)
        error_response, inject_headers = await async_check_limits(active_limiter, limit_request, handler, request.app)
        if error_response is not None:
            return error_response

        response = await call_next(request)
        if inject_headers:
            response = active_limiter._inject_headers(response, request.state.view_rate_limit)
        return response
