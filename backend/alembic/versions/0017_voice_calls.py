"""voice calls (Stella: phone calls and live conversations in the app)

Revision ID: 0017
Revises: 0016
"""
from alembic import op
import sqlalchemy as sa

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "voice_calls",
        sa.Column("id", sa.String(24), primary_key=True),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("number", sa.String(32), nullable=False, server_default=""),
        sa.Column("owner", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(16), nullable=False, server_default="new"),
        sa.Column("purpose", sa.Text, nullable=False, server_default=""),
        sa.Column("outcome", sa.Text, nullable=False, server_default=""),
        sa.Column("error", sa.Text, nullable=False, server_default=""),
        sa.Column("chat_id", sa.String(12), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_voice_calls_started_at", "voice_calls", ["started_at"])


def downgrade() -> None:
    op.drop_index("ix_voice_calls_started_at", "voice_calls")
    op.drop_table("voice_calls")
