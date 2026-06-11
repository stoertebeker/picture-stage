from contextvars import ContextVar
from functools import partial
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.i18n import t as _translate

# Stores the current request's locale so imported Jinja2 macros (which have no
# access to the template context) can still call t() via the env global.
_locale_ctx: ContextVar[str] = ContextVar("locale", default="de")


def _global_t(key: str, **kwargs: Any) -> str:
    """Jinja2 env-global t() for imported macros — reads locale from ContextVar."""
    return _translate(key, locale=_locale_ctx.get(), **kwargs)


templates = Jinja2Templates(directory="app/templates")


def _asset_url(path: str) -> str:
    """Build a cache-busted URL for a static asset.

    Appends ?v=<asset_version> so a fresh build (new ASSET_VERSION) yields a new
    URL, busting browser, CDN and origin caches together. Only JS/CSS use this;
    fonts intentionally stay un-versioned to avoid preload/url() mismatches.
    Usage in templates: {{ asset('js/alpine.min.js') }}.
    """
    return f"/static/{path}?v={settings.asset_version}"


def _t_for_request(request: Request, key: str, **kwargs: Any) -> str:
    """Request-aware translation function for use in templates.

    Automatically uses the locale from request.state.locale (set by LocaleMiddleware).
    Usage in templates: {{ t('nav.dashboard') }} or {{ t('gallery.images_count', count=5) }}
    """
    locale = getattr(request.state, "locale", "de") if hasattr(request, "state") else "de"
    return _translate(key, locale=locale, **kwargs)


# Monkey-patch the _build_context to inject locale and t() into every template render.
_original_TemplateResponse = templates.TemplateResponse


def _patched_template_response(
    request: Request, name: str, context: dict[str, Any] | None = None, **kwargs: Any
) -> Any:
    """Wrap TemplateResponse to inject locale and request-aware t() into context."""
    if context is None:
        context = {}
    context.setdefault("request", request)
    locale = getattr(request.state, "locale", "de") if hasattr(request, "state") else "de"
    context.setdefault("locale", locale)
    _locale_ctx.set(locale)
    # Provide request-aware t() bound to the current request
    context.setdefault("t", partial(_t_for_request, request))
    # Expose the logged-in user (set by get_user_from_cookie) for the nav/admin menu.
    current_user = getattr(request.state, "current_user", None) if hasattr(request, "state") else None
    context.setdefault("current_user", current_user)
    return _original_TemplateResponse(request, name, context, **kwargs)


templates.TemplateResponse = _patched_template_response  # type: ignore[assignment]

# Also register in Jinja2 globals for templates that might use it outside TemplateResponse
templates.env.globals["supported_locales"] = ("de", "en")
templates.env.globals["asset"] = _asset_url
templates.env.globals["t"] = _global_t
