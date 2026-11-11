"""memory entries can be marked as voice (always in the context of a voice conversation)

Revision ID: 0022
Revises: 0021
"""
from alembic import op
import sqlalchemy as sa

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("memory_entries") as b:
        b.add_column(sa.Column("voice", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("memory_entries") as b:
        b.drop_column("voice")
