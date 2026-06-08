"""Add users.tokens_valid_after for stateless JWT invalidation.

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-08

Adds a per-user cut-off timestamp. Access tokens issued (``iat``) before this
instant are rejected at request time (app/auth/dependencies.py). Set to now()
on admin-initiated password reset or account lock so already-issued JWTs are
invalidated immediately instead of lingering until expiry. Mirrors
``User.tokens_valid_after`` in app/db/models.py.
"""

import sqlalchemy as sa

from alembic import op

# Revision identifiers
revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the nullable cut-off column.

    NULL means "no invalidation point" so all existing tokens stay valid across
    the deploy — no surprise mass-logout.
    """
    op.add_column(
        "users",
        sa.Column("tokens_valid_after", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "tokens_valid_after")
