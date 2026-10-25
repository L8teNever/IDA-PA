"""reminders: one-time things get their own table; routines only repeat (run_at moves over)

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reminders",
        sa.Column("id", sa.String(12), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False, server_default="remind"),
        sa.Column("agent_id", sa.String(32), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(8), nullable=False, server_default="pending"),
        sa.Column("result", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(32), nullable=False, server_default="user"),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name="fk_reminders_agent_id_agents", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_reminders"),
    )
    op.create_index("ix_reminders_agent_id", "reminders", ["agent_id"])
    op.create_index("ix_reminders_due_at", "reminders", ["due_at"])
    # one-time routines become tasks (kind "task" = the agent does it, like before)
    op.execute(
        "INSERT INTO reminders (id, text, kind, agent_id, due_at, status, result, created_by, created_at, updated_at) "
        "SELECT id, prompt, 'task', agent_id, run_at, CASE WHEN enabled THEN 'pending' ELSE 'failed' END, '', 'user', created_at, updated_at "
        "FROM routines WHERE run_at IS NOT NULL"
    )
    op.execute("DELETE FROM routines WHERE run_at IS NOT NULL")
    with op.batch_alter_table("routines") as b:
        b.drop_column("run_at")


def downgrade() -> None:
    with op.batch_alter_table("routines") as b:
        b.add_column(sa.Column("run_at", sa.DateTime(timezone=True), nullable=True))
    op.drop_table("reminders")
