"""Integration tests for the admin user-management API.

S2a (this file, read-only): GET /api/v1/admin/users + /pending-signups/count.
Run against real PostgreSQL in CI (the sandbox cannot reach the DB).
"""

from app.db.models import Gallery, PendingSignup, User, UserStatus
from tests.integration.conftest import make_user


async def _add_pending(db, email: str) -> None:
    db.add(
        PendingSignup(
            email=email,
            password_hash="x",
            verification_token_hash=b"x",
            verification_token_salt=b"x",
        )
    )
    await db.commit()


async def _add_gallery(db, owner: User, name: str) -> None:
    db.add(Gallery(owner_id=owner.id, name=name))
    await db.commit()


# --- GET /api/v1/admin/users ---


async def test_list_users_returns_all_accounts(client, db, auth_headers):
    admin = await make_user(db, "admin@test.local", status=UserStatus.admin)
    await make_user(db, "active@test.local", status=UserStatus.active)
    await make_user(db, "disabled@test.local", status=UserStatus.disabled)

    resp = await client.get("/api/v1/admin/users", headers=auth_headers(admin))

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert body["page"] == 1
    assert {u["email"] for u in body["users"]} == {
        "admin@test.local",
        "active@test.local",
        "disabled@test.local",
    }


async def test_list_users_status_filter(client, db, auth_headers):
    admin = await make_user(db, "admin@test.local", status=UserStatus.admin)
    await make_user(db, "active@test.local", status=UserStatus.active)
    await make_user(db, "disabled@test.local", status=UserStatus.disabled)

    resp = await client.get(
        "/api/v1/admin/users",
        params={"status": "disabled"},
        headers=auth_headers(admin),
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["users"][0]["email"] == "disabled@test.local"


async def test_list_users_includes_gallery_count(client, db, auth_headers):
    admin = await make_user(db, "admin@test.local", status=UserStatus.admin)
    user = await make_user(db, "withgal@test.local", status=UserStatus.active)
    await _add_gallery(db, user, "G1")
    await _add_gallery(db, user, "G2")

    resp = await client.get("/api/v1/admin/users", headers=auth_headers(admin))

    body = resp.json()
    with_gal = next(u for u in body["users"] if u["email"] == "withgal@test.local")
    admin_rec = next(u for u in body["users"] if u["email"] == "admin@test.local")
    assert with_gal["galleries_count"] == 2
    assert admin_rec["galleries_count"] == 0


async def test_list_users_pagination(client, db, auth_headers):
    admin = await make_user(db, "admin@test.local", status=UserStatus.admin)
    for i in range(3):
        await make_user(db, f"u{i}@test.local", status=UserStatus.active)

    resp = await client.get(
        "/api/v1/admin/users",
        params={"page": 1, "per_page": 2},
        headers=auth_headers(admin),
    )

    body = resp.json()
    assert body["total"] == 4  # 3 + admin
    assert len(body["users"]) == 2
    assert body["per_page"] == 2


async def test_list_users_requires_admin(client, db, auth_headers):
    non_admin = await make_user(db, "active@test.local", status=UserStatus.active)
    resp = await client.get("/api/v1/admin/users", headers=auth_headers(non_admin))
    assert resp.status_code == 403


# --- GET /api/v1/admin/pending-signups/count ---


async def test_pending_signups_count(client, db, auth_headers):
    admin = await make_user(db, "admin@test.local", status=UserStatus.admin)
    await _add_pending(db, "p1@test.local")
    await _add_pending(db, "p2@test.local")

    resp = await client.get("/api/v1/admin/pending-signups/count", headers=auth_headers(admin))

    assert resp.status_code == 200
    assert resp.json()["count"] == 2


async def test_pending_signups_count_zero(client, db, auth_headers):
    admin = await make_user(db, "admin@test.local", status=UserStatus.admin)
    resp = await client.get("/api/v1/admin/pending-signups/count", headers=auth_headers(admin))
    assert resp.status_code == 200
    assert resp.json()["count"] == 0


async def test_pending_signups_count_requires_admin(client, db, auth_headers):
    non_admin = await make_user(db, "active@test.local", status=UserStatus.active)
    resp = await client.get("/api/v1/admin/pending-signups/count", headers=auth_headers(non_admin))
    assert resp.status_code == 403
