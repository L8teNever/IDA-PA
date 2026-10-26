"""tracker links: secret URLs (e.g. on an NFC tag) that tick a tracker when opened

Revision ID: 0010
Revises: 0009
"""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tracker_links",
        sa.Column("id", sa.String(12), nullable=False),
        sa.Column("tracker_id", sa.String(12), nullable=False),
        sa.Column("name", sa.String(40), nullable=False, server_default="NFC-Tag"),
        sa.Column("token", sa.String(32), nullable=False),
        sa.Column("slot", sa.String(12), nullable=True),
        sa.Column("uses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tracker_id"], ["trackers.id"], name="fk_tracker_links_tracker_id_trackers", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_tracker_links"),
        sa.UniqueConstraint("token", name="uq_tracker_links_token"),
    )
    op.create_index("ix_tracker_links_tracker_id", "tracker_links", ["tracker_id"])


def downgrade() -> None:
    op.drop_table("tracker_links")
