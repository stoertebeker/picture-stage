"""Classify legacy gallery credentials for bounded indexed lookup windows.

Revision ID: 0017
Revises: 0016
"""

import sqlalchemy as sa

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

CLASSIFY_FUNCTION = """
CREATE OR REPLACE FUNCTION classify_gallery_token_lookup() RETURNS trigger AS $$
BEGIN
    IF NEW.share_token IS NOT NULL AND NEW.share_token_salt IS NOT NULL
       AND sha256(convert_to(NEW.share_token, 'UTF8') || NEW.share_token_salt) = NEW.share_token_hash THEN
        NEW.share_token_lookup := sha256(convert_to(NEW.share_token, 'UTF8'));
    ELSE
        NEW.share_token_lookup := NULL;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

CLASSIFY_TRIGGER = """
CREATE TRIGGER galleries_classify_token_lookup
BEFORE INSERT OR UPDATE OF share_token, share_token_hash, share_token_salt, share_token_lookup ON galleries
FOR EACH ROW EXECUTE FUNCTION classify_gallery_token_lookup()
"""


def upgrade() -> None:
    op.execute(CLASSIFY_FUNCTION)
    op.execute(CLASSIFY_TRIGGER)
    # Revisit every row: 0009 trusted raw values, and old rolling writers may
    # have left non-NULL locators pointing to credentials that no longer match.
    op.execute("UPDATE galleries SET share_token_lookup = share_token_lookup")
    op.create_index("ix_galleries_token_candidates", "galleries", ["share_token_lookup", "id"])
    op.create_index(
        "ix_galleries_legacy_token_ids",
        "galleries",
        ["id"],
        postgresql_where=sa.text("share_token_lookup IS NULL AND share_token_hash IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_galleries_legacy_token_ids", table_name="galleries")
    op.drop_index("ix_galleries_token_candidates", table_name="galleries")
    op.execute("DROP TRIGGER galleries_classify_token_lookup ON galleries")
    op.execute("DROP FUNCTION classify_gallery_token_lookup()")
