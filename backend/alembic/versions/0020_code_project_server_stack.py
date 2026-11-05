"""code projects: Docker stack on the server (Dashboard) per project

Revision ID: 0020
Revises: 0019
"""
from alembic import op
import sqlalchemy as sa

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("code_projects") as b:
        b.add_column(sa.Column("server_stack", sa.String(120), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("code_projects") as b:
        b.drop_column("server_stack")
