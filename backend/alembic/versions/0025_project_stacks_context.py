"""code projects: several server stacks and a context text for every thread

Revision ID: 0025
Revises: 0024
"""
from alembic import op
import sqlalchemy as sa

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("code_projects") as b:
        b.add_column(sa.Column("server_stacks", sa.JSON(), nullable=True))
        b.add_column(sa.Column("context", sa.Text(), nullable=False, server_default=""))
    rows = op.get_bind().execute(sa.text("SELECT id, server_stack FROM code_projects WHERE server_stack IS NOT NULL")).fetchall()
    t = sa.table("code_projects", sa.column("id", sa.String), sa.column("server_stacks", sa.JSON))
    for pid, stack in rows:
        op.execute(t.update().where(t.c.id == pid).values(server_stacks=[stack]))


def downgrade() -> None:
    with op.batch_alter_table("code_projects") as b:
        b.drop_column("context")
        b.drop_column("server_stacks")
