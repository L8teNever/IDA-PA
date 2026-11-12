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
    scopes = None

    def is_connected(self):
        return True

    def get_access_token(self, scopes=None):
        FakeGoogle.scopes = scopes
        return "ya29.kurz"


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

    def test_gmail_token_nur_mit_gmail_scopes(self):
        self.assertEqual(self.c.post("/api/v1/token/gmail").status_code, 401)
        r = self.c.post("/api/v1/token/gmail", headers=self.h).json()
        self.assertEqual(r["access_token"], "ya29.kurz")
        self.assertTrue(FakeGoogle.scopes and all("gmail" in x for x in FakeGoogle.scopes))

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


class AuthFlowTest(unittest.TestCase):
    def setUp(self):
        from app.google_auth import GoogleAuthManager
        from types import SimpleNamespace
        import tempfile, os
        self.s = SimpleNamespace(api_token="", google_redirect_uri="https://ida.example/api/v1/google/callback", google_client_id="c", google_client_secret="s",
                                 token_file_path=os.path.join(tempfile.mkdtemp(), "t.json"))
        self.g = GoogleAuthManager(self.s, ["https://www.googleapis.com/auth/calendar", "https://www.googleapis.com/auth/drive.file"])
        self.c = TestClient(build_internal_app(self.s, FakeMcp(), self.g, FakeRunner(), {"calendar": ["https://www.googleapis.com/auth/calendar"]}))

    def test_start_gibt_google_adresse_mit_ida_ruecksprung(self):
        from urllib.parse import parse_qs, urlparse
        url = self.c.post("/api/v1/auth/start").json()["url"]
        q = parse_qs(urlparse(url).query)
        self.assertTrue(url.startswith("https://accounts.google.com/"))
        self.assertEqual(q["redirect_uri"], ["https://ida.example/api/v1/google/callback"])
        self.assertEqual(q["access_type"], ["offline"])
        self.assertIn("drive.file", q["scope"][0])

    def test_callback_prueft_state_und_tauscht_code(self):
        calls = []
        self.g.exchange_code = lambda code: calls.append(code)
        self.assertEqual(self.c.post("/api/v1/auth/callback", json={"code": "c1", "state": "falsch"}).status_code, 400)
        state = self.g.generate_state()
        self.assertEqual(self.c.post("/api/v1/auth/callback", json={"code": "c1", "state": state}).json(), {"ok": True})
        self.assertEqual(calls, ["c1"])
        # derselbe state ein zweites Mal geht nicht
        self.assertEqual(self.c.post("/api/v1/auth/callback", json={"code": "c2", "state": state}).status_code, 400)

    def test_google_fehler_wird_gemeldet(self):
        from app.google_auth import GoogleAuthError

        def boom(code):
            raise GoogleAuthError("Google hat keinen Refresh-Token geliefert")
        self.g.exchange_code = boom
        r = self.c.post("/api/v1/auth/callback", json={"code": "c", "state": self.g.generate_state()})
        self.assertEqual((r.status_code, "Refresh-Token" in r.json()["detail"]), (400, True))

    def test_status_ohne_verbindung(self):
        d = self.c.get("/api/v1/auth/status").json()
        self.assertEqual((d["verbunden"], d["vollstaendig"], d["dienste"]), (False, False, [{"name": "Kalender", "ok": False}]))
