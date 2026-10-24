"""routines: fixed run times (times + days)

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("routines") as b:
        b.add_column(sa.Column("times", sa.JSON(), nullable=True))
        b.add_column(sa.Column("days", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("routines") as b:
        b.drop_column("days")
        b.drop_column("times")
