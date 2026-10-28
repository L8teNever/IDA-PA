"""code area: projects (git repos in data/projects) and their threads (chats with kind=code)

Revision ID: 0013
Revises: 0012
"""
from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "code_projects",
        sa.Column("id", sa.String(12), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("repo_url", sa.String(300), nullable=True),
        sa.Column("default_branch", sa.String(100), nullable=False, server_default="main"),
        sa.Column("status", sa.String(10), nullable=False, server_default="ready"),
        sa.Column("status_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_code_projects"),
    )
    with op.batch_alter_table("chats") as b:
        b.add_column(sa.Column("kind", sa.String(8), nullable=False, server_default="chat"))
        b.add_column(sa.Column("project_id", sa.String(12), nullable=True))
        b.add_column(sa.Column("permission_mode", sa.String(8), nullable=False, server_default="ask"))
        b.create_foreign_key("fk_chats_project_id_code_projects", "code_projects", ["project_id"], ["id"], ondelete="CASCADE")
        b.create_index("ix_chats_kind", ["kind"])
        b.create_index("ix_chats_project_id", ["project_id"])


def downgrade() -> None:
    with op.batch_alter_table("chats") as b:
        b.drop_index("ix_chats_project_id")
        b.drop_index("ix_chats_kind")
        b.drop_constraint("fk_chats_project_id_code_projects", type_="foreignkey")
        b.drop_column("permission_mode")
        b.drop_column("project_id")
        b.drop_column("kind")
    op.drop_table("code_projects")
