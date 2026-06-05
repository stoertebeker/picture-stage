"""Tests for frontend legal pages and Markdown rendering."""

from pathlib import Path

from app.frontend.legal import _minimal_md_to_html


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LEGAL_ROOT = PROJECT_ROOT / "app" / "templates" / "legal"


def _template(name: str) -> str:
    return (LEGAL_ROOT / name).read_text()


def test_legal_templates_use_editorial_dark_layout() -> None:
    for name in ("impressum.html", "datenschutz.html"):
        html = _template(name)
        assert "bg-surface-base" in html
        assert "font-display" in html
        assert "max-w-3xl" in html
        assert "border-border-subtle" in html


def test_legal_templates_keep_content_safe_only_after_renderer() -> None:
    for name in ("impressum.html", "datenschutz.html"):
        html = _template(name)
        assert "content | safe" in html
        assert "has_content" in html
        assert "config_security_note" in html


def test_legal_templates_have_navigation_and_config_fallbacks() -> None:
    import json

    impressum = _template("impressum.html")
    datenschutz = _template("datenschutz.html")

    # Load i18n to verify config hints contain env var references
    i18n_de = json.loads((PROJECT_ROOT / "app" / "i18n" / "de.json").read_text())

    assert "/legal/datenschutz" in impressum
    assert "/legal/impressum" in datenschutz
    assert "LEGAL_IMPRESSUM_PATH" in i18n_de["legal"]["impressum_config_hint"]
    assert "LEGAL_DATENSCHUTZ_PATH" in i18n_de["legal"]["datenschutz_config_hint"]


def test_minimal_markdown_renderer_strips_unsafe_html_and_links() -> None:
    rendered = _minimal_md_to_html(
        "# Titel\n\n<script>alert(1)</script>\n\n[bad](javascript:alert(1))\n\n[ok](https://example.invalid)"
    )

    assert "<script>" not in rendered
    assert "javascript:" not in rendered
    assert "<h1>Titel</h1>" in rendered
    assert '<a href="https://example.invalid" rel="noopener noreferrer">ok</a>' in rendered
