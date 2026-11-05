"""Signed, request-local progress for bounded compatibility token lookups.

Progress and positive hints locate candidates only; resolvers must always verify
the current salted token and the caller must retain its lifecycle/access fences.
No server-side cursor or transaction survives a response.
"""

import hashlib
import hmac
import re
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from urllib.parse import unquote

from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import settings

LOOKUP_WINDOW = 64
LOOKUP_TTL = 15 * 60
RETRY_HEADER = "X-Picture-Stage-Lookup"
RETRY_VALUE = "before-effects"


@dataclass
class LookupContext:
    request: Request
    cookies: dict[tuple[str, str], str] = field(default_factory=dict)


_context: ContextVar[LookupContext | None] = ContextVar("token_lookup_request", default=None)


class LookupProgressMiddleware:
    """Attach cookies to actual exception responses as well as normal results."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        context = LookupContext(Request(scope))
        reset = _context.set(context)

        async def send_with_progress(message: Message) -> None:
            if message["type"] == "http.response.start" and context.cookies:
                response = Response()
                for (name, path), value in context.cookies.items():
                    response.set_cookie(
                        name,
                        value,
                        max_age=LOOKUP_TTL if value else 0,
                        path=path,
                        httponly=True,
                        secure=settings.cookie_secure,
                        samesite="lax",
                    )
                message["headers"] = list(message["headers"]) + [
                    header for header in response.raw_headers if header[0] == b"set-cookie"
                ]
            await send(message)

        try:
            await self.app(scope, receive, send_with_progress)
        finally:
            _context.reset(reset)


class LookupProgress:
    """A bounded envelope, authenticated for a purpose and exact bearer token.

    Fixed-width keys avoid big-int/UUID parser overflow on untrusted cookies.
    An expiry is renewed only when a resolver records actual candidate progress.
    """

    def __init__(self, purpose: str, token: str, path: str | None = None) -> None:
        self.purpose = purpose
        self.token_digest = hashlib.sha256(token.encode()).hexdigest()
        self.context = _context.get()
        self.name = f"{purpose}_lookup"
        scope = self.context.request.scope if self.context else {}
        root = scope.get("root_path", "").rstrip("/")
        if path is None:
            # Verification has distinct HTML and API entry points. Keep its
            # continuation on the exact requesting route, including a mount.
            path = self.context.request.scope["path"] if self.context else "/"
            self.path = path if root and (path == root or path.startswith(root + "/")) else root + path
            decoded_path = self.path  # ASGI path is already decoded exactly once.
        else:
            self.path = root + path
            decoded_path = root + unquote(path)
        # Browsers match Cookie Path against the raw URL, while routers match
        # its decoded path. Keep an encoded alias on its actual public prefix:
        # /g/%6cegacy and /g/legacy resolve the same bearer but not the same cookie.
        raw_path = scope.get("raw_path", b"").decode("ascii", errors="replace")
        decoded_root = root
        if root and not (unquote(raw_path) == decoded_root or unquote(raw_path).startswith(decoded_root + "/")):
            raw_path = root + raw_path
        for end in [match.start() for match in re.finditer("/", raw_path)] + [len(raw_path)]:
            prefix = raw_path[:end]
            if unquote(prefix) == decoded_path:
                self.path = prefix
                break

    def _mac(self, payload: str) -> str:
        return hmac.new(
            settings.secret_key.encode(),
            f"picture-stage:lookup:v1:{self.purpose}:{self.token_digest}:{payload}".encode(),
            hashlib.sha256,
        ).hexdigest()

    def read(self) -> tuple[str, str] | None:
        value = self.context.request.cookies.get(self.name) if self.context else None
        if not value or len(value) > 160:
            return None
        match = re.fullmatch(r"([ilh])\.([0-9a-f]{32})\.([0-9]{1,12})\.([0-9a-f]{64})", value)
        if match is None:
            return None
        phase, key, expiry, signature = match.groups()
        payload = f"{phase}.{key}.{expiry}"
        if not hmac.compare_digest(signature, self._mac(payload)) or int(expiry) <= time.time():
            return None
        return phase, key

    def save(self, phase: str, key: str) -> None:
        if self.context is None:
            return
        payload = f"{phase}.{key}.{int(time.time()) + LOOKUP_TTL}"
        self.context.cookies[self.name, self.path] = f"{payload}.{self._mac(payload)}"

    def clear(self) -> None:
        if self.context is not None:
            self.context.cookies[self.name, self.path] = ""

    def retry(self, phase: str, key: str) -> None:
        self.save(phase, key)
        raise HTTPException(
            status_code=503,
            detail="Token lookup in progress; retry the same request",
            headers={"Retry-After": "1", RETRY_HEADER: RETRY_VALUE, "Cache-Control": "private, no-store"},
        )
