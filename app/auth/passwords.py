import hashlib
import hmac
import os

import bcrypt


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def hash_token(token: str) -> tuple[bytes, bytes]:
    salt = os.urandom(32)
    token_hash = hashlib.sha256(token.encode() + salt).digest()
    return token_hash, salt


def verify_token(token: str, token_hash: bytes, token_salt: bytes) -> bool:
    expected = hashlib.sha256(token.encode() + token_salt).digest()
    return hmac.compare_digest(expected, token_hash)
