"""connectors know what they are (context for every agent with access)

Revision ID: 0019
Revises: 0018
"""
from alembic import op
import sqlalchemy as sa

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("connectors") as b:
        b.add_column(sa.Column("context", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("connectors") as b:
        b.drop_column("context")
