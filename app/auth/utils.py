"""Auth utility helpers."""

from fastapi import Request


def get_client_ip(request: Request) -> str | None:
    """Extract the real client IP from the request.

    Priority:
    1. CF-Connecting-IP — set by Cloudflare, reliable when CF is the actual
       edge proxy. Cannot be spoofed by clients because Cloudflare strips any
       client-supplied header with the same name before forwarding.
    2. X-Forwarded-For (leftmost IP) — set by any proxy; spoofable when there
       is no trusted proxy in front, so only use as fallback.
    3. request.client.host — the immediate TCP peer, which behind Caddy/CF will
       be the proxy address, not the real client.
    """
    cf_ip = request.headers.get("CF-Connecting-IP")
    if cf_ip:
        return cf_ip.strip()

    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()

    return request.client.host if request.client else None
