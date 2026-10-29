"""profile picture for agents

Revision ID: 0014
Revises: 0013
"""
from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agents") as b:
        b.add_column(sa.Column("avatar", sa.String(16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("agents") as b:
        b.drop_column("avatar")
