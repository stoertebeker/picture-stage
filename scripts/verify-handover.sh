#!/usr/bin/env bash
# Verify that a Picture-Stage handover is in a clean, workable state.
#
# Run this at the start of a fresh session:
#   bash scripts/verify-handover.sh
#
# Exit code 0 = ready to work. Non-zero = something needs attention.

set -u
PASS=0
FAIL=0

ok()  { PASS=$((PASS + 1)); echo "  ✅ $1"; }
bad() { FAIL=$((FAIL + 1)); echo "  ❌ $1"; }

echo "=== Repository state ==="
if [ -z "$(git status --porcelain)" ]; then
    ok "working tree clean"
else
    bad "working tree has uncommitted changes — run 'git status'"
fi

REMOTE_HEAD=$(git rev-parse origin/main 2>/dev/null || echo "")
LOCAL_HEAD=$(git rev-parse HEAD)
if [ -n "$REMOTE_HEAD" ] && [ "$REMOTE_HEAD" = "$LOCAL_HEAD" ]; then
    ok "HEAD matches origin/main ($LOCAL_HEAD)"
elif [ -n "$REMOTE_HEAD" ]; then
    bad "HEAD differs from origin/main — run 'git pull --rebase' or 'git push'"
else
    bad "no origin/main reference — run 'git fetch origin'"
fi

if git tag -l "handover-*" | head -1 | grep -q .; then
    LATEST_TAG=$(git tag -l "handover-*" | sort | tail -1)
    ok "handover tag present: $LATEST_TAG"
else
    bad "no handover tag found — handover may be incomplete"
fi

echo
echo "=== Toolchain ==="
command -v python3 >/dev/null && ok "python3: $(python3 --version 2>&1)" || bad "python3 missing"
command -v bd      >/dev/null && ok "bd (beads): $(bd version 2>/dev/null | head -1 || echo present)" || bad "bd missing — issue tracking unavailable"
command -v docker  >/dev/null && ok "docker present" || bad "docker missing (only needed for full-stack runs)"
command -v ruff    >/dev/null && ok "ruff present" || echo "  ⚠️  ruff missing (lint won't run locally)"
command -v mypy    >/dev/null && ok "mypy present" || echo "  ⚠️  mypy missing (type-check won't run locally)"

echo
echo "=== Project files ==="
[ -f pyproject.toml ]   && ok "pyproject.toml present"   || bad "pyproject.toml missing"
[ -f CLAUDE.md ]        && ok "CLAUDE.md present"        || bad "CLAUDE.md missing"
[ -f README.md ]        && ok "README.md present"        || bad "README.md missing"
[ -f docker-compose.yml ] && ok "docker-compose.yml present" || bad "docker-compose.yml missing"
[ -d tests/integration ] && ok "tests/integration/ present" || bad "tests/integration/ missing"
[ -d app/i18n ]         && ok "app/i18n/ present (DE+EN)" || bad "app/i18n/ missing"

echo
echo "=== bd state ==="
if command -v bd >/dev/null; then
    READY=$(bd ready 2>/dev/null | grep -c "^○" || echo 0)
    echo "  ℹ️  bd ready: $READY issue(s) available"
    echo "  ℹ️  next:    run 'bd ready' to see queue, '/set-course' to plan new epic"
fi

echo
echo "=== Result: $PASS passed, $FAIL failed ==="
if [ "$FAIL" -eq 0 ]; then
    echo "Klar zum Entern, Käpt'n. ⚓"
    exit 0
else
    echo "Punkte zum Klären stehen oben."
    exit 1
fi
