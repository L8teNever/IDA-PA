"""Smart Boards: Thema/Prompt, Modell und das von Ida erstellte Ergebnis.

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "boards",
        sa.Column("id", sa.String(12), primary_key=True),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("prompt", sa.Text, nullable=False),
        sa.Column("model", sa.String(80), nullable=True),
        sa.Column("content", sa.Text, nullable=False, server_default=""),
        sa.Column("status", sa.String(12), nullable=False, server_default="idle"),
        sa.Column("error", sa.String(400), nullable=False, server_default=""),
        sa.Column("last_run_at", sa.DateTime, nullable=True),
        sa.Column("run_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("boards")
