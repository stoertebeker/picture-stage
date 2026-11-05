"""Bounded, signed ALTCHA v2 admission shared by both public signup paths."""

import base64
import binascii
import json
import math
import re
import time
from datetime import UTC, datetime
from typing import Any

from altcha import Payload, create_challenge, verify_solution
from fastapi import HTTPException
from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import CaptchaRedemption
from app.security.captcha_config import captcha_secret

PROOF_MAX_BYTES = 8192
CHALLENGE_TTL_SECONDS = 300
LEDGER_CAPACITY = 10_000
_HEX32 = re.compile(r"[0-9a-f]{32}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")


class CaptchaError(HTTPException):
    def __init__(self, unavailable: bool = False) -> None:
        self.message_key = "auth.captcha_unavailable" if unavailable else "auth.captcha_invalid"
        super().__init__(
            status_code=503 if unavailable else 422,
            detail="Signup verification is temporarily unavailable. Please retry."
            if unavailable
            else "Complete a fresh signup verification and retry.",
        )


def _secret() -> str:
    try:
        return captcha_secret(settings.altcha_secret_key, settings.secret_key)
    except (ValueError, UnicodeError) as exc:
        raise CaptchaError(unavailable=True) from exc


def signup_challenge() -> dict[str, Any]:
    issued_at = int(time.time())
    return create_challenge(
        algorithm="PBKDF2/SHA-256",
        cost=5000,
        key_length=32,
        key_prefix="00",
        expires_at=issued_at + CHALLENGE_TTL_SECONDS,
        data={"purpose": "picture-stage/signup", "issuedAt": issued_at},
        hmac_secret=_secret(),
    ).to_dict()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _object(value: Any, required: set[str], optional: set[str] | None = None) -> None:
    if type(value) is not dict or not required <= value.keys() or value.keys() - required - (optional or set()):
        raise ValueError("Unexpected proof shape")


def verify_signup_proof(proof: str | None) -> tuple[str, datetime]:
    """Validate our exact wire format before allowing the SDK to perform one KDF.

    SDK 2.3.0 verifies expiry and HMAC before deriving any key. The replay key
    is the verified challenge signature, independent of JSON/base64/solution.
    """
    secret = _secret()
    try:
        if type(proof) is not str or not 0 < len(proof) <= PROOF_MAX_BYTES or not proof.isascii():
            raise ValueError("Missing or oversized proof")
        raw = base64.b64decode(proof, validate=True)
        # Bound nesting before the JSON decoder (which otherwise accepts very
        # deeply nested attacker data). Brackets inside strings only overreject.
        if raw.count(b"{") + raw.count(b"[") > 8:
            raise ValueError("Excessive proof nesting")
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
        _object(data, {"challenge", "solution"})
        challenge, solution = data["challenge"], data["solution"]
        _object(challenge, {"parameters", "signature"})
        params = challenge["parameters"]
        _object(params, {"algorithm", "cost", "keyLength", "keyPrefix", "nonce", "salt", "expiresAt", "data"})
        _object(params["data"], {"purpose", "issuedAt"})
        _object(solution, {"counter", "derivedKey"}, {"time"})
        if (
            params["algorithm"] != "PBKDF2/SHA-256"
            or params["keyPrefix"] != "00"
            or type(params["cost"]) is not int
            or params["cost"] != 5000
            or type(params["keyLength"]) is not int
            or params["keyLength"] != 32
            or params["data"]["purpose"] != "picture-stage/signup"
        ):
            raise ValueError("Unexpected challenge policy")
        for value in (params["nonce"], params["salt"]):
            if type(value) is not str or _HEX32.fullmatch(value) is None:
                raise ValueError("Invalid randomness")
        for value in (challenge["signature"], solution["derivedKey"]):
            if type(value) is not str or _HEX64.fullmatch(value) is None:
                raise ValueError("Invalid signature or key")
        issued_at, expires_at = params["data"]["issuedAt"], params["expiresAt"]
        now = time.time()
        if (
            type(issued_at) is not int
            or type(expires_at) is not int
            or expires_at - issued_at != CHALLENGE_TTL_SECONDS
            or not issued_at <= now < expires_at
            or type(solution["counter"]) is not int
            or not 0 <= solution["counter"] < 2**32
        ):
            raise ValueError("Invalid freshness or counter")
        if "time" in solution and (
            type(solution["time"]) not in (int, float)
            or not math.isfinite(solution["time"])
            or not 0 <= solution["time"] <= CHALLENGE_TTL_SECONDS * 1000
        ):
            raise ValueError("Invalid solution time")
        if not verify_solution(Payload.from_dict(data), secret).verified:
            raise ValueError("Unverified solution")
        return challenge["signature"], datetime.fromtimestamp(expires_at, UTC)
    except (ValueError, TypeError, KeyError, UnicodeError, binascii.Error, RecursionError, OverflowError) as exc:
        raise CaptchaError() from exc


async def require_signup_captcha(proof: str | None, db: AsyncSession) -> None:
    if not settings.captcha_enabled:
        return
    signature, expires_at = verify_signup_proof(proof)
    try:
        # One brief global PG gate bounds total rows across processes. No KDF,
        # bcrypt, account access or mail happens while holding this gate.
        gate = await db.scalar(text("SELECT pg_try_advisory_xact_lock(1347635011, 1128353876)"))
        if not gate:
            raise CaptchaError(unavailable=True)
        now = datetime.now(UTC)
        if now >= expires_at:
            raise CaptchaError()
        expired = (
            select(CaptchaRedemption.signature)
            .where(CaptchaRedemption.expires_at <= now)
            .order_by(CaptchaRedemption.expires_at)
            .limit(256)
        )
        await db.execute(delete(CaptchaRedemption).where(CaptchaRedemption.signature.in_(expired)))
        count = await db.scalar(select(func.count()).select_from(CaptchaRedemption))
        if count is None or count >= LEDGER_CAPACITY:
            raise CaptchaError(unavailable=True)
        redeemed = await db.scalar(
            insert(CaptchaRedemption)
            .values(signature=signature, expires_at=expires_at)
            .on_conflict_do_nothing(index_elements=[CaptchaRedemption.signature])
            .returning(CaptchaRedemption.signature)
        )
        if redeemed is None:
            raise CaptchaError()
        # Durable redemption precedes every signup effect, including duplicate
        # account timing equalization. A failed signup cannot reuse its proof.
        await db.commit()
    except CaptchaError:
        await db.rollback()
        raise
    except SQLAlchemyError as exc:
        try:
            await db.rollback()
        except SQLAlchemyError:
            # A broken connection may make rollback fail too. Preserve the
            # public fail-closed response; the session owner still closes it.
            pass
        raise CaptchaError(unavailable=True) from exc
