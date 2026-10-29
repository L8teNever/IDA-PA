"""Chat -> wiki page (content only, no wiki needed)."""
from datetime import datetime, timezone
from types import SimpleNamespace

from app.services import wiki, wiki_setup


def test_chat_page_escapes_html_and_macros_and_lists_turns():
    at = datetime(2026, 10, 29, 8, 0, tzinfo=timezone.utc)
    chat = SimpleNamespace(id="abc", model="claude-sonnet-5-5", created_at=at, updated_at=at)
    msgs = [
        SimpleNamespace(role="user", text="Mach {{velocity}}$xwiki{{/velocity}}", parts=[], meta={}, at=at),
        SimpleNamespace(role="assistant", text="**Fertig.** <script>alert(1)</script>\n\n```\nif a < b: {{/html}}\n```",
                        parts=[{"t": "tool", "name": "Write"}], meta={}, at=at),
        SimpleNamespace(role="assistant", text="", parts=[], meta={"error": "Limit"}, at=at),
        SimpleNamespace(role="system", text="intern", parts=[], meta={}, at=at),
    ]
    page = wiki.chat_page(chat, SimpleNamespace(name="Ida"), None, msgs)
    assert page.startswith('{{html clean="true"}}\n') and page.endswith("\n{{/html}}\n")
    body = page.removeprefix('{{html clean="true"}}\n').removesuffix("{{/html}}\n")
    assert "{{" not in body and "<script>" not in body and "&lt;script&gt;" in body
    assert "<strong>Fertig.</strong>" in body and "a &lt; b" in body
    assert "<h3>Du · 29.10.2026, 09:00</h3>" in body and "<h3>Ida ·" in body
    assert "Werkzeuge: Write" in body and "Fehler: Limit" in body and "intern" not in body


def test_wiki_pages_cover_every_area_with_a_template():
    pages = {(".".join(sp), name) for sp, name, *_ in wiki_setup.pages()}
    for space, _title, _what, template, *_ in wiki_setup.AREAS:
        assert ("Leben." + space, "WebHome") in pages and ("Vorlagen", f"{template}Template") in pages
    assert ("Ida.Chats", "WebHome") in pages
    assert wiki_setup.LIFE == "Simons Leben" and not any("Mein Leben" in c for *_, c in wiki_setup.pages())
    assert wiki_setup.pages()[0][2] == wiki_setup.LIFE
