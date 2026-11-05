"""Real ALTCHA SDK proofs, bounded parsing, and both signup admission paths."""

import base64
import copy
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from altcha import Challenge, Payload, create_challenge, solve_challenge
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.datastructures import FormData
from starlette.requests import Request

from app.auth.router import signup as api_signup
from app.auth.schemas import SignupRequest
from app.config import Settings, settings
from app.frontend.auth import signup_submit
from app.security import captcha
from app.security.captcha_config import captcha_secret
from app.security.rate_limit import limiter

KEY = "test-captcha-strong-key-0123456789abcdef"


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setattr(settings, "captcha_enabled", True)
    monkeypatch.setattr(settings, "altcha_secret_key", KEY)
    monkeypatch.setattr(limiter, "enabled", False)


@pytest.fixture(scope="module")
def solved():
    now = int(time.time())
    challenge = create_challenge(
        algorithm="PBKDF2/SHA-256",
        cost=5000,
        key_prefix="00",
        expires_at=now + 300,
        data={"purpose": "picture-stage/signup", "issuedAt": now},
        hmac_secret=KEY,
    )
    solution = solve_challenge(challenge)
    assert solution is not None
    return Payload(challenge, solution).to_dict()


def encode(data):
    return base64.b64encode(json.dumps(data).encode()).decode()


def test_real_sdk_proof_and_encoding_alias_share_identity(solved):
    signature, expires = captcha.verify_signup_proof(encode(solved))
    alias = base64.b64encode(json.dumps(solved, sort_keys=True, indent=1).encode()).decode()
    assert captcha.verify_signup_proof(alias) == (signature, expires)
    assert signature == solved["challenge"]["signature"]


def test_another_real_solution_has_same_redemption_identity(solved):
    challenge = Challenge.from_dict(solved["challenge"])
    alternative = solve_challenge(challenge, counter_start=solved["solution"]["counter"] + 1)
    assert alternative is not None
    first = captcha.verify_signup_proof(encode(solved))
    assert captcha.verify_signup_proof(Payload(challenge, alternative).to_base64()) == first


@pytest.mark.parametrize("proof", [None, "", "mock", "test", "a" * 8193, "é", "W10=", "e30=", "!!!!"])
def test_bad_proofs_rejected_without_sdk(proof):
    with patch.object(captcha, "verify_solution") as verify, pytest.raises(captcha.CaptchaError):
        captcha.verify_signup_proof(proof)
    verify.assert_not_called()


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        ("parameters", "algorithm", "SHA-256"),
        ("parameters", "cost", 2**60),
        ("parameters", "cost", True),
        ("parameters", "keyLength", 4096),
        ("parameters", "keyPrefix", ""),
        ("parameters", "nonce", "00"),
        ("parameters", "salt", "FF" * 16),
        ("parameters", "memoryCost", 1000000000),
        ("parameters", "expiresAt", None),
        ("parameters", "expiresAt", 1),
        ("solution", "counter", True),
        ("solution", "counter", -1),
        ("solution", "counter", 2**32),
        ("solution", "derivedKey", "00"),
        ("solution", "time", float("nan")),
        ("solution", "time", float("inf")),
        ("solution", "time", -1),
    ],
)
def test_unissued_parameters_and_types_never_reach_sdk(solved, section, key, value):
    data = copy.deepcopy(solved)
    target = data["challenge"]["parameters"] if section == "parameters" else data["solution"]
    target[key] = value
    with patch.object(captcha, "verify_solution") as verify, pytest.raises(captcha.CaptchaError):
        captcha.verify_signup_proof(encode(data))
    verify.assert_not_called()


def test_duplicate_keys_deep_json_legacy_and_test_are_rejected(solved):
    raw = json.dumps(solved).replace('"cost": 5000', '"cost": 5000, "cost": 5000')
    proofs = [
        base64.b64encode(raw.encode()).decode(),
        encode({"test": True}),
        encode({"algorithm": "SHA-256", "number": 0}),
        base64.b64encode(("[" * 1000 + "0" + "]" * 1000).encode()).decode(),
    ]
    for proof in proofs:
        with pytest.raises(captcha.CaptchaError):
            captcha.verify_signup_proof(proof)


@pytest.mark.parametrize("change", ["signature", "expiry", "purpose", "issuedAt", "solution"])
def test_tampering_and_expiry_never_perform_kdf(solved, change):
    data = copy.deepcopy(solved)
    if change == "signature":
        data["challenge"]["signature"] = "0" * 64
    elif change == "expiry":
        data["challenge"]["parameters"]["expiresAt"] += 1
    elif change in {"purpose", "issuedAt"}:
        data["challenge"]["parameters"]["data"][change] = "wrong"
    else:
        data["solution"]["derivedKey"] = "0" * 64
    if change == "solution":
        with pytest.raises(captcha.CaptchaError):
            captcha.verify_signup_proof(encode(data))
    else:
        with patch("altcha.v2.derive_key_pbkdf2", side_effect=AssertionError("KDF before signature")):
            with pytest.raises(captcha.CaptchaError):
                captcha.verify_signup_proof(encode(data))


def test_issued_parameters_and_expired_proof(solved, monkeypatch):
    challenge = captcha.signup_challenge()
    assert challenge["parameters"]["cost"] == 5000
    assert challenge["parameters"]["expiresAt"] - challenge["parameters"]["data"]["issuedAt"] == 300
    monkeypatch.setattr(captcha.time, "time", lambda: solved["challenge"]["parameters"]["expiresAt"])
    with pytest.raises(captcha.CaptchaError):
        captcha.verify_signup_proof(encode(solved))


@pytest.mark.parametrize("key", ["", "CHANGE_ME", "short", " " * 40])
def test_invalid_secret_fails_startup_and_runtime(key, monkeypatch):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment="development", captcha_enabled=True, secret_key=key, altcha_secret_key="")
    monkeypatch.setattr(settings, "altcha_secret_key", key)
    monkeypatch.setattr(settings, "secret_key", key)
    with pytest.raises(captcha.CaptchaError) as error:
        captcha.signup_challenge()
    assert error.value.status_code == 503


def test_secret_fallback_is_domain_separated_and_explicit_invalid_is_not_replaced():
    assert captcha_secret("", KEY) != KEY
    assert captcha_secret(KEY, "CHANGE_ME") == KEY
    with pytest.raises(ValueError):
        captcha_secret("short", KEY)


@pytest.mark.parametrize("path", ["api", "html"])
async def test_missing_proof_precedes_all_signup_effects(path):
    db = MagicMock(execute=AsyncMock(), add=MagicMock(), commit=AsyncMock())
    with patch("app.auth.router.hash_password") as api_hash, patch("app.frontend.auth.hash_password") as html_hash:
        if path == "api":
            with pytest.raises(captcha.CaptchaError):
                await api_signup(MagicMock(), SignupRequest(email="test@example.com", password="long-enough"), db)
        else:
            request = Request({"type": "http", "method": "POST", "path": "/signup", "headers": []})
            request._form = FormData(
                {"email": "test@example.com", "password": "long-enough", "password_confirm": "long-enough"}
            )
            with patch("app.frontend.auth.templates.TemplateResponse") as render:
                await signup_submit(request, db)
                assert render.call_args.kwargs["status_code"] == 422
        api_hash.assert_not_called()
        html_hash.assert_not_called()
    db.execute.assert_not_called()
    db.add.assert_not_called()
    db.commit.assert_not_called()


async def test_disabled_mode_does_not_parse_or_query(monkeypatch):
    monkeypatch.setattr(settings, "captcha_enabled", False)
    db = MagicMock()
    await captcha.require_signup_captcha("not-a-proof", db)
    db.scalar.assert_not_called()


async def test_unavailable_database_and_rollback_failure_remain_503(solved):
    db = MagicMock(
        scalar=AsyncMock(side_effect=SQLAlchemyError("connection unavailable")),
        rollback=AsyncMock(side_effect=SQLAlchemyError("rollback unavailable")),
        commit=AsyncMock(),
    )
    with pytest.raises(captcha.CaptchaError) as error:
        await captcha.require_signup_captcha(encode(solved), db)
    assert error.value.status_code == 503
    db.commit.assert_not_called()


def test_each_challenge_is_fresh_and_strong_application_key_can_supply_secret():
    configured = Settings(
        _env_file=None, environment="development", captcha_enabled=True, secret_key=KEY, altcha_secret_key=""
    )
    assert configured.captcha_enabled
    first = captcha.signup_challenge()
    second = captcha.signup_challenge()
    assert first["signature"] != second["signature"]
    assert first["parameters"]["nonce"] != second["parameters"]["nonce"]
