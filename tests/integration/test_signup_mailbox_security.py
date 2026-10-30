"""HTTP and PostgreSQL regression coverage for single-mailbox signup."""

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from app.auth.passwords import hash_password
from app.config import settings
from app.db.models import PendingSignup, User, UserStatus
from app.notifications import service


@pytest.mark.parametrize("email", ["a@example.com,b@example.com", "a@example.com\r\nBcc: b@example.com"])
async def test_invalid_recipient_list_never_creates_signup_or_sends(client, db, monkeypatch, email):
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "send_verification_email_enabled", True)
    send = AsyncMock()
    monkeypatch.setattr(service.aiosmtplib, "send", send)
    page = await client.get("/signup")
    token = page.cookies["csrf_token"]
    response = await client.post(
        "/signup",
        data={"email": email, "password": "good-password", "password_confirm": "good-password", "csrf_token": token},
    )
    api = await client.post("/api/v1/auth/signup", json={"email": email, "password": "good-password"})
    assert response.status_code == 422
    assert api.status_code == 422
    assert await db.scalar(select(func.count()).select_from(PendingSignup)) == 0
    send.assert_not_awaited()


async def test_valid_browser_signup_persists_and_sends_to_one_mailbox(client, db, monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "send_verification_email_enabled", True)
    monkeypatch.setattr(settings, "notify_admins_on_signup", False)
    send = AsyncMock()
    monkeypatch.setattr(service.aiosmtplib, "send", send)
    page = await client.get("/signup")
    token = page.cookies["csrf_token"]
    response = await client.post(
        "/signup",
        data={
            "email": "person@EXAMPLE.COM",
            "password": "good-password",
            "password_confirm": "good-password",
            "csrf_token": token,
        },
    )
    assert response.status_code == 200
    assert await db.scalar(select(PendingSignup.email)) == "person@EXAMPLE.COM"
    send.assert_awaited_once()
    assert send.await_args.kwargs["recipients"] == ["person@example.com"]


async def test_mixed_case_browser_signup_can_log_in_after_approval(client, db, monkeypatch):
    monkeypatch.setattr(settings, "send_verification_email_enabled", False)
    email = "person@EXAMPLE.COM"
    page = await client.get("/signup")
    token = page.cookies["csrf_token"]
    signup = await client.post(
        "/signup",
        data={"email": email, "password": "good-password", "password_confirm": "good-password", "csrf_token": token},
    )
    assert signup.status_code == 200
    pending = (await db.execute(select(PendingSignup))).scalar_one()
    assert pending.email == email
    db.add(User(email=pending.email, password_hash=pending.password_hash, status=UserStatus.active))
    await db.delete(pending)
    await db.commit()
    login_page = await client.get("/login")
    csrf = login_page.cookies.get("csrf_token") or client.cookies["csrf_token"]
    login = await client.post("/login", data={"email": email, "password": "good-password", "csrf_token": csrf})
    assert login.status_code == 303
    assert login.headers["location"] == "/dashboard"


@pytest.mark.parametrize("existing_kind", ["user", "pending"])
async def test_mixed_case_existing_account_is_not_duplicated(client, db, monkeypatch, existing_kind):
    email = "person@EXAMPLE.COM"
    original_hash = hash_password("original-password")
    if existing_kind == "user":
        db.add(User(email=email, password_hash=original_hash, status=UserStatus.active))
    else:
        db.add(
            PendingSignup(
                email=email, password_hash=original_hash, verification_token_hash=b"h", verification_token_salt=b"s"
            )
        )
    await db.commit()
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "send_verification_email_enabled", True)
    send = AsyncMock()
    monkeypatch.setattr(service.aiosmtplib, "send", send)
    page = await client.get("/signup")
    response = await client.post(
        "/signup",
        data={
            "email": email,
            "password": "other-password",
            "password_confirm": "other-password",
            "csrf_token": page.cookies["csrf_token"],
        },
    )
    assert response.status_code == 200
    assert await db.scalar(select(func.count()).select_from(PendingSignup)) == (existing_kind == "pending")
    if existing_kind == "user":
        assert await db.scalar(select(User.password_hash)) == original_hash
    else:
        assert await db.scalar(select(PendingSignup.password_hash)) == original_hash
    send.assert_not_awaited()
