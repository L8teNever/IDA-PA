"""What each agent is doing right now (in memory) – drives the mascot's mood in the browser.

Every Claude run registers here (chats and background runs alike) and reports its current step:
thinking, writing text or using a tool. When a run ends, the agent's "done" counter moves on,
so the browser can react (happy / sad) even if it missed the run itself.
"""
import itertools
import time

_ids = itertools.count(1)
_runs: dict[int, dict] = {}  # run id -> {"agent", "kind", "state", "tool", "server", "since"}
_done: dict[str, dict] = {}  # agent id -> {"seq", "ok", "at"}
_seq = itertools.count(1)


def start(agent_id: str, kind: str) -> int:
    rid = next(_ids)
    _runs[rid] = {"agent": agent_id, "kind": kind, "state": "thinking", "tool": None, "server": None, "since": time.time()}
    return rid


def step(rid: int, state: str, tool: str | None = None, server: str | None = None) -> None:
    r = _runs.get(rid)
    if not r:
        return
    if state == "writing" and r["state"] == "tool":
        return  # keep showing the last tool while the answer is written – it is what the agent "did"
    r["state"] = state
    if tool is not None:
        r["tool"], r["server"] = tool, server


def end(rid: int, ok: bool) -> None:
    r = _runs.pop(rid, None)
    if r:
        _done[r["agent"]] = {"seq": next(_seq), "ok": ok, "at": time.time()}


def snapshot() -> dict:
    """Per agent: the newest running step (or none) and the last finished run."""
    now = time.time()
    agents: dict[str, dict] = {}
    for r in sorted(_runs.values(), key=lambda x: x["since"]):
        agents[r["agent"]] = {"kind": r["kind"], "state": r["state"], "tool": r["tool"], "server": r["server"],
                              "seconds": int(now - r["since"])}
    done = {a: {"seq": d["seq"], "ok": d["ok"], "ago": int(now - d["at"])} for a, d in _done.items()}
    return {"agents": agents, "done": done}
