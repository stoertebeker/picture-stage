"""In-Process-Funktionstest für v0.3-Features.

Läuft komplett ohne Netzwerk/DB über FastAPI TestClient (ASGI in-process) +
direkte Logik-Tests. Deckt 6 der 7 v0.3-Features ab; die DB-gebundenen
Request-Flows (Galerie-CRUD/Lösch/Audit-Persistenz) brauchen eine echte DB
und sind hier bewusst ausgeklammert.

Aufruf:  .venv/bin/python scripts/functional_test_v03.py
"""

import sys

PASS = 0
FAIL = 0


def ok(name: str) -> None:
    global PASS
    PASS += 1
    print(f"  ✅ {name}")


def bad(name: str, detail: str = "") -> None:
    global FAIL
    FAIL += 1
    print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))


def section(title: str) -> None:
    print(f"\n[{title}]")


# ---------------------------------------------------------------------------
section("1] Route-Registrierung (alle v0.3-Endpunkte)")
from app.main import app  # noqa: E402

paths = {getattr(r, "path", "") for r in app.routes}
methods = {(getattr(r, "path", ""), m) for r in app.routes for m in getattr(r, "methods", []) or []}

expected_routes = [
    ("/api/v1/galleries/{gallery_id}/audit-log", "GET", "Audit-Log Viewer (fbr.2)"),
    ("/api/v1/galleries/{gallery_id}/audit-log/export", "GET", "Audit-Log CSV-Export (fbr.2)"),
    ("/legal/impressum", "GET", "Impressum (fbr.3)"),
    ("/legal/datenschutz", "GET", "Datenschutz (fbr.3)"),
    ("/api/v1/galleries/{gallery_id}", "DELETE", "Galerie-Lösch (fbr.4)"),
    ("/galleries/{gallery_id}/delete", "POST", "Lösch-Frontend (fbr.4)"),
    ("/galleries/{gallery_id}/expiry", "POST", "Ablaufdatum setzen (fbr.1)"),
    ("/api/v1/auth/locale", "PUT", "Locale-Update (fbr.6)"),
    ("/set-lang/{locale}", "GET", "Language-Switcher (fbr.6)"),
]
for path, method, label in expected_routes:
    if (path, method) in methods:
        ok(f"{method} {path} — {label}")
    else:
        bad(f"{method} {path} — {label}", "nicht registriert")


# ---------------------------------------------------------------------------
section("2] HTTP-Requests in-process (auth-frei, ohne DB)")
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(app)  # ohne 'with' -> kein Lifespan/DB-Startup

r = client.get("/health")
ok("GET /health → 200") if r.status_code == 200 else bad("GET /health", f"HTTP {r.status_code}")

for path, label in [("/legal/impressum", "Impressum"), ("/legal/datenschutz", "Datenschutz")]:
    r = client.get(path)
    if r.status_code == 200:
        ok(f"GET {path} → 200 ({label})")
    else:
        bad(f"GET {path}", f"HTTP {r.status_code}")

# set-lang setzt Cookie + redirect
r = client.get("/set-lang/en", follow_redirects=False)
if r.status_code in (302, 303, 307) and "lang" in r.headers.get("set-cookie", ""):
    ok(f"GET /set-lang/en → {r.status_code} + lang-Cookie")
else:
    bad("GET /set-lang/en", f"HTTP {r.status_code}, set-cookie={r.headers.get('set-cookie', '')!r}")

# ungültige locale wird abgewiesen oder ignoriert (kein 500)
r = client.get("/set-lang/xx", follow_redirects=False)
ok("GET /set-lang/xx kein Server-Error") if r.status_code < 500 else bad("GET /set-lang/xx", f"HTTP {r.status_code}")


# ---------------------------------------------------------------------------
section("3] i18n-Logik (fbr.6)")
import json  # noqa: E402
from pathlib import Path  # noqa: E402

from app.i18n import t  # noqa: E402

de = json.loads(Path("app/i18n/de.json").read_text())
en = json.loads(Path("app/i18n/en.json").read_text())


def flatten(d: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(flatten(v, key))
        else:
            out[key] = v
    return out


de_flat, en_flat = flatten(de), flatten(en)
ok(f"de.json geladen ({len(de_flat)} Keys)")
missing_en = set(de_flat) - set(en_flat)
missing_de = set(en_flat) - set(de_flat)
if not missing_en and not missing_de:
    ok(f"DE↔EN Key-Parität vollständig ({len(en_flat)} Keys)")
else:
    bad("DE↔EN Key-Parität", f"EN fehlt: {list(missing_en)[:3]}, DE fehlt: {list(missing_de)[:3]}")

# t() Lookup DE vs EN
if t("dashboard.no_galleries_title", "de") == "Noch keine Galerien":
    ok("t() DE-Lookup korrekt")
else:
    bad("t() DE-Lookup", t("dashboard.no_galleries_title", "de"))
if t("dashboard.no_galleries_title", "en") != t("dashboard.no_galleries_title", "de"):
    ok("t() EN liefert andere Übersetzung als DE")
else:
    bad("t() EN-Lookup", "EN == DE (nicht übersetzt?)")
# Fallback: unbekannter Key -> Key selbst
if t("does.not.exist", "de") == "does.not.exist":
    ok("t() Fallback bei fehlendem Key → Key selbst")
else:
    bad("t() Fallback", t("does.not.exist", "de"))
# Fallback: unbekannte locale -> DE
if t("dashboard.no_galleries_title", "fr") == t("dashboard.no_galleries_title", "de"):
    ok("t() Fallback unbekannte Locale → DE")
else:
    bad("t() Locale-Fallback", "fr != de")


# ---------------------------------------------------------------------------
section("4] Legal Markdown-Renderer / XSS-Schutz (fbr.3)")
from app.frontend.legal import _minimal_md_to_html  # noqa: E402

html = _minimal_md_to_html("# Titel\n\nNormaler **fetter** Text.")
ok("Heading gerendert") if "<h1>" in html else bad("Heading", html[:80])
ok("Bold gerendert") if "<strong>" in html else bad("Bold", html[:80])

# Raw HTML wird gestrippt/escaped
xss = _minimal_md_to_html("<script>alert(1)</script>")
if "<script>" not in xss:
    ok("Raw <script> wird entfernt/escaped")
else:
    bad("XSS <script>", xss[:80])

# javascript: Link wird zu Plaintext (kein href)
jslink = _minimal_md_to_html("[klick](javascript:alert(1))")
if "javascript:" not in jslink or "href" not in jslink:
    ok("javascript:-Link wird neutralisiert")
else:
    bad("javascript:-Link", jslink[:120])

# https-Link bleibt erhalten
goodlink = _minimal_md_to_html("[Seite](https://example.com)")
if 'href="https://example.com"' in goodlink:
    ok("https-Link bleibt erhalten")
else:
    bad("https-Link", goodlink[:120])


# ---------------------------------------------------------------------------
section("5] Wasserzeichen-Konfig (fbr.7)")
from app.images.processing import _calculate_text_position, _resolve_watermark_settings  # noqa: E402

# Globaler Default (kein per-gallery config), Platzhalter {gallery_id}
text, pos, opacity, font = _resolve_watermark_settings(None, "abc12345", 1280)
if "abc12345" in text or "{gallery_id}" not in text:
    ok(f"Default-Watermark + {{gallery_id}}-Platzhalter aufgelöst (text={text!r})")
else:
    bad("Platzhalter-Auflösung", text)

# Per-gallery Override
ov = {"text": "MEINS", "position": "top-left", "opacity": 0.5, "font_size": 40}
text2, pos2, op2, font2 = _resolve_watermark_settings(ov, "abc12345", 1280)
if text2 == "MEINS" and pos2 == "top-left":
    ok("Per-Galerie-Override greift (Text + Position)")
else:
    bad("Override", f"text={text2!r} pos={pos2!r}")

# Position-Berechnung: 5 Positionen liefern unterschiedliche Koordinaten
positions = {p: _calculate_text_position(p, 1000, 800, 100, 50) for p in
             ["top-left", "top-right", "bottom-left", "bottom-right", "center"]}
if len({v for v in positions.values()}) == 5:
    ok("5 Wasserzeichen-Positionen liefern distinkte Koordinaten")
else:
    bad("Positionen", str(positions))
# top-left ist immer (MARGIN, MARGIN)
if positions["top-left"][0] < positions["top-right"][0]:
    ok("top-left links von top-right (Geometrie plausibel)")
else:
    bad("Geometrie", str(positions))

# Pydantic-Validierung: opacity/font_size Range
from pydantic import ValidationError  # noqa: E402

from app.galleries.schemas import WatermarkConfig  # noqa: E402

try:
    WatermarkConfig(text="x", position="bottom-right", opacity=0.3, font_size=24)
    ok("WatermarkConfig akzeptiert gültige Werte")
except ValidationError as e:
    bad("WatermarkConfig gültig", str(e)[:80])

for field, value in [("opacity", 5.0), ("font_size", 999), ("position", "nowhere")]:
    kwargs = {"text": "x", "position": "bottom-right", "opacity": 0.3, "font_size": 24}
    kwargs[field] = value
    try:
        WatermarkConfig(**kwargs)
        bad(f"WatermarkConfig lehnt {field}={value} ab", "wurde akzeptiert")
    except ValidationError:
        ok(f"WatermarkConfig lehnt ungültiges {field}={value} ab")


# ---------------------------------------------------------------------------
section("6] Backup-CLI (fbr.5)")
import tarfile  # noqa: E402
import tempfile  # noqa: E402

from app.cli.backup import _parse_database_url, _safe_tar_extract  # noqa: E402

# DATABASE_URL parsing (asyncpg-Schema normalisiert)
db = _parse_database_url("postgresql+asyncpg://user:secret@dbhost:5433/mydb")
if db["host"] == "dbhost" and db["port"] == "5433" and db["dbname"] == "mydb" and db["user"] == "user":
    ok("DATABASE_URL (asyncpg) korrekt geparst")
else:
    bad("URL-Parsing", str(db))

# Path-Traversal-Schutz beim tar-Extract
with tempfile.TemporaryDirectory() as td:
    tdp = Path(td)
    evil = tdp / "evil.tar"
    with tarfile.open(evil, "w") as tar:
        info = tarfile.TarInfo(name="../../etc/passwd_escape")
        data = b"x"
        import io as _io
        info.size = len(data)
        tar.addfile(info, _io.BytesIO(data))
    dest = tdp / "out"
    dest.mkdir()
    try:
        with tarfile.open(evil, "r") as tar:
            _safe_tar_extract(tar, dest)
        bad("Path-Traversal-Schutz", "Extract wurde NICHT blockiert")
    except ValueError:
        ok("Path-Traversal (../) beim Extract abgewiesen")


# ---------------------------------------------------------------------------
section("7] CSV-Filename-Sanitization (fbr.2 Security-Fix)")
from app.galleries.router import _sanitize_filename  # noqa: E402

# Testet die ECHTE Funktion aus router.py (keine Replikation)
evil_name = 'gallery"\r\nSet-Cookie: x=1'
clean = _sanitize_filename(evil_name)
if "\r" not in clean and "\n" not in clean and '"' not in clean and ":" not in clean:
    ok(f"CSV-Filename neutralisiert Header-Injection (→ {clean!r})")
else:
    bad("CSV-Filename", repr(clean))

# Tab und weitere Steuerzeichen ebenfalls raus
if "\t" not in _sanitize_filename("a\tb"):
    ok("CSV-Filename entfernt Tabs")
else:
    bad("CSV-Filename Tab", repr(_sanitize_filename("a\tb")))

# Legitime Namen (Buchstaben, Zahlen, Leerzeichen, Bindestrich) bleiben erhalten
if _sanitize_filename("Sommer-Shooting 2026") == "Sommer-Shooting 2026":
    ok("CSV-Filename erhält legitime Namen (Leerzeichen/Bindestrich)")
else:
    bad("CSV-Filename legitim", repr(_sanitize_filename("Sommer-Shooting 2026")))


# ---------------------------------------------------------------------------
print(f"\n=== Ergebnis: {PASS} bestanden, {FAIL} fehlgeschlagen ===")
if FAIL == 0:
    print("Alle In-Process-Funktionstests grün. ⚓")
    sys.exit(0)
else:
    print("Findings vorhanden — bitte oben prüfen.")
    sys.exit(1)
