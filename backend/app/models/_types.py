import uuid

from sqlalchemy import JSON


def new_id() -> str:
    return uuid.uuid4().hex[:12]


# JSON works on PostgreSQL (json) and SQLite (text) alike.
Json = JSON
