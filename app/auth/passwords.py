import hashlib
import hmac
import os

import bcrypt


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


# Pre-computed bcrypt hash (rounds=12, same cost as a real password) for login
# timing-equalization. Computed once at import. See verify_password_or_dummy.
_DUMMY_PASSWORD_HASH = hash_password("timing-equalization-decoy")


def verify_password_or_dummy(password: str, password_hash: str | None) -> bool:
    """Constant-cost password check for login (account-enumeration guard, 0y7).

    Always runs exactly one bcrypt verify. When ``password_hash`` is ``None`` (no
    such user) it verifies against a dummy hash and returns ``False`` — so a
    missing account and a wrong password take the same time, leaking nothing about
    account existence via response timing. Callers MUST invoke this on its own line
    (not behind a short-circuit ``or``) for the equalization to hold.
    """
    if password_hash is None:
        verify_password(password, _DUMMY_PASSWORD_HASH)
        return False
    return verify_password(password, password_hash)


def hash_token(token: str) -> tuple[bytes, bytes]:
    salt = os.urandom(32)
    token_hash = hashlib.sha256(token.encode() + salt).digest()
    return token_hash, salt


def verify_token(token: str, token_hash: bytes, token_salt: bytes) -> bool:
    expected = hashlib.sha256(token.encode() + token_salt).digest()
    return hmac.compare_digest(expected, token_hash)
