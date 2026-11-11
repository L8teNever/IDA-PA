"""trackers can be chained: after a tick the next tracker follows on the same page

Revision ID: 0023
Revises: 0022
"""
from alembic import op
import sqlalchemy as sa

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("trackers") as b:
        b.add_column(sa.Column("next_id", sa.String(12), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("trackers") as b:
        b.drop_column("next_id")
