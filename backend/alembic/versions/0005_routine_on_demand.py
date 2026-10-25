"""routines: on demand only (no schedule, started via MCP / "Jetzt ausführen")

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("routines") as b:
        b.add_column(sa.Column("on_demand", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("routines") as b:
        b.drop_column("on_demand")
