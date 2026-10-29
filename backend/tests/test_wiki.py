"""Chat -> wiki page (content only, no wiki needed)."""
from datetime import datetime, timezone
from types import SimpleNamespace

from app.services import wiki, wiki_setup


def test_chat_markdown_escapes_macros_and_lists_turns():
    at = datetime(2026, 10, 29, 8, 0, tzinfo=timezone.utc)
    chat = SimpleNamespace(id="abc", model="claude-sonnet-5-5", created_at=at, updated_at=at)
    msgs = [
        SimpleNamespace(role="user", text="Mach {{velocity}}$xwiki{{/velocity}}", parts=[], meta={}, at=at),
        SimpleNamespace(role="assistant", text="Fertig.", parts=[{"t": "tool", "name": "Write"}], meta={}, at=at),
        SimpleNamespace(role="assistant", text="", parts=[], meta={"error": "Limit"}, at=at),
        SimpleNamespace(role="system", text="intern", parts=[], meta={}, at=at),
    ]
    md = wiki.chat_markdown(chat, SimpleNamespace(name="Ida"), None, msgs)
    assert "{{velocity" not in md and "{​{velocity" in md
    assert "### Du · 29.10.2026, 09:00" in md and "### Ida ·" in md
    assert "_Werkzeuge: Write_" in md and "> Fehler: Limit" in md and "intern" not in md


def test_wiki_pages_cover_every_area_with_a_template():
    pages = {(".".join(sp), name) for sp, name, *_ in wiki_setup.pages()}
    for space, _title, _what, template, *_ in wiki_setup.AREAS:
        assert ("Leben." + space, "WebHome") in pages and ("Vorlagen", f"{template}Template") in pages
    assert ("Ida.Chats", "WebHome") in pages
