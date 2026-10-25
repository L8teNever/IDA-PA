"""accounts: provider (claude | antigravity)

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("accounts") as b:
        b.add_column(sa.Column("provider", sa.String(16), nullable=False, server_default="claude"))


def downgrade() -> None:
    with op.batch_alter_table("accounts") as b:
        b.drop_column("provider")
