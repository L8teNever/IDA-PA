"""How the voice container talks to IDA (internal, header x-voice-secret). If IDA is down, the call still works – just
without context and tools – and the result is retried a few times."""
import asyncio
import logging

import httpx

log = logging.getLogger("ida.voice.ida")
FALLBACK_CONTEXT = ('Du bist "Stella", die Telefon- und Sprachassistentin des Nutzers. Du hast gerade keinen Zugriff auf seine Daten '
                    "(IDA ist nicht erreichbar) – sag das ehrlich, wenn er etwas nachschlagen will.\n")


class Ida:
    def __init__(self, settings, transport: httpx.AsyncBaseTransport | None = None):
        self.s = settings
        self._transport = transport

    async def _post(self, path: str, body: dict, timeout: float = 15) -> dict:
        async with httpx.AsyncClient(timeout=timeout, transport=self._transport) as c:
            r = await c.post(self.s.voice_ida_url.rstrip("/") + "/api/v1/voice/internal/" + path, json=body,
                             headers={"x-voice-secret": self.s.voice_secret})
        r.raise_for_status()
        return r.json()

    async def start(self, call_id: str, direction: str, number: str, owner: bool, purpose: str) -> dict:
        try:
            return await self._post("start", {"call_id": call_id, "direction": direction, "number": number, "owner": owner, "purpose": purpose}, 20)
        except Exception as e:  # noqa: BLE001
            log.warning("IDA start failed: %s", e)
            return {"context": FALLBACK_CONTEXT, "tools": [], "owner": owner, "offline": True}

    async def tool(self, call_id: str, name: str, args: dict) -> dict:
        try:
            return await self._post("tool", {"call_id": call_id, "name": name, "args": args}, 40)
        except httpx.TimeoutException:
            return {"error": "Das hat zu lange gedauert."}
        except Exception as e:  # noqa: BLE001
            log.warning("IDA tool %s failed: %s", name, e)
            return {"error": "Das Tool ist gerade nicht erreichbar."}

    async def event(self, call_id: str, direction: str, number: str, status: str, purpose: str = "", error: str = "", owner: bool = False) -> None:
        try:
            await self._post("event", {"call_id": call_id, "direction": direction, "number": number, "status": status,
                                       "purpose": purpose, "error": error, "owner": owner})
        except Exception as e:  # noqa: BLE001
            log.warning("IDA event %s failed: %s", status, e)

    async def finish(self, call_id: str, direction: str, number: str, transcript: list[dict], outcome: str, status: str, error: str = "") -> None:
        body = {"call_id": call_id, "direction": direction, "number": number, "transcript": transcript, "outcome": outcome,
                "status": status, "error": error}
        for attempt in range(5):  # IDA may be restarting right now (update) – the transcript should not get lost
            try:
                await self._post("finish", body, 30)
                return
            except Exception as e:  # noqa: BLE001
                log.warning("IDA finish failed (%d): %s", attempt + 1, e)
                await asyncio.sleep(5 * (attempt + 1))
