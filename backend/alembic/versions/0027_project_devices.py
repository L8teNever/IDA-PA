"""code projects: devices from IDA Health (ida-connect) the threads may use

Revision ID: 0027
Revises: 0026
"""
from alembic import op
import sqlalchemy as sa

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("code_projects") as b:
        b.add_column(sa.Column("devices", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("code_projects") as b:
        b.drop_column("devices")
