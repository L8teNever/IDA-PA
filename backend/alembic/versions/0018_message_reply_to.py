"""a result knows the task it answers (the chat shows both in one card)

Revision ID: 0018
Revises: 0017
"""
from alembic import op
import sqlalchemy as sa

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("messages") as b:
        b.add_column(sa.Column("reply_to", sa.String(12), nullable=True))
        b.create_index("ix_messages_reply_to", ["reply_to"])


def downgrade() -> None:
    with op.batch_alter_table("messages") as b:
        b.drop_index("ix_messages_reply_to")
        b.drop_column("reply_to")
