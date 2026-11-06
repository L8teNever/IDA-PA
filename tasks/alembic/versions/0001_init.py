"""Aufgaben

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op

from app.db import UTCDateTime

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(12), primary_key=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("day", sa.Date(), nullable=True),
        sa.Column("due", sa.Date(), nullable=True),
        sa.Column("done_at", UTCDateTime(), nullable=True),
        sa.Column("created_by", sa.String(40), nullable=False, server_default="user"),
        sa.Column("updated_by", sa.String(40), nullable=False, server_default="user"),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
    )
    op.create_index("ix_tasks_day", "tasks", ["day"])
    op.create_index("ix_tasks_done_at", "tasks", ["done_at"])


def downgrade() -> None:
    op.drop_table("tasks")
