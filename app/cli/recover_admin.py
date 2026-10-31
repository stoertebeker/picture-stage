"""Recreate an administrator locally when the user table is empty.

Run inside the application container with an interactive terminal:
    python -m app.cli.recover_admin
The password is read using getpass and is never passed in command arguments.
"""

import asyncio
import getpass
import sys
from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.passwords import hash_password
from app.db.base import async_session
from app.db.models import SetupState, User, UserStatus


class RecoveryUnavailable(Exception):
    """Recovery is permitted only when no users exist and setup state is intact."""


async def recover_admin(db: AsyncSession, email: str, password: str) -> None:
    """Create one verified admin under the same singleton lock as setup."""
    email = email.strip()
    if not email or len(email) > 320 or len(password) < 8:
        raise ValueError("A valid email and a password of at least eight characters are required")
    state = await db.scalar(select(SetupState).where(SetupState.id == 1).with_for_update())
    if state is None or (await db.scalar(select(func.count()).select_from(User)) or 0) != 0:
        raise RecoveryUnavailable
    await db.execute(text("SELECT set_config('picture_stage.admin_insert', 'recovery', true)"))
    db.add(
        User(
            email=email,
            password_hash=hash_password(password),
            status=UserStatus.admin,
            email_verified_at=datetime.now(UTC),
        )
    )
    await db.flush()
    state.completed = True
    await db.commit()


async def _run(email: str, password: str) -> None:
    async with async_session() as db:
        await recover_admin(db, email, password)


def main() -> int:
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        print("Administrator recovery requires an interactive terminal.", file=sys.stderr)
        return 1
    try:
        email = input("Administrator email: ")
        password = getpass.getpass("New password: ")
        confirmation = getpass.getpass("Confirm password: ")
        if password != confirmation:
            print("Passwords do not match.", file=sys.stderr)
            return 1
        asyncio.run(_run(email, password))
    except (EOFError, KeyboardInterrupt):
        print("Recovery cancelled.", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except RecoveryUnavailable:
        print("Recovery is unavailable: users exist or setup state is missing.", file=sys.stderr)
        return 1
    print("Administrator created.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
