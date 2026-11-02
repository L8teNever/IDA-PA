"""ratings of the "Auto" model choice

Revision ID: 0015
Revises: 0014
"""
from alembic import op
import sqlalchemy as sa

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "route_feedback",
        sa.Column("id", sa.String(12), primary_key=True),
        sa.Column("message_id", sa.String(12), nullable=False, unique=True),
        sa.Column("kind", sa.String(16), nullable=False, index=True),
        sa.Column("difficulty", sa.Integer, nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("rating", sa.String(8), nullable=False),
        sa.Column("better_model", sa.String(64), nullable=True),
        sa.Column("snippet", sa.Text, nullable=False, server_default=""),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, index=True),
    )


def downgrade() -> None:
    op.drop_table("route_feedback")
