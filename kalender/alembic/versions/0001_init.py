"""Kalender: Kalender, Termine, Verlauf, Erinnerungs-Merker, Warteschlange, Volltextsuche

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

dt = sa.DateTime()


def upgrade() -> None:
    op.create_table(
        "calendars",
        sa.Column("id", sa.String(12), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("color", sa.String(16), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("visible", sa.Boolean, nullable=False),
        sa.Column("default_alarms", sa.JSON, nullable=False),
        sa.Column("agent_access", sa.String(8), nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("is_default", sa.Boolean, nullable=False),
        sa.Column("created_at", dt, nullable=False),
        sa.Column("updated_at", dt, nullable=False),
    )
    op.create_table(
        "events",
        sa.Column("id", sa.String(12), primary_key=True),
        sa.Column("calendar_id", sa.String(12), sa.ForeignKey("calendars.id", ondelete="CASCADE"), nullable=False),
        sa.Column("uid", sa.String(120), nullable=False, unique=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("location", sa.String(300), nullable=False),
        sa.Column("url", sa.String(500), nullable=False),
        sa.Column("start_at", dt, nullable=False),
        sa.Column("end_at", dt, nullable=False),
        sa.Column("all_day", sa.Boolean, nullable=False),
        sa.Column("tzid", sa.String(64), nullable=False),
        sa.Column("rrule", sa.String(500), nullable=False),
        sa.Column("exdates", sa.JSON, nullable=False),
        sa.Column("series_end", dt, nullable=True),
        sa.Column("recurrence_parent_id", sa.String(12), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=True),
        sa.Column("recurrence_original_start", sa.String(16), nullable=True),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("transparency", sa.String(8), nullable=False),
        sa.Column("color", sa.String(16), nullable=True),
        sa.Column("alarms", sa.JSON, nullable=False),
        sa.Column("alarm_action", sa.String(8), nullable=False),
        sa.Column("alarm_task", sa.String(500), nullable=False),
        sa.Column("travel_min", sa.Integer, nullable=False),
        sa.Column("birth_year", sa.Integer, nullable=True),
        sa.Column("attendees", sa.JSON, nullable=False),
        sa.Column("created_by", sa.String(40), nullable=False),
        sa.Column("updated_by", sa.String(40), nullable=False),
        sa.Column("sequence", sa.Integer, nullable=False),
        sa.Column("deleted_at", dt, nullable=True),
        sa.Column("created_at", dt, nullable=False),
        sa.Column("updated_at", dt, nullable=False),
    )
    op.create_index("ix_events_calendar_start", "events", ["calendar_id", "start_at"])
    op.create_index("ix_events_end_at", "events", ["end_at"])
    op.create_index("ix_events_series_end", "events", ["series_end"])
    op.create_index("ix_events_parent", "events", ["recurrence_parent_id"])
    op.create_index("ix_events_deleted_at", "events", ["deleted_at"])
    op.create_table(
        "alarms_sent",
        sa.Column("event_id", sa.String(12), primary_key=True),
        sa.Column("occurrence_start", sa.String(16), primary_key=True),
        sa.Column("minutes", sa.Integer, primary_key=True),
        sa.Column("sent_at", dt, nullable=False),
    )
    op.create_table(
        "changes",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("batch", sa.String(12), nullable=False),
        sa.Column("event_id", sa.String(12), nullable=False),
        sa.Column("calendar_id", sa.String(12), nullable=False),
        sa.Column("action", sa.String(10), nullable=False),
        sa.Column("by", sa.String(40), nullable=False),
        sa.Column("via", sa.String(10), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("before", sa.JSON, nullable=True),
        sa.Column("after", sa.JSON, nullable=True),
        sa.Column("undone_at", dt, nullable=True),
        sa.Column("at", dt, nullable=False),
    )
    op.create_index("ix_changes_at", "changes", ["at"])
    op.create_index("ix_changes_by", "changes", ["by"])
    op.create_index("ix_changes_batch", "changes", ["batch"])
    op.create_table("settings", sa.Column("key", sa.String(60), primary_key=True), sa.Column("value", sa.JSON, nullable=True))
    op.create_table(
        "outbox",
        sa.Column("id", sa.String(12), primary_key=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("next_try", dt, nullable=False),
        sa.Column("last_error", sa.String(300), nullable=False),
        sa.Column("created_at", dt, nullable=False),
    )
    op.create_index("ix_outbox_next_try", "outbox", ["next_try"])
    # Volltext: wird von services/search.py gepflegt (eigene Normalisierung ä→ae, ß→ss), nicht per Trigger
    op.execute("CREATE VIRTUAL TABLE events_fts USING fts5(event_id UNINDEXED, text, tokenize='unicode61 remove_diacritics 2')")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS events_fts")
    for t in ("outbox", "settings", "changes", "alarms_sent", "events", "calendars"):
        op.drop_table(t)
