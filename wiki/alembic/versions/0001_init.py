"""Grundschema IDA Wiki: Seiten, Link-Graph, Tags, Anhänge, Revisionen, Freigabe-Proposals, MCP-Tokens, Idempotenz & FTS5.

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pages",
        sa.Column("id", sa.String(12), primary_key=True),
        sa.Column("space", sa.String(16), nullable=False),
        sa.Column("path", sa.String(320), nullable=False),
        sa.Column("folder", sa.String(280), nullable=False, server_default=""),
        sa.Column("slug", sa.String(140), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("summary", sa.String(300), nullable=False, server_default=""),
        sa.Column("frontmatter", sa.JSON, nullable=False),
        sa.Column("body_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("word_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("pinned", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("importance", sa.Integer, nullable=False, server_default="3"),
        sa.Column("expires_at", sa.DateTime, nullable=True),
        sa.Column("managed_by", sa.String(24), nullable=True),
        sa.Column("current_revision_id", sa.Integer, nullable=True),
        sa.Column("created_by", sa.String(64), nullable=False, server_default="user"),
        sa.Column("updated_by", sa.String(64), nullable=False, server_default="user"),
        sa.Column("deleted_at", sa.DateTime, nullable=True),
        sa.Column("embedding", sa.LargeBinary, nullable=True),
        sa.Column("embed_model", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
        sa.UniqueConstraint("path", name="uq_pages_path"),
    )
    op.create_index("ix_pages_space", "pages", ["space"])
    op.create_index("ix_pages_folder", "pages", ["folder"])
    op.create_index("ix_pages_slug", "pages", ["slug"])
    op.create_index("ix_pages_deleted_at", "pages", ["deleted_at"])
    op.create_index("ix_pages_updated_at", "pages", ["updated_at"])

    op.create_table(
        "page_links",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("source_page_id", sa.String(12), sa.ForeignKey("pages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_page_id", sa.String(12), sa.ForeignKey("pages.id", ondelete="SET NULL"), nullable=True),
        sa.Column("target_raw", sa.String(320), nullable=False),
        sa.Column("heading", sa.String(160), nullable=True),
        sa.Column("alias", sa.String(160), nullable=True),
        sa.Column("is_embed", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("context_snippet", sa.String(280), nullable=False, server_default=""),
    )
    op.create_index("ix_page_links_source_page_id", "page_links", ["source_page_id"])
    op.create_index("ix_page_links_target_page_id", "page_links", ["target_page_id"])

    op.create_table(
        "page_tags",
        sa.Column("page_id", sa.String(12), sa.ForeignKey("pages.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("tag", sa.String(80), primary_key=True),
    )
    op.create_index("ix_page_tags_tag", "page_tags", ["tag"])

    op.create_table(
        "attachments",
        sa.Column("id", sa.String(12), primary_key=True),
        sa.Column("page_id", sa.String(12), sa.ForeignKey("pages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("mime", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("size", sa.Integer, nullable=False, server_default="0"),
        sa.Column("sha256", sa.String(64), nullable=False, server_default=""),
        sa.Column("source_chat_file_id", sa.String(12), nullable=True),
        sa.Column("uploaded_by", sa.String(64), nullable=False, server_default="user"),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_attachments_page_id", "attachments", ["page_id"])
    op.create_index("ix_attachments_source_chat_file_id", "attachments", ["source_chat_file_id"])

    op.create_table(
        "revisions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("page_id", sa.String(12), sa.ForeignKey("pages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rev_number", sa.Integer, nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("space", sa.String(16), nullable=False),
        sa.Column("path_snapshot", sa.String(320), nullable=False),
        sa.Column("title_snapshot", sa.String(200), nullable=False),
        sa.Column("content_full", sa.Text, nullable=False),
        sa.Column("added_lines", sa.Integer, nullable=False, server_default="0"),
        sa.Column("deleted_lines", sa.Integer, nullable=False, server_default="0"),
        sa.Column("note", sa.String(240), nullable=False, server_default=""),
        sa.Column("by", sa.String(64), nullable=False),
        sa.Column("approved_by", sa.String(64), nullable=True),
        sa.Column("via", sa.String(16), nullable=False),
        sa.Column("at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_revisions_page_id", "revisions", ["page_id"])
    op.create_index("ix_revisions_at", "revisions", ["at"])
    op.create_index("ix_revisions_space", "revisions", ["space"])

    op.create_table(
        "proposals",
        sa.Column("id", sa.String(12), primary_key=True),
        sa.Column("agent_id", sa.String(64), nullable=False),
        sa.Column("action", sa.String(24), nullable=False),
        sa.Column("target_path", sa.String(320), nullable=False),
        sa.Column("summary", sa.String(240), nullable=False, server_default=""),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("diff_preview", sa.Text, nullable=False, server_default=""),
        sa.Column("commit_secret_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("expires_at", sa.DateTime, nullable=False),
        sa.Column("decided_at", sa.DateTime, nullable=True),
    )
    op.create_index("ix_proposals_status", "proposals", ["status"])

    op.create_table(
        "mcp_tokens",
        sa.Column("id", sa.String(12), primary_key=True),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("prefix", sa.String(12), nullable=False),
        sa.Column("allow_read_private", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("allow_read_chats", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("last_used_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.UniqueConstraint("token_hash", name="uq_mcp_tokens_token_hash"),
    )

    op.create_table(
        "requests",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("method", sa.String(8), nullable=False),
        sa.Column("path", sa.String(200), nullable=False),
        sa.Column("status", sa.Integer, nullable=False),
        sa.Column("response", sa.JSON, nullable=False),
        sa.Column("at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_requests_at", "requests", ["at"])

    op.create_table(
        "settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.JSON, nullable=False),
    )

    op.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5("
        "page_id UNINDEXED, title, summary, tags_text, headings_text, body, "
        "tokenize='unicode61 remove_diacritics 2')"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS pages_fts")
    for t in ("settings", "requests", "mcp_tokens", "proposals", "revisions", "attachments", "page_tags", "page_links", "pages"):
        op.drop_table(t)
