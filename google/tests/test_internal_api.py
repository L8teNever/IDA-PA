import asyncio
import json
import unittest

from starlette.testclient import TestClient

from app.internal_api import build_internal_app, _plain


class FakeRunner:
    def calendar_status(self):
        return {"aktiv": True}

    def backup_status(self):
        return {"aktiv": False}

    async def run_calendar(self, dry_run=False):
        return {"ok": True, "dry_run": dry_run}

    async def run_backup(self):
        return {"ok": True}


class FakeGoogle:
    def is_connected(self):
        return True


class Settings:
    api_token = "geheim"


class Tool:
    def __init__(self, name):
        self.name, self.description, self.inputSchema = name, "Beschreibung", {"type": "object"}


class FakeMcp:
    async def list_tools(self):
        return [Tool("google_demo")]

    async def call_tool(self, name, args):
        if args.get("fehler"):
            raise ValueError("Pflichtparameter fehlt")
        return ([], {"result": [{"a": args.get("x", 1)}]})


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.c = TestClient(build_internal_app(Settings(), FakeMcp(), FakeGoogle(), FakeRunner()))
        self.h = {"Authorization": "Bearer geheim"}

    def test_token_pflicht(self):
        self.assertEqual(self.c.get("/api/v1/status").status_code, 401)
        self.assertEqual(self.c.get("/healthz").status_code, 200)

    def test_status_und_tools(self):
        self.assertEqual(self.c.get("/api/v1/status", headers=self.h).json()["verbunden"], True)
        self.assertEqual(self.c.get("/api/v1/tools", headers=self.h).json()[0]["name"], "google_demo")

    def test_funktion_ausfuehren(self):
        r = self.c.post("/api/v1/tools/google_demo", headers=self.h, json={"x": 5})
        self.assertEqual(r.json(), {"ergebnis": [{"a": 5}]})
        self.assertEqual(self.c.post("/api/v1/tools/gibt_es_nicht", headers=self.h, json={}).status_code, 404)
        self.assertEqual(self.c.post("/api/v1/tools/google_demo", headers=self.h, json={"fehler": 1}).status_code, 400)

    def test_abgleich_probelauf(self):
        self.assertEqual(self.c.post("/api/v1/sync/calendar?dry_run=1", headers=self.h).json(), {"ok": True, "dry_run": True})

    def test_plain_varianten(self):
        class T:
            text = json.dumps({"id": 1})
        self.assertEqual(_plain([T()]), {"id": 1})
        self.assertEqual(_plain({"result": 3}), 3)


class RealToolsTest(unittest.TestCase):
    def test_echte_funktionen_sind_da(self):
        from app.server import mcp
        names = {t.name for t in asyncio.run(mcp.list_tools())}
        self.assertTrue({"google_termine_liste", "google_drive_dateien_liste", "google_verbindung_status"} <= names)
        out = asyncio.run(mcp.call_tool("google_verbindung_status", {}))
        self.assertIn(_plain(out), ({"verbunden": False}, {"verbunden": True}))


if __name__ == "__main__":
    unittest.main()
