"""connectors can log in with OAuth (client id/secret, browser login, token refresh)

Revision ID: 0021
Revises: 0020
"""
from alembic import op
import sqlalchemy as sa

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("connectors") as b:
        b.add_column(sa.Column("oauth", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("connectors") as b:
        b.drop_column("oauth")
