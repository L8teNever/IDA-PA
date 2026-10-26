"""trackers: daily things to tick off (user or agents), follow-up (notification/routine) when a slot stays open

Revision ID: 0009
Revises: 0008
"""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trackers",
        sa.Column("id", sa.String(12), nullable=False),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("icon", sa.String(40), nullable=False, server_default="task_alt"),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("slots", sa.JSON(), nullable=False),
        sa.Column("days", sa.JSON(), nullable=True),
        sa.Column("on_missed", sa.String(8), nullable=False, server_default="none"),
        sa.Column("routine_id", sa.String(12), nullable=True),
        sa.Column("fired", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("show_on_home", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(32), nullable=False, server_default="user"),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["routine_id"], ["routines.id"], name="fk_trackers_routine_id_routines", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_trackers"),
    )
    op.create_index("ix_trackers_routine_id", "trackers", ["routine_id"])
    op.create_table(
        "tracker_checks",
        sa.Column("id", sa.String(12), nullable=False),
        sa.Column("tracker_id", sa.String(12), nullable=False),
        sa.Column("day", sa.String(10), nullable=False),
        sa.Column("slot", sa.String(12), nullable=False),
        sa.Column("by", sa.String(32), nullable=False, server_default="user"),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tracker_id"], ["trackers.id"], name="fk_tracker_checks_tracker_id_trackers", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_tracker_checks"),
        sa.UniqueConstraint("tracker_id", "day", "slot", name="uq_tracker_checks_tracker_id"),
    )
    op.create_index("ix_tracker_checks_tracker_id", "tracker_checks", ["tracker_id"])
    op.create_index("ix_tracker_checks_day", "tracker_checks", ["day"])


def downgrade() -> None:
    op.drop_table("tracker_checks")
    op.drop_table("trackers")
