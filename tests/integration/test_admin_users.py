"""Integration tests for the admin user-management API.

S2a (this file, read-only): GET /api/v1/admin/users + /pending-signups/count.
Run against real PostgreSQL in CI (the sandbox cannot reach the DB).
"""

from sqlalchemy import func, select

from app.auth.passwords import verify_password
from app.db.models import AuditLog, Gallery, PendingSignup, User, UserStatus
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


# --- PATCH /api/v1/admin/users/{id}/status ---


async def test_promote_user_to_admin(client, db, auth_headers, verify_db):
    admin = await make_user(db, "admin@test.local", status=UserStatus.admin)
    target = await make_user(db, "u@test.local", status=UserStatus.active)

    resp = await client.patch(
        f"/api/v1/admin/users/{target.id}/status",
        json={"status": "admin"},
        headers=auth_headers(admin),
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "admin"
    refreshed = await verify_db.get(User, target.id)
    assert refreshed.status == UserStatus.admin


async def test_disable_and_reenable_user(client, db, auth_headers, verify_db):
    admin = await make_user(db, "admin@test.local", status=UserStatus.admin)
    target = await make_user(db, "u@test.local", status=UserStatus.active)

    resp = await client.patch(
        f"/api/v1/admin/users/{target.id}/status",
        json={"status": "disabled"},
        headers=auth_headers(admin),
    )
    assert resp.status_code == 200
    assert (await verify_db.get(User, target.id)).status == UserStatus.disabled


async def test_status_change_is_audited(client, db, auth_headers, verify_db):
    admin = await make_user(db, "admin@test.local", status=UserStatus.admin)
    target = await make_user(db, "u@test.local", status=UserStatus.active)

    await client.patch(
        f"/api/v1/admin/users/{target.id}/status",
        json={"status": "disabled"},
        headers=auth_headers(admin),
    )

    rows = (
        (await verify_db.execute(select(AuditLog).where(AuditLog.event_type == "user_status_changed"))).scalars().all()
    )
    assert len(rows) == 1
    assert rows[0].actor_user_id == admin.id
    assert rows[0].details["new_status"] == "disabled"


async def test_cannot_change_own_status(client, db, auth_headers):  # S1
    admin = await make_user(db, "admin@test.local", status=UserStatus.admin)
    resp = await client.patch(
        f"/api/v1/admin/users/{admin.id}/status",
        json={"status": "disabled"},
        headers=auth_headers(admin),
    )
    assert resp.status_code == 400


async def test_status_pending_rejected(client, db, auth_headers):
    admin = await make_user(db, "admin@test.local", status=UserStatus.admin)
    target = await make_user(db, "u@test.local", status=UserStatus.active)
    resp = await client.patch(
        f"/api/v1/admin/users/{target.id}/status",
        json={"status": "pending"},
        headers=auth_headers(admin),
    )
    assert resp.status_code == 400


async def test_status_change_requires_admin(client, db, auth_headers):
    non_admin = await make_user(db, "active@test.local", status=UserStatus.active)
    target = await make_user(db, "u@test.local", status=UserStatus.active)
    resp = await client.patch(
        f"/api/v1/admin/users/{target.id}/status",
        json={"status": "admin"},
        headers=auth_headers(non_admin),
    )
    assert resp.status_code == 403


# --- DELETE /api/v1/admin/users/{id} ---


async def test_delete_user_removes_account_and_galleries(client, db, auth_headers, verify_db):
    admin = await make_user(db, "admin@test.local", status=UserStatus.admin)
    target = await make_user(db, "u@test.local", status=UserStatus.active)
    await _add_gallery(db, target, "G1")
    await _add_gallery(db, target, "G2")

    resp = await client.delete(f"/api/v1/admin/users/{target.id}", headers=auth_headers(admin))

    assert resp.status_code == 204
    assert await verify_db.get(User, target.id) is None
    remaining = await verify_db.scalar(select(func.count()).select_from(Gallery).where(Gallery.owner_id == target.id))
    assert remaining == 0


async def test_delete_user_is_audited(client, db, auth_headers, verify_db):
    admin = await make_user(db, "admin@test.local", status=UserStatus.admin)
    target = await make_user(db, "u@test.local", status=UserStatus.active)

    await client.delete(f"/api/v1/admin/users/{target.id}", headers=auth_headers(admin))

    rows = (await verify_db.execute(select(AuditLog).where(AuditLog.event_type == "user_deleted"))).scalars().all()
    assert len(rows) == 1
    assert rows[0].details["target_email"] == "u@test.local"


async def test_cannot_delete_self(client, db, auth_headers, verify_db):  # S1
    admin = await make_user(db, "admin@test.local", status=UserStatus.admin)
    resp = await client.delete(f"/api/v1/admin/users/{admin.id}", headers=auth_headers(admin))
    assert resp.status_code == 400
    assert await verify_db.get(User, admin.id) is not None


async def test_delete_requires_admin(client, db, auth_headers, verify_db):
    non_admin = await make_user(db, "active@test.local", status=UserStatus.active)
    target = await make_user(db, "u@test.local", status=UserStatus.active)
    resp = await client.delete(f"/api/v1/admin/users/{target.id}", headers=auth_headers(non_admin))
    assert resp.status_code == 403
    assert await verify_db.get(User, target.id) is not None


# --- POST /api/v1/admin/users/{id}/reset-password ---


async def test_reset_password_sets_new_hash(client, db, auth_headers, verify_db):
    admin = await make_user(db, "admin@test.local", status=UserStatus.admin)
    target = await make_user(db, "u@test.local", status=UserStatus.active)

    resp = await client.post(
        f"/api/v1/admin/users/{target.id}/reset-password",
        json={"new_password": "brandnew-pw-123"},
        headers=auth_headers(admin),
    )

    assert resp.status_code == 204
    refreshed = await verify_db.get(User, target.id)
    assert verify_password("brandnew-pw-123", refreshed.password_hash)


async def test_reset_password_too_short_rejected(client, db, auth_headers):
    admin = await make_user(db, "admin@test.local", status=UserStatus.admin)
    target = await make_user(db, "u@test.local", status=UserStatus.active)
    resp = await client.post(
        f"/api/v1/admin/users/{target.id}/reset-password",
        json={"new_password": "short"},
        headers=auth_headers(admin),
    )
    assert resp.status_code == 422


async def test_reset_password_requires_admin(client, db, auth_headers):
    non_admin = await make_user(db, "active@test.local", status=UserStatus.active)
    target = await make_user(db, "u@test.local", status=UserStatus.active)
    resp = await client.post(
        f"/api/v1/admin/users/{target.id}/reset-password",
        json={"new_password": "brandnew-pw-123"},
        headers=auth_headers(non_admin),
    )
    assert resp.status_code == 403


# --- JWT invalidation on reset/lock (picture-stage-7kr) ---


async def test_reset_password_revokes_existing_token(client, db, auth_headers, verify_db):
    """An access token minted before an admin password reset must stop working."""
    admin = await make_user(db, "admin@test.local", status=UserStatus.admin)
    target = await make_user(db, "u@test.local", status=UserStatus.active)

    # Mint the victim's token BEFORE the reset, then prove it works.
    old_headers = auth_headers(target)
    pre = await client.get("/api/v1/galleries", headers=old_headers)
    assert pre.status_code == 200

    reset = await client.post(
        f"/api/v1/admin/users/{target.id}/reset-password",
        json={"new_password": "brandnew-pw-123"},
        headers=auth_headers(admin),
    )
    assert reset.status_code == 204
    assert (await verify_db.get(User, target.id)).tokens_valid_after is not None

    # Same token, now rejected: iat predates the cut-off.
    after = await client.get("/api/v1/galleries", headers=old_headers)
    assert after.status_code == 401


async def test_disable_revokes_existing_token(client, db, auth_headers, verify_db):
    """Locking a user sets the cut-off so already-issued tokens are rejected."""
    admin = await make_user(db, "admin@test.local", status=UserStatus.admin)
    target = await make_user(db, "u@test.local", status=UserStatus.active)

    old_headers = auth_headers(target)
    assert (await client.get("/api/v1/galleries", headers=old_headers)).status_code == 200

    resp = await client.patch(
        f"/api/v1/admin/users/{target.id}/status",
        json={"status": "disabled"},
        headers=auth_headers(admin),
    )
    assert resp.status_code == 200
    assert (await verify_db.get(User, target.id)).tokens_valid_after is not None

    # Token check (401, revoked) runs before the status gate (403); either way: no access.
    assert (await client.get("/api/v1/galleries", headers=old_headers)).status_code == 401


async def test_token_issued_after_cutoff_is_accepted(client, db, auth_headers):
    """A token issued after the cut-off (the legitimate re-login) still works.

    The cut-off is placed firmly in the past so this is deterministic despite
    the second-level granularity of the JWT iat claim.
    """
    from datetime import UTC, datetime, timedelta

    target = await make_user(db, "u@test.local", status=UserStatus.active)
    target.tokens_valid_after = datetime.now(UTC) - timedelta(hours=1)
    await db.commit()

    resp = await client.get("/api/v1/galleries", headers=auth_headers(target))
    assert resp.status_code == 200


async def test_no_cutoff_keeps_token_valid(client, db, auth_headers):
    """A user that was never reset/locked (NULL cut-off) keeps full access."""
    target = await make_user(db, "u@test.local", status=UserStatus.active)
    assert target.tokens_valid_after is None

    resp = await client.get("/api/v1/galleries", headers=auth_headers(target))
    assert resp.status_code == 200
