"""Persist and serialize the first administrator bootstrap claim.

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "setup_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("completed", sa.Boolean(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_setup_state_singleton"),
    )
    op.execute("INSERT INTO setup_state (id, completed) SELECT 1, EXISTS (SELECT 1 FROM users)")
    # The old HTTP setup handler does not acquire the singleton lock. Guard only
    # direct administrator INSERTs; normal approval INSERTs active users, and
    # promotion changes an existing row via UPDATE.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION picture_stage_guard_admin_insert()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            claim_complete boolean;
            admin_permit text;
        BEGIN
            admin_permit := current_setting('picture_stage.admin_insert', true);
            IF admin_permit IS NULL OR admin_permit NOT IN ('bootstrap', 'recovery') THEN
                RAISE EXCEPTION 'administrator insertion requires operator authorization'
                    USING ERRCODE = '42501';
            END IF;
            SELECT completed INTO claim_complete FROM setup_state WHERE id = 1 FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'bootstrap claim missing' USING ERRCODE = '23514';
            END IF;
            IF admin_permit = 'bootstrap' AND claim_complete THEN
                RAISE EXCEPTION 'bootstrap claim already consumed' USING ERRCODE = '23514';
            END IF;
            IF EXISTS (SELECT 1 FROM users) THEN
                RAISE EXCEPTION 'administrator bootstrap requires an empty user table' USING ERRCODE = '23514';
            END IF;
            UPDATE setup_state SET completed = true WHERE id = 1;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER guard_admin_insert BEFORE INSERT ON users "
        "FOR EACH ROW WHEN (NEW.status = 'admin') EXECUTE FUNCTION picture_stage_guard_admin_insert()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS guard_admin_insert ON users")
    op.execute("DROP FUNCTION IF EXISTS picture_stage_guard_admin_insert()")
    op.drop_table("setup_state")
