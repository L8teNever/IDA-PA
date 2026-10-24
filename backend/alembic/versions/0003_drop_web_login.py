"""web UI login removed: drop the stored password hash and session key

Revision ID: 0003
Revises: 0002
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM settings WHERE key IN ('password_hash', 'secret_key')")


def downgrade() -> None:
    pass
