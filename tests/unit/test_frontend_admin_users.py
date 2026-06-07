"""Unit tests for the admin user-management UI (S3).

Render-level + mapping tests, no DB. The DB-backed business logic is covered by
tests/integration/test_admin_users.py (the frontend routes are thin adapters
onto the same app.admin.service functions).
"""

import datetime
import uuid
from dataclasses import dataclass

from app.frontend.admin import _STATUS_ACTIONS
from app.frontend.deps import templates
from app.i18n import t as translate


@dataclass
class _U:
    id: uuid.UUID
    email: str
    status: str
    locale: str = "de"
    created_at: datetime.datetime = datetime.datetime(2026, 6, 1, 12, 0)


def _t(key: str, **kw: object) -> str:
    return translate(key, "de", **kw)


def _render(name: str, **ctx: object) -> str:
    return templates.env.get_template(name).render(t=_t, **ctx)


def test_status_actions_map_is_unambiguous() -> None:
    from app.db.models import UserStatus

    assert _STATUS_ACTIONS["promote"][0] == UserStatus.admin
    assert _STATUS_ACTIONS["demote"][0] == UserStatus.active
    assert _STATUS_ACTIONS["disable"][0] == UserStatus.disabled
    assert _STATUS_ACTIONS["enable"][0] == UserStatus.active
    # enable and demote both -> active but carry distinct toast keys
    assert _STATUS_ACTIONS["enable"][1] != _STATUS_ACTIONS["demote"][1]


def test_user_row_shows_all_actions_for_other_user() -> None:
    admin = _U(uuid.uuid4(), "admin@test.local", "admin")
    other = _U(uuid.uuid4(), "bob@test.local", "active")
    html = _render("admin/_user_row.html", user=other, current_user=admin, galleries_count=2)

    assert f"/admin/users/{other.id}/status/promote" in html
    assert f"/admin/users/{other.id}/status/disable" in html
    assert f"/admin/users/{other.id}/delete" in html
    assert f"/admin/users/{other.id}/reset-password" in html
    assert "bob@test.local" in html


def test_user_row_hides_actions_for_self() -> None:
    """S1 enforced in the UI: the acting admin sees no action buttons on their own row."""
    admin = _U(uuid.uuid4(), "admin@test.local", "admin")
    html = _render("admin/_user_row.html", user=admin, current_user=admin, galleries_count=0)

    assert "/status/promote" not in html
    assert "/delete" not in html
    assert "/reset-password" not in html


def test_user_row_shows_enable_for_disabled_user() -> None:
    admin = _U(uuid.uuid4(), "admin@test.local", "admin")
    locked = _U(uuid.uuid4(), "locked@test.local", "disabled")
    html = _render("admin/_user_row.html", user=locked, current_user=admin, galleries_count=0)

    assert f"/admin/users/{locked.id}/status/enable" in html
    assert "/status/disable" not in html


def test_user_row_shows_demote_for_admin_user() -> None:
    admin = _U(uuid.uuid4(), "admin@test.local", "admin")
    other_admin = _U(uuid.uuid4(), "admin2@test.local", "admin")
    html = _render("admin/_user_row.html", user=other_admin, current_user=admin, galleries_count=0)

    assert f"/admin/users/{other_admin.id}/status/demote" in html
    assert "/status/promote" not in html


def test_delete_modal_requires_email_confirmation() -> None:
    admin = _U(uuid.uuid4(), "admin@test.local", "admin")
    other = _U(uuid.uuid4(), "bob@test.local", "active")
    html = _render("admin/_user_row.html", user=other, current_user=admin, galleries_count=0)

    # Submit stays disabled until typed email matches the target email.
    assert "deleteConfirm !== targetEmail" in html
    assert "bob@test.local" in html


def test_users_page_renders_table_and_empty_state() -> None:
    admin = _U(uuid.uuid4(), "admin@test.local", "admin")
    other = _U(uuid.uuid4(), "bob@test.local", "active")

    filled = _render("admin/users.html", rows=[(other, 2)], current_user=admin, csrf_token="x", request=None)
    assert "bob@test.local" in filled
    assert _t("admin.users_heading") in filled

    empty = _render("admin/users.html", rows=[], current_user=admin, csrf_token="x", request=None)
    assert _t("admin.no_users") in empty
