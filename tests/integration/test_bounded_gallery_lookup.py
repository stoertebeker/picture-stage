"""Legacy progress bounds each request while retaining every old credential."""

import uuid
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import event, select, text, update
from sqlalchemy.orm import Session

from app.auth.lookup_progress import LookupProgressMiddleware
from app.auth.passwords import hash_password, hash_token, verify_token
from app.auth.token_lookup import token_locator
from app.db.models import Gallery, GalleryStatus, ShareSession, UserStatus
from app.db.session import get_db
from app.guest import service
from app.guest.router import router
from app.main import app
from app.security.rate_limit import limiter

pytestmark = pytest.mark.usefixtures("gallery_lookup_classifier")


async def _population(db, owner, *, count=25, inactive=True):
    rows = []
    for number in range(1, count + 1):
        raw = "legacy-target" if number == count else f"other-{number}"
        digest, salt = hash_token(raw)
        rows.append(
            Gallery(
                id=uuid.UUID(int=number),
                owner_id=owner.id,
                name=f"legacy-{number}",
                status=GalleryStatus.draft if inactive and number % 2 else GalleryStatus.shared,
                share_token_hash=digest,
                share_token_salt=salt,
            )
        )
    rows[-1].status = GalleryStatus.shared
    db.add_all(rows)
    # Indexed active/inactive rows are absent from the legacy partial index.
    for number in range(100, 200):
        raw = f"indexed-{number}"
        digest, salt = hash_token(raw)
        db.add(
            Gallery(
                id=uuid.UUID(int=number),
                owner_id=owner.id,
                name=raw,
                status=GalleryStatus.draft,
                share_token=raw,
                share_token_hash=digest,
                share_token_salt=salt,
                share_token_lookup=token_locator(raw),
            )
        )
    await db.commit()
    return rows[-1]


async def _until_resolved(client, path, *, method="get", **kwargs):
    statuses = []
    for _ in range(40):
        response = await getattr(client, method)(path, **kwargs)
        statuses.append(response.status_code)
        if response.status_code != 503:
            return response, statuses
        assert response.headers["X-Picture-Stage-Lookup"] == "before-effects"
        assert response.headers["Retry-After"] == "1"
        assert "gallery_lookup=" in response.headers["set-cookie"]
    raise AssertionError("lookup did not finish")


async def test_windows_precede_access_filters_and_hints_reverify(client, db, owner, monkeypatch):
    monkeypatch.setattr(service, "LOOKUP_WINDOW", 3)
    target = await _population(db, owner)
    loaded = []

    def capture(_session, instance):
        if isinstance(instance, Gallery):
            loaded.append(instance.id)

    event.listen(Session, "loaded_as_persistent", capture)
    try:
        statuses = []
        for _ in range(12):
            loaded.clear()
            with patch.object(service, "verify_token", wraps=verify_token) as checks:
                response = await client.get("/g/legacy-target")
            statuses.append(response.status_code)
            # A successful route also locks/reloads the selected gallery.
            assert len(set(loaded)) <= 3
            assert checks.call_count <= 5
            if response.status_code != 503:
                break
            assert await db.scalar(select(ShareSession.id)) is None
        assert statuses == [503] * 8 + [200]
        # The signed PK hint reaches the late hash-only row without rescanning.
        with patch.object(service, "verify_token", wraps=verify_token) as checks:
            assert (await client.get("/g/legacy-target/images")).status_code == 200
        assert checks.call_count <= 2
        owner.status = UserStatus.disabled
        await db.commit()
        response, _ = await _until_resolved(client, "/g/legacy-target")
        assert response.status_code == 404
        owner.status = UserStatus.active
        new_hash, new_salt = hash_token("rotated")
        await db.execute(
            update(Gallery)
            .where(Gallery.id == target.id)
            .values(
                share_token_hash=new_hash,
                share_token_salt=new_salt,
            )
        )
        await db.commit()
        response, _ = await _until_resolved(client, "/g/legacy-target/images")
        assert response.status_code == 404
    finally:
        event.remove(Session, "loaded_as_persistent", capture)


@pytest.mark.parametrize(
    "path", ["/g/missing", "/g/missing/images", "/g/missing/selections", "/g/missing/gallery-images"]
)
async def test_invalid_tokens_exhaust_bounded_windows(client, db, owner, monkeypatch, path):
    monkeypatch.setattr(service, "LOOKUP_WINDOW", 3)
    await _population(db, owner, count=10, inactive=False)
    for expected in (503, 503, 503, 404):
        with patch.object(service, "verify_token", wraps=verify_token) as checks:
            response = await client.get(path, headers={"HX-Request": "true"})
        assert response.status_code == expected
        assert checks.call_count <= 3
    assert await db.scalar(select(ShareSession.id)) is None


async def test_indexed_fast_path_and_duplicate_locator_window(client, db, owner, monkeypatch):
    monkeypatch.setattr(service, "LOOKUP_WINDOW", 3)
    target = await _population(db, owner, count=10)
    digest, salt = hash_token("direct")
    target.share_token = "direct"
    target.share_token_hash, target.share_token_salt = digest, salt
    await db.commit()
    with patch.object(service, "verify_token", wraps=verify_token) as checks:
        assert (await client.get("/g/direct")).status_code == 200
    assert checks.call_count == 2  # resolver + lifecycle fence
    # A corrupt/old index with duplicate locator rows cannot amplify one request.
    await db.execute(text("ALTER TABLE galleries DISABLE TRIGGER galleries_classify_token_lookup"))
    await db.execute(update(Gallery).where(Gallery.id < target.id).values(share_token_lookup=token_locator("direct")))
    await db.commit()
    client.cookies.clear()
    with patch.object(service, "verify_token", wraps=verify_token) as checks:
        response = await client.get("/g/direct")
    assert response.status_code == 503
    assert checks.call_count <= 3
    response, statuses = await _until_resolved(client, "/g/direct")
    assert response.status_code == 200 and len(statuses) == 3


@pytest.mark.parametrize("root", ["", "/mount"])
@pytest.mark.parametrize("alias", ["/g/%6cegacy-target", "/%67/legacy-target"])
async def test_encoded_legacy_links_finish_their_windows(db, owner, monkeypatch, root, alias):
    monkeypatch.setattr(service, "LOOKUP_WINDOW", 3)
    await _population(db, owner, count=7)
    async with AsyncClient(transport=ASGITransport(app=app, root_path=root), base_url="http://test") as client:
        response, statuses = await _until_resolved(client, root + alias)
        assert statuses == [503, 503, 200]
        assert response.status_code == 200
        cookie = next(cookie for cookie in client.cookies.jar if cookie.name == "gallery_lookup")
        # CookieJar uppercases percent-escape hex digits without decoding them.
        assert cookie.path == (root + alias).replace("%6c", "%6C")
        assert "%" in cookie.path


@pytest.mark.parametrize("html", [True, False])
async def test_password_lookup_retries_do_not_consume_password_attempt_budget(client, db, owner, monkeypatch, html):
    monkeypatch.setattr(service, "LOOKUP_WINDOW", 3)
    target = await _population(db, owner)
    target.password_hash = hash_password("correct")
    await db.commit()
    if html:
        page, _ = await _until_resolved(client, "/g/legacy-target")
        import re

        csrf = re.search(r'name="guest_csrf_token" value="([^"]+)"', page.text).group(1)
        # Keep the password challenge but force another full legacy resolution.
        client.cookies.delete("gallery_lookup", path="/g/legacy-target")
        kwargs = {"data": {"password": "correct", "guest_csrf_token": csrf}}
        http_client = client
    else:
        api = FastAPI()
        api.state.limiter = limiter
        api.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
        api.add_middleware(LookupProgressMiddleware)
        api.include_router(router)
        api.dependency_overrides[get_db] = app.dependency_overrides[get_db]
        http_client = AsyncClient(transport=ASGITransport(app=api), base_url="http://test")
        kwargs = {"json": {"password": "correct"}}
    try:
        response, statuses = await _until_resolved(
            http_client, "/g/legacy-target/verify-password", method="post", **kwargs
        )
        assert statuses == [503] * 8 + [200]
        assert response.status_code == 200
        # Actual failed password verifications retain their five-attempt cap.
        if html:
            kwargs["data"]["password"] = "wrong"
        else:
            kwargs["json"]["password"] = "wrong"
        statuses = [
            (await http_client.post("/g/legacy-target/verify-password", **kwargs)).status_code for _ in range(5)
        ]
        assert statuses == [401] * 4 + [429]
    finally:
        if not html:
            await http_client.aclose()
