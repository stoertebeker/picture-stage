import hashlib
import hmac
import time
from urllib.parse import urlencode

from app.config import settings


def sign_url(path: str, expires_in: int = 3600) -> str:
    expires = int(time.time()) + expires_in
    payload = f"{path}:{expires}"
    sig = hmac.new(settings.hmac_secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    params = urlencode({"exp": expires, "sig": sig})
    return f"{path}?{params}"


def verify_signed_url(path: str, exp: int, sig: str) -> bool:
    if time.time() > exp:
        return False
    payload = f"{path}:{exp}"
    expected = hmac.new(settings.hmac_secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)
