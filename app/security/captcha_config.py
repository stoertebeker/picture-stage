"""Pure CAPTCHA secret configuration; safe to import while Settings is built."""

import hashlib
import hmac


def captcha_secret(explicit_key: str, application_key: str) -> str:
    key = explicit_key if explicit_key else application_key
    if len(key.strip().encode("utf-8")) < 32 or key.strip().upper() in {"CHANGE_ME", "CHANGEME"}:
        raise ValueError("Enabled CAPTCHA requires a strong ALTCHA_SECRET_KEY or SECRET_KEY (at least 32 bytes)")
    if explicit_key:
        return explicit_key
    return hmac.new(key.encode("utf-8"), b"picture-stage/signup/altcha/v2", hashlib.sha256).hexdigest()
