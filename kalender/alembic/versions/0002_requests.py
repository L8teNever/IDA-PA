"""Idempotenz: bereits ausgeführte Schreibaufrufe merken (Offline-Abgleich darf wiederholen, ohne Doppeltes anzulegen)

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
        "requests",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("method", sa.String(8), nullable=False),
        sa.Column("path", sa.String(200), nullable=False),
        sa.Column("status", sa.Integer, nullable=False),
        sa.Column("response", sa.JSON, nullable=False),
        sa.Column("at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_requests_at", "requests", ["at"])


def downgrade() -> None:
    op.drop_table("requests")
