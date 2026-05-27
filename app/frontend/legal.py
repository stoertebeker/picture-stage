"""Frontend legal routes: Impressum and Datenschutzerklaerung."""

import html
import logging
import re
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.config import settings
from app.frontend.deps import templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/legal", tags=["frontend-legal"])

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"\*(.+?)\*")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _minimal_md_to_html(text: str) -> str:
    """Convert basic Markdown to HTML without external dependencies.

    Supports headings, bold, italic, links, and paragraphs.
    All input is HTML-escaped first to prevent XSS.
    """
    text = _HTML_TAG_RE.sub("", text)
    text = html.escape(text)
    text = _HEADING_RE.sub(
        lambda m: f"<h{len(m.group(1))}>{m.group(2)}</h{len(m.group(1))}>", text
    )
    text = _BOLD_RE.sub(r"<strong>\1</strong>", text)
    text = _ITALIC_RE.sub(r"<em>\1</em>", text)
    def _safe_link(m: re.Match[str]) -> str:
        label, url = m.group(1), m.group(2)
        url_lower = url.strip().lower()
        if url_lower.startswith(("javascript:", "data:", "vbscript:")):
            return label
        if not (
            url_lower.startswith(("http:", "https:", "mailto:", "tel:"))
            or url.startswith(("/", "#", "?"))
        ):
            return label
        return f'<a href="{url}" rel="noopener noreferrer">{label}</a>'

    text = _LINK_RE.sub(_safe_link, text)
    paragraphs = re.split(r"\n{2,}", text.strip())
    parts = []
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        if p.startswith("<h"):
            parts.append(p)
        else:
            parts.append(f"<p>{p.replace(chr(10), '<br>')}</p>")
    return "\n".join(parts)


def _render_legal_page(md_path: str) -> str:
    path = Path(md_path)
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8")
    return _minimal_md_to_html(text)


@router.get("/impressum", response_class=HTMLResponse)
async def impressum_page(request: Request) -> HTMLResponse:
    html_content = _render_legal_page(settings.legal_impressum_path)
    return templates.TemplateResponse(
        request,
        "legal/impressum.html",
        {
            "request": request,
            "content": html_content,
            "has_content": bool(html_content),
        },
    )


@router.get("/datenschutz", response_class=HTMLResponse)
async def datenschutz_page(request: Request) -> HTMLResponse:
    html_content = _render_legal_page(settings.legal_datenschutz_path)
    return templates.TemplateResponse(
        request,
        "legal/datenschutz.html",
        {
            "request": request,
            "content": html_content,
            "has_content": bool(html_content),
        },
    )
