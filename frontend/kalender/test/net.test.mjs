import assert from "node:assert/strict";
import { describe, test } from "node:test";
import { ApiError, classifyFailure, classifyResponse } from "../js/net.js";

const json = {};
describe("Antworten", () => {
  test("Erfolg mit JSON", () => assert.equal(classifyResponse({ status: 200, ok: true, type: "basic", json }), null));
  test("Weiterleitung zur Anmeldung (redirect: manual) ist auth, nicht offline", () => assert.equal(classifyResponse({ status: 0, ok: false, type: "opaqueredirect", json: null }), "auth"));
  test("200 mit HTML statt JSON (Access-Anmeldeseite) ist auth", () => assert.equal(classifyResponse({ status: 200, ok: true, type: "basic", json: null }), "auth"));
  test("401 und 403 ohne JSON sind auth; 403 mit Meldung des Kalenders ist invalid", () => {
    assert.equal(classifyResponse({ status: 401, ok: false, type: "basic", json: null }), "auth");
    assert.equal(classifyResponse({ status: 403, ok: false, type: "basic", json: null }), "auth");
    assert.equal(classifyResponse({ status: 403, ok: false, type: "basic", json: { detail: "nur lesbar" } }), "invalid");
  });
  test("409 conflict, 404 mit Meldung gone, 404 ohne JSON server", () => {
    assert.equal(classifyResponse({ status: 409, ok: false, type: "basic", json }), "conflict");
    assert.equal(classifyResponse({ status: 404, ok: false, type: "basic", json: { detail: "Termin nicht gefunden" } }), "gone");
    assert.equal(classifyResponse({ status: 404, ok: false, type: "basic", json: null }), "server");
  });
  test("5xx, 429 und unlesbare Fehler sind server; 400/422 invalid", () => {
    for (const s of [500, 502, 503, 429]) assert.equal(classifyResponse({ status: s, ok: false, type: "basic", json }), "server");
    assert.equal(classifyResponse({ status: 502, ok: false, type: "basic", json: null }), "server");
    for (const s of [400, 422]) assert.equal(classifyResponse({ status: s, ok: false, type: "basic", json: { detail: "x" } }), "invalid");
  });
});

describe("Verbindungsfehler", () => {
  test("Zeitüberschreitung und kein Netz sind offline, ohne zu prüfen", async () => {
    let probed = 0;
    const probe = async () => { probed++; return "auth"; };
    assert.equal(await classifyFailure({ aborted: true, online: true, probe }), "offline");
    assert.equal(await classifyFailure({ online: false, probe }), "offline");
    assert.equal(probed, 0);
  });
  test("scheinbar online: die Prüfung entscheidet zwischen auth und offline", async () => {
    assert.equal(await classifyFailure({ online: true, probe: async () => "auth" }), "auth");
    assert.equal(await classifyFailure({ online: true, probe: async () => "ok" }), "offline");
    assert.equal(await classifyFailure({ online: true, probe: async () => { throw new TypeError("Failed to fetch"); } }), "offline");
    assert.equal(await classifyFailure({ online: true }), "offline");
  });
});

test("ApiError trägt Art und Status", () => {
  const e = new ApiError("x", 409, "conflict", { server: { sequence: 3 } });
  assert.equal(e.kind, "conflict"); assert.equal(e.status, 409); assert.equal(e.server.sequence, 3); assert.equal(e.offline, false);
  assert.equal(new ApiError("offline", 0, "offline").offline, true);
});
