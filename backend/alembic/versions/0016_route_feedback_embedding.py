"""embeddings of the rated questions (Auto finds similar earlier questions)

Revision ID: 0016
Revises: 0015
"""
from alembic import op
import sqlalchemy as sa

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("route_feedback") as b:
        b.add_column(sa.Column("embedding", sa.LargeBinary, nullable=True))
        b.add_column(sa.Column("embed_model", sa.String(80), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("route_feedback") as b:
        b.drop_column("embed_model")
        b.drop_column("embedding")
