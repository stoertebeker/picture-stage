"""Internationalization module for Picture-Stage.

Provides translation lookups with dot-notation keys and locale detection.
Fallback chain: requested locale -> "de" -> key itself (never crashes).
"""

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import Request

logger = logging.getLogger(__name__)

_TRANSLATIONS: dict[str, dict[str, Any]] = {}
_DEFAULT_LOCALE = "de"
_SUPPORTED_LOCALES = ("de", "en")
_I18N_DIR = Path(__file__).parent


def _load_translations() -> None:
    """Load all JSON translation files from the i18n directory."""
    for locale in _SUPPORTED_LOCALES:
        path = _I18N_DIR / f"{locale}.json"
        if path.exists():
            with open(path, encoding="utf-8") as f:
                _TRANSLATIONS[locale] = json.load(f)
            logger.info("Loaded %d top-level keys for locale '%s'", len(_TRANSLATIONS[locale]), locale)
        else:
            logger.warning("Translation file not found: %s", path)
            _TRANSLATIONS[locale] = {}


def _resolve_key(data: dict[str, Any], key: str) -> str | None:
    """Resolve a dot-notation key in a nested dict. Returns None if not found."""
    parts = key.split(".")
    current: Any = data
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return str(current) if current is not None else None


def t(key: str, locale: str = _DEFAULT_LOCALE, **kwargs: Any) -> str:
    """Translate a dot-notation key for the given locale.

    Fallback chain: requested locale -> default locale ("de") -> key itself.
    Supports Python format string interpolation via kwargs.
    """
    if not _TRANSLATIONS:
        _load_translations()

    # Try requested locale
    result = _resolve_key(_TRANSLATIONS.get(locale, {}), key)

    # Fallback to default locale
    if result is None and locale != _DEFAULT_LOCALE:
        result = _resolve_key(_TRANSLATIONS.get(_DEFAULT_LOCALE, {}), key)

    # Fallback to key itself
    if result is None:
        return key

    # Apply format string interpolation if kwargs provided
    if kwargs:
        try:
            return result.format(**kwargs)
        except (KeyError, IndexError):
            return result

    return result


def detect_locale(request: Request) -> str:
    """Detect the preferred locale from the request.

    Priority:
    1. Authenticated user's locale preference (from request.state.user)
    2. Cookie "lang"
    3. Accept-Language header
    4. Default: "de"
    """
    # 1. Check authenticated user's locale
    user = getattr(request.state, "user", None) if hasattr(request, "state") else None
    if user is not None:
        user_locale = getattr(user, "locale", None)
        if user_locale and user_locale in _SUPPORTED_LOCALES:
            return user_locale

    # 2. Check "lang" cookie
    lang_cookie = request.cookies.get("lang")
    if lang_cookie and lang_cookie in _SUPPORTED_LOCALES:
        return lang_cookie

    # 3. Parse Accept-Language header
    accept_lang = request.headers.get("accept-language", "")
    if accept_lang:
        for part in accept_lang.split(","):
            lang = part.split(";")[0].strip().lower()
            # Match exact or prefix (e.g., "de-DE" -> "de")
            lang_prefix = lang.split("-")[0]
            if lang_prefix in _SUPPORTED_LOCALES:
                return lang_prefix

    # 4. Default
    return _DEFAULT_LOCALE


def get_supported_locales() -> tuple[str, ...]:
    """Return tuple of supported locale codes."""
    return _SUPPORTED_LOCALES


def reload_translations() -> None:
    """Force reload of translation files. Useful for development."""
    _TRANSLATIONS.clear()
    _load_translations()


# Load translations on module import
_load_translations()
