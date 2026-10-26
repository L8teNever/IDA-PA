"""trackers: duration_s – a timer runs before the tick (e.g. 3 minutes brushing teeth)

Revision ID: 0012
Revises: 0011
"""
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("trackers") as b:
        b.add_column(sa.Column("duration_s", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("trackers") as b:
        b.drop_column("duration_s")
