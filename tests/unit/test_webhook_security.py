"""Webhook destination validation, pinned TLS and bounded response handling."""

import asyncio
import ipaddress
import socket
import ssl
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from pydantic import ValidationError

from app.notifications import service, webhook
from app.notifications.schemas import NotificationConfigUpdate


@pytest.mark.parametrize(
    "address",
    [
        "10.0.0.8",
        "127.0.0.2",
        "169.254.169.254",
        "100.64.0.1",
        "224.0.0.1",
        "::1",
        "fc00::1",
        "::ffff:127.0.0.1",
        "64:ff9b::a00:1",
        "2002:0a00:0001::1",
    ],
)
def test_private_and_non_unicast_literal_addresses_rejected(address: str) -> None:
    host = f"[{address}]" if ":" in address else address
    with pytest.raises(ValidationError):
        NotificationConfigUpdate(webhook_url=f"https://{host}/hook", events=[])


@pytest.mark.parametrize("url", ["https://user:pass@example.com", "http://example.com", "https://service.local./hook"])
def test_invalid_urls_rejected_at_configuration_and_delivery(url: str) -> None:
    with pytest.raises(ValidationError):
        NotificationConfigUpdate(webhook_url=url, events=[])
    with pytest.raises(ValueError):
        webhook.parse_webhook_url(url)


async def test_dns_mixed_public_and_private_answers_are_rejected() -> None:
    records = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
    ]
    loop = asyncio.get_running_loop()
    with patch.object(loop, "getaddrinfo", new=AsyncMock(return_value=records)):
        with pytest.raises(ValueError, match="public unicast"):
            await webhook.resolve_webhook_ips(httpx.URL("https://example.com/hook"))


async def test_dns_public_ipv4_and_ipv6_are_accepted() -> None:
    records = [
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:4700:4700::1111", 443, 0, 0)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443)),
    ]
    loop = asyncio.get_running_loop()
    with patch.object(loop, "getaddrinfo", new=AsyncMock(return_value=records)):
        assert await webhook.resolve_webhook_ips(httpx.URL("https://example.com/hook")) == [
            str(ipaddress.ip_address(record[4][0])) for record in records
        ]


async def test_dns_rebinding_does_not_trigger_second_lookup(monkeypatch) -> None:
    public = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443))
    private = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
    resolver = AsyncMock(side_effect=[[public], [private]])
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolver)
    real_client = httpx.AsyncClient
    destinations = []

    async def handle(request: httpx.Request) -> httpx.Response:
        destinations.append(request.url.host)
        return httpx.Response(204)

    monkeypatch.setattr(
        webhook.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs),
    )
    await webhook.post_webhook("https://rebind.example/hook", "gallery_completed", {})
    assert resolver.await_count == 1
    assert destinations == ["1.1.1.1"]


@pytest.mark.parametrize("failure", ["connect", "read", "redirect"])
async def test_fallback_does_not_repeat_sent_requests_or_follow_redirects(monkeypatch, failure: str) -> None:
    real_client = httpx.AsyncClient
    destinations = []

    async def handle(request: httpx.Request) -> httpx.Response:
        destinations.append(request.url.host)
        if len(destinations) == 1:
            if failure == "connect":
                raise httpx.ConnectError("unreachable", request=request)
            if failure == "read":
                raise httpx.ReadTimeout("timed out", request=request)
            return httpx.Response(307, headers={"Location": "https://127.0.0.1/private"})
        return httpx.Response(204)

    monkeypatch.setattr(webhook, "resolve_webhook_ips", AsyncMock(return_value=["1.1.1.1", "8.8.8.8"]))
    monkeypatch.setattr(
        webhook.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs),
    )
    if failure == "connect":
        await webhook.post_webhook("https://example.com/hook", "gallery_completed", {})
        assert destinations == ["1.1.1.1", "8.8.8.8"]
    else:
        with pytest.raises(httpx.ReadTimeout if failure == "read" else httpx.HTTPStatusError):
            await webhook.post_webhook("https://example.com/hook", "gallery_completed", {})
        assert destinations == ["1.1.1.1"]


async def test_delivery_pins_host_and_never_iterates_response(monkeypatch) -> None:
    class UnreadableResponse(httpx.AsyncByteStream):
        async def __aiter__(self):
            raise AssertionError("webhook response body was consumed")
            yield b""

    seen = []

    async def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204, stream=UnreadableResponse())

    real_client = httpx.AsyncClient

    def client(*args, **kwargs):
        assert kwargs["trust_env"] is False
        assert kwargs["follow_redirects"] is False
        return real_client(*args, transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(webhook, "resolve_webhook_ips", AsyncMock(return_value=["2606:4700:4700::1111"]))
    monkeypatch.setattr(webhook.httpx, "AsyncClient", client)
    await webhook.post_webhook("https://hooks.example.com:8443/path?q=one", "gallery_completed", {"a": 1})
    assert len(seen) == 1
    assert seen[0].url == httpx.URL("https://[2606:4700:4700::1111]:8443/path?q=one")
    assert seen[0].headers["host"] == "hooks.example.com:8443"
    assert seen[0].extensions["sni_hostname"] == "hooks.example.com"


async def test_preexisting_private_destination_fails_delivery_without_connecting() -> None:
    config = MagicMock(id=4, webhook_url="https://10.0.0.8/hook")
    db = MagicMock()
    db.commit = AsyncMock()
    with patch.object(webhook.httpx, "AsyncClient") as client:
        await service._send_webhook(config, "gallery_completed", {}, db)
    client.assert_not_called()
    assert db.add.call_args.args[0].status == "failed"
    db.commit.assert_awaited_once()


async def test_real_tls_uses_original_hostname_and_checks_certificate(tmp_path, monkeypatch) -> None:
    """A local TLS peer proves SNI, Host, verification and response closure."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "hooks.example.test")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("hooks.example.test")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "server.crt", tmp_path / "server.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    server_tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_tls.load_cert_chain(cert_path, key_path)
    names, requests = [], []
    server_tls.set_servername_callback(lambda _sock, host, _context: names.append(host))

    async def serve(reader, writer):
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            requests.append(head)
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 1000000000\r\n\r\n")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(serve, "127.0.0.1", 0, ssl=server_tls)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setattr(webhook, "resolve_webhook_ips", AsyncMock(return_value=["127.0.0.1"]))
    monkeypatch.setenv("SSL_CERT_FILE", str(cert_path))
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    try:
        await asyncio.wait_for(
            webhook.post_webhook(f"https://hooks.example.test:{port}/hook?q=1", "gallery_completed", {}), 3
        )
        with pytest.raises(httpx.ConnectError):
            await webhook.post_webhook(f"https://wrong.example.test:{port}/hook", "gallery_completed", {})
    finally:
        server.close()
        await server.wait_closed()
        cert_path.unlink(missing_ok=True)
        key_path.unlink(missing_ok=True)
    assert names == ["hooks.example.test", "wrong.example.test"]
    assert len(requests) == 1
    assert requests[0].startswith(b"POST /hook?q=1 HTTP/1.1\r\n")
    assert f"host: hooks.example.test:{port}\r\n".encode() in requests[0].lower()


@pytest.mark.parametrize(
    "listen_address,valid_ip,wrong_ip",
    [
        ("127.0.0.1", "1.1.1.1", "8.8.8.8"),
        ("::1", "2606:4700:4700::1111", "2606:4700:4700::1001"),
    ],
)
async def test_real_tls_checks_ip_literal_certificate_san(
    tmp_path, monkeypatch, listen_address: str, valid_ip: str, wrong_ip: str
) -> None:
    """Literal IP identities are verified as IP SANs, including IPv6 when available."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "webhook-ip-test")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(valid_ip))]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "server.crt", tmp_path / "server.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    server_tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_tls.load_cert_chain(cert_path, key_path)
    names, requests = [], []
    server_tls.set_servername_callback(lambda _sock, host, _context: names.append(host))

    async def serve(reader, writer):
        try:
            requests.append(await reader.readuntil(b"\r\n\r\n"))
            writer.write(b"HTTP/1.1 204 No Content\r\n\r\n")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    try:
        server = await asyncio.start_server(serve, listen_address, 0, ssl=server_tls)
    except OSError as exc:
        cert_path.unlink(missing_ok=True)
        key_path.unlink(missing_ok=True)
        if ":" in listen_address:
            pytest.skip(f"IPv6 loopback unavailable: {exc}")
        raise
    port = server.sockets[0].getsockname()[1]
    # The public IP is the URL and certificate identity; local resolution is
    # substituted solely to run the TLS peer without external network access.
    monkeypatch.setattr(webhook, "resolve_webhook_ips", AsyncMock(return_value=[listen_address]))
    monkeypatch.setenv("SSL_CERT_FILE", str(cert_path))

    def authority(ip: str) -> str:
        return f"[{ip}]" if ":" in ip else ip

    try:
        await asyncio.wait_for(
            webhook.post_webhook(f"https://{authority(valid_ip)}:{port}/hook", "gallery_completed", {}), 3
        )
        with pytest.raises(httpx.ConnectError):
            await webhook.post_webhook(f"https://{authority(wrong_ip)}:{port}/hook", "gallery_completed", {})
    finally:
        server.close()
        await server.wait_closed()
        cert_path.unlink(missing_ok=True)
        key_path.unlink(missing_ok=True)
    assert names == [None, None]  # Python TLS omits SNI for IP literals.
    assert len(requests) == 1
    assert f"host: {authority(valid_ip)}:{port}\r\n".encode() in requests[0].lower()
