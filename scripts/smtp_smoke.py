#!/usr/bin/env python3
"""SMTP-Smoke-Test — verifiziert die SMTP_*-Credentials gegen den echten Provider.

Nutzt denselben Code-Pfad wie der produktive Versand in
``app/notifications/service.py`` (``aiosmtplib.send`` mit ``start_tls=``),
lädt die echten Settings aus ``.env`` und schickt EINE Testmail.

Beweist in einem Schritt: Host/Port erreichbar, Auth (User/Secret) korrekt,
STARTTLS-Handshake ok, Absender-Domain beim Provider verifiziert.

Verwendung:
    python scripts/smtp_smoke.py
    # -> fragt die Empfängeradresse interaktiv ab

Hinweis: Muss aus einer Umgebung MIT Netzzugang zum SMTP-Host laufen
(z.B. lokal oder auf dem Server) — nicht aus einer Sandbox ohne Egress.
"""

from __future__ import annotations

import asyncio
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib

from app.config import settings


def _mask(value: str, keep: int = 4) -> str:
    """Maskiert ein Secret für die Ausgabe (nie das volle Credential loggen)."""
    if not value:
        return "(leer)"
    if len(value) <= keep:
        return "*" * len(value)
    return value[:keep] + "*" * (len(value) - keep)


async def _run(recipient: str) -> int:
    # --- Vorab-Checks: harte Fehlkonfiguration früh und klar melden ---
    problems: list[str] = []
    if not settings.smtp_host:
        problems.append("SMTP_HOST ist leer")
    if not settings.smtp_from or settings.smtp_from == "noreply@example.com":
        problems.append("SMTP_FROM ist leer oder noch der Platzhalter (Provider lehnt das ab)")
    if not settings.smtp_user:
        problems.append("SMTP_USER ist leer")
    if not settings.smtp_password:
        problems.append("SMTP_PASSWORD ist leer")

    print("=== SMTP-Konfiguration (aus .env via app.config.settings) ===")
    print(f"  SMTP_HOST     : {settings.smtp_host or '(leer)'}")
    print(f"  SMTP_PORT     : {settings.smtp_port}")
    print(f"  SMTP_STARTTLS : {settings.smtp_starttls}")
    print(f"  SMTP_USER     : {_mask(settings.smtp_user)}")
    print(f"  SMTP_PASSWORD : {_mask(settings.smtp_password)}")
    print(f"  SMTP_FROM     : {settings.smtp_from}")
    print(f"  -> Empfänger  : {recipient}")
    print("=" * 60)

    if problems:
        print("❌ Abbruch — Konfigurationsprobleme:")
        for p in problems:
            print(f"   - {p}")
        return 2

    # --- Mail bauen (identisch zur Struktur in service.py) ---
    msg = MIMEMultipart("alternative")
    msg["From"] = settings.smtp_from
    msg["To"] = recipient
    msg["Subject"] = "Picture-Stage SMTP-Smoke-Test"
    text_body = (
        "Dies ist eine Testmail aus scripts/smtp_smoke.py.\n"
        "Wenn du sie liest, funktioniert der SMTP-Versand von Picture-Stage.\n"
    )
    html_body = (
        "<p>Dies ist eine Testmail aus <code>scripts/smtp_smoke.py</code>.</p>"
        "<p>Wenn du sie liest, funktioniert der SMTP-Versand von Picture-Stage.</p>"
    )
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    # --- Versand: exakt derselbe Aufruf wie in app/notifications/service.py ---
    try:
        print(f"→ Verbinde mit {settings.smtp_host}:{settings.smtp_port} (start_tls={settings.smtp_starttls}) …")
        await aiosmtplib.send(
            msg,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_user or None,
            password=settings.smtp_password or None,
            start_tls=settings.smtp_starttls,
        )
    except aiosmtplib.SMTPAuthenticationError as exc:
        print(f"❌ Authentifizierung fehlgeschlagen ({exc.code}): {exc.message}")
        print("   → Prüfe SMTP_USER (= API-Key) und SMTP_PASSWORD (= Secret-Key).")
        return 1
    except aiosmtplib.SMTPException as exc:
        print(f"❌ SMTP-Fehler: {exc!r}")
        print("   → Häufige Ursache: Absender-Domain/Adresse nicht beim Provider verifiziert (550),")
        print("     falscher Port/STARTTLS, oder Host nicht erreichbar.")
        return 1
    except (OSError, TimeoutError) as exc:
        print(f"❌ Netzwerk-/Verbindungsfehler: {exc!r}")
        print(f"   → Ist {settings.smtp_host}:{settings.smtp_port} von hier aus erreichbar? (Egress/Firewall/Sandbox?)")
        return 1

    print("✅ Versand erfolgreich abgeschlossen. Prüfe das Postfach des Empfängers.")
    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] in {"-h", "--help"}:
        print(__doc__)
        return 0
    try:
        recipient = input("Empfängeradresse für die Testmail: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nAbgebrochen.", file=sys.stderr)
        return 130
    if "@" not in recipient:
        print(f"Fehler: '{recipient}' sieht nicht wie eine E-Mail-Adresse aus.", file=sys.stderr)
        return 2
    return asyncio.run(_run(recipient))


if __name__ == "__main__":
    raise SystemExit(main())
