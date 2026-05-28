#!/usr/bin/env bash
# Smoke-Test für v0.3-Features gegen den laufenden Container auf localhost:8000.
# Testet auth-freie Endpunkte + prüft, ob alle neuen Routen registriert sind.
# Aufruf:  bash scripts/smoke_test_v03.sh

set -u
BASE="${BASE:-http://localhost:8000}"
PASS=0
FAIL=0

check() {
    local name="$1" expected="$2" actual="$3"
    if [ "$actual" = "$expected" ]; then
        echo "  ✅ $name (HTTP $actual)"
        PASS=$((PASS + 1))
    else
        echo "  ❌ $name (erwartet $expected, war $actual)"
        FAIL=$((FAIL + 1))
    fi
}

code() { curl -s -m 8 -o /dev/null -w "%{http_code}" "$@"; }

echo "=== Picture-Stage v0.3 Smoke-Test gegen $BASE ==="
echo

echo "[1] Basis-Erreichbarkeit"
check "GET /health" 200 "$(code "$BASE/health")"
echo

echo "[2] DSGVO-Seiten (fbr.3)"
check "GET /legal/impressum" 200 "$(code "$BASE/legal/impressum")"
check "GET /legal/datenschutz" 200 "$(code "$BASE/legal/datenschutz")"
echo

echo "[3] i18n: Language-Switcher (fbr.6)"
# set-lang setzt Cookie + redirect (302/303/307)
SETLANG=$(code "$BASE/set-lang/en")
if [ "$SETLANG" = "302" ] || [ "$SETLANG" = "303" ] || [ "$SETLANG" = "307" ] || [ "$SETLANG" = "200" ]; then
    echo "  ✅ GET /set-lang/en (HTTP $SETLANG)"
    PASS=$((PASS + 1))
else
    echo "  ❌ GET /set-lang/en (HTTP $SETLANG)"
    FAIL=$((FAIL + 1))
fi
# Cookie wird gesetzt?
if curl -s -m 8 -i "$BASE/set-lang/de" | grep -qi "set-cookie:.*lang"; then
    echo "  ✅ set-lang setzt lang-Cookie"
    PASS=$((PASS + 1))
else
    echo "  ⚠️  set-lang: kein lang-Cookie im Header gefunden (prüfen)"
    FAIL=$((FAIL + 1))
fi
echo

echo "[4] Neue v0.3-Routen in OpenAPI registriert"
SPEC=$(curl -s -m 8 "$BASE/openapi.json")
for route in \
    "/api/v1/galleries/{gallery_id}/audit-log" \
    "/api/v1/galleries/{gallery_id}/audit-log/export" \
    "/api/v1/auth/locale" ; do
    if printf '%s' "$SPEC" | grep -q "$route"; then
        echo "  ✅ Route registriert: $route"
        PASS=$((PASS + 1))
    else
        echo "  ❌ Route FEHLT: $route"
        FAIL=$((FAIL + 1))
    fi
done
echo

echo "[5] i18n Accept-Language Detection"
# Login-/Startseite mit EN-Header sollte englische Strings liefern (heuristisch)
HOME_EN=$(curl -s -m 8 -H "Accept-Language: en" "$BASE/")
HOME_DE=$(curl -s -m 8 -H "Accept-Language: de" "$BASE/")
if [ -n "$HOME_EN" ] && [ -n "$HOME_DE" ]; then
    echo "  ✅ Startseite liefert Inhalt für beide Sprachen (manuelle Sichtprüfung empfohlen)"
    PASS=$((PASS + 1))
else
    echo "  ❌ Startseite leer für eine Sprache"
    FAIL=$((FAIL + 1))
fi
echo

echo "=== Ergebnis: $PASS bestanden, $FAIL fehlgeschlagen ==="
[ "$FAIL" -eq 0 ] && echo "Alle auth-freien Smoke-Tests grün. ⚓" || echo "Es gibt Findings — bitte oben prüfen."
