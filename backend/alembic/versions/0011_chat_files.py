"""chat files: pictures, PDFs and text files attached to chat messages

Revision ID: 0011
Revises: 0010
"""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_files",
        sa.Column("id", sa.String(12), nullable=False),
        sa.Column("chat_id", sa.String(12), nullable=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("mime", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], name="fk_chat_files_chat_id_chats", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_chat_files"),
    )
    op.create_index("ix_chat_files_chat_id", "chat_files", ["chat_id"])
    op.create_index("ix_chat_files_created_at", "chat_files", ["created_at"])


def downgrade() -> None:
    op.drop_table("chat_files")
