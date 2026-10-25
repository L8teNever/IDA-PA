"""routines/reminders: show_on_home – only marked ones appear in the start page's day plan

Revision ID: 0008
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # routines run in the background and stay out of the day plan unless marked; reminders show by default
    with op.batch_alter_table("routines") as b:
        b.add_column(sa.Column("show_on_home", sa.Boolean(), nullable=False, server_default=sa.false()))
    with op.batch_alter_table("reminders") as b:
        b.add_column(sa.Column("show_on_home", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    with op.batch_alter_table("reminders") as b:
        b.drop_column("show_on_home")
    with op.batch_alter_table("routines") as b:
        b.drop_column("show_on_home")
