"""Resolve and pin webhook destinations without trusting DNS after validation."""

import asyncio
import ipaddress
import socket
from typing import Any

import httpx

BLOCKED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]", "metadata.google.internal", "169.254.169.254"}  # noqa: S104


def parse_webhook_url(value: str) -> httpx.URL:
    """Parse the URL at delivery time as well as when it is configured."""
    try:
        url = httpx.URL(value)
    except httpx.InvalidURL as exc:
        raise ValueError("Invalid webhook URL") from exc
    if url.scheme != "https":
        raise ValueError("Webhook URL must use HTTPS")
    if not url.host or url.userinfo or "%" in url.host:
        raise ValueError("Webhook URL must not contain credentials or scoped addresses")
    host = url.host.lower().rstrip(".")
    if host in BLOCKED_HOSTS or host.endswith(".internal") or host.endswith(".local"):
        raise ValueError("Webhook URL must not point to internal addresses")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        _public_address(str(literal))
    return url


def _public_address(value: str) -> str:
    ip = ipaddress.ip_address(value)
    if isinstance(ip, ipaddress.IPv6Address) and any(
        ip in ipaddress.ip_network(prefix) for prefix in ("64:ff9b::/96", "64:ff9b:1::/48", "2002::/16", "2001::/32")
    ):
        # Translation/tunnel prefixes can conceal an IPv4 destination which
        # the local network maps into a private address space.
        raise ValueError("Webhook destination must resolve only to public unicast addresses")
    # IPv4-mapped IPv6 must obey the IPv4 policy too. Explicit checks are needed
    # because e.g. multicast may be classified as global by ipaddress.
    check = ip.ipv4_mapped if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped else ip
    if not check.is_global or check.is_multicast or check.is_reserved or check.is_unspecified:
        raise ValueError("Webhook destination must resolve only to public unicast addresses")
    return str(ip)


async def resolve_webhook_ips(url: httpx.URL) -> list[str]:
    """Reject the entire DNS answer if any address is not public unicast."""
    host = url.raw_host.decode("ascii")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        records = await asyncio.get_running_loop().getaddrinfo(
            host, url.port or 443, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM
        )
        addresses = [_public_address(str(record[4][0])) for record in records]
    else:
        addresses = [_public_address(str(literal))]
    if not addresses:
        raise ValueError("Webhook destination has no addresses")
    return list(dict.fromkeys(addresses))


async def post_webhook(value: str, event: str, payload: dict[str, Any]) -> None:
    """POST to a validated address, preserving the original Host and TLS name.

    Proxies are disabled to prevent them resolving a different destination.
    HTTPX's original CA environment is preserved when creating the TLS context.
    Redirects are disabled and response bodies are never consumed.
    """
    url = parse_webhook_url(value)
    async with asyncio.timeout(10):
        addresses = await resolve_webhook_ips(url)
        ssl_context = httpx.create_ssl_context(verify=True, trust_env=True)
        async with httpx.AsyncClient(
            verify=ssl_context, timeout=10.0, trust_env=False, follow_redirects=False
        ) as client:
            for index, address in enumerate(addresses):
                try:
                    async with client.stream(
                        "POST",
                        url.copy_with(host=address),
                        json={"event": event, "data": payload},
                        headers={
                            "Host": url.netloc.decode("ascii"),
                            "Content-Type": "application/json",
                            "User-Agent": "Picture-Stage/0.1",
                        },
                        extensions={"sni_hostname": url.raw_host.decode("ascii")},
                    ) as response:
                        response.raise_for_status()
                    return
                except (httpx.ConnectError, httpx.ConnectTimeout):
                    if index == len(addresses) - 1:
                        raise
