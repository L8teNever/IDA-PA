"""Model "auto": picks provider, model and strength for each message by itself.

Step 1 (rules + profiles): the message is sorted into a kind (code, writing, research, chat, general) and a
difficulty (1 light … 3 hard). Each usable model gets a profile from its name (provider, tier 1–3, thinking
levels). Auto takes the lightest model that fits the difficulty, prefers a provider per kind and skips
accounts at their limit. Which models it may use and whether it may change provider are set under
Einstellungen → Auto (settings key "autoroute").

Step 2 (learning): after an answer the user can rate the choice (passte / zu schwach / zu stark). Per kind the
ratings shift the difficulty one step up or down once enough of them point the same way (table route_feedback).
"""
import re
from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models import ChatMessage, RouteFeedback
from . import antigravity, providers, settings_store
from .errors import Invalid

AUTO = "auto"
KEY = "autoroute"
PROVIDER_MODES = ("per_chat", "free", "claude", "antigravity")
DEFAULTS = {"provider_mode": "per_chat", "models": None}  # models None = the configured Claude models + all Antigravity models
EFFORT_OF = {1: "low", 2: "medium", 3: "high"}
KIND_NAMES = {"code": "Code", "writing": "Schreiben", "research": "Recherche", "chat": "Smalltalk", "general": "Allgemein"}
LEVEL_NAMES = {1: "leicht", 2: "mittel", 3: "schwer"}
# which provider fits a kind better when Auto may choose; None = no preference (stay with the chat's / default provider)
PREFER = {"code": "claude", "writing": "claude", "research": "antigravity", "chat": None, "general": None}


def is_auto(model: str | None) -> bool:
    return model == AUTO


@dataclass
class Profile:
    id: str
    name: str
    provider: str
    tier: int | None  # 1 light, 2 middle, 3 strong; None = unknown (only used when nothing else fits)
    level: int | None = None  # Antigravity variants "(Low|Medium|High)": thinking level of this variant


def _tier(text: str) -> int | None:
    t = text.lower()
    if re.search(r"haiku|flash|lite|mini|nano", t):
        return 1
    if re.search(r"opus|\bpro\b|ultra", t):
        return 3
    if re.search(r"sonnet|gpt|claude", t):
        return 2
    return None


def profile(model: str, name: str | None = None) -> Profile:
    name = name or model
    if antigravity.is_model(model):
        m = re.search(r"\((low|medium|high)\)\s*$", name, re.I)
        level = {"low": 1, "medium": 2, "high": 3}[m.group(1).lower()] if m else None
        return Profile(model, name, "antigravity", _tier(name + " " + model), level)
    return Profile(model, name, "claude", _tier(model))


def supports_effort(model: str) -> bool:
    """Same rule as the model menu (frontend effortsOf): older Claude models know no --effort."""
    return not antigravity.is_model(model) and not re.search(r"haiku-4-5|sonnet-4-5|-3-|-4-1|-4-0|-4$", model)


# ---- settings ----

async def config(db: AsyncSession) -> dict:
    saved = await settings_store.get(db, KEY) or {}
    out = {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}
    if out["provider_mode"] not in PROVIDER_MODES:
        out["provider_mode"] = DEFAULTS["provider_mode"]
    return out


async def available(db: AsyncSession) -> list[Profile]:
    names = await providers.model_names(db)
    return [profile(m, names.get(m)) for m in await providers.model_list(db)]


def default_allowed(profiles: list[Profile]) -> list[str]:
    configured = set(get_settings().model_list)
    return [p.id for p in profiles if p.provider == "antigravity" or p.id in configured]


async def save(db: AsyncSession, provider_mode: str, models: list[str] | None) -> dict:
    if provider_mode not in PROVIDER_MODES:
        raise Invalid("Unbekannte Anbieter-Einstellung")
    await settings_store.put(db, KEY, {"provider_mode": provider_mode, "models": models})
    return await config(db)


# ---- 1. what kind of message, how hard ----

_CODE = re.compile(r"```|traceback|exception|stack ?trace|\bdef |function\s*\(|=>|\bclass \w+|\bimport \w+|"
                   r"\b(code|bug|debug|refactor|fehler ?meldung|kompilier|commit|deploy|docker|regex|sql|api|endpoint|funktion|skript|script)\b", re.I)
_WRITE = re.compile(r"\b(schreib|formulier|mail|e-mail|brief|bewerbung|text|aufsatz|zusammenfass|übersetz|korrigier|umschreib)", re.I)
_RESEARCH = re.compile(r"\b(recherch|such\w* (im|nach)|internet|google|vergleich|welche[sr]? (ist|sind) (die |das |der )?best|aktuell|preis|news|nachrichten)", re.I)
_HARD = re.compile(r"\b(architektur|konzept|strategie|beweis|herleit|analysier|analyse|optimier|plan(e|ung)?\b|entwirf|design|"
                   r"schritt für schritt|ausführlich|gründlich|komplex|warum genau|vergleiche .* mit)", re.I)
_SMALL = re.compile(r"^(hi|hey|hallo|moin|servus|danke|ok(ay)?|super|cool|gut|ja|nein|alles klar|passt|top|nice)\b", re.I)


def classify(text: str, *, code_thread: bool = False, files: int = 0) -> tuple[str, int, str]:
    """(kind, difficulty 1–3, reason in German) – cheap rules, no AI."""
    t = (text or "").strip()
    n = len(t)
    hard = bool(_HARD.search(t))
    if code_thread or _CODE.search(t):
        d = 3 if (hard or n > 1500) else 2
        return "code", d, "Code-Thread" if code_thread else "Code erkannt"
    if n < 80 and not files and (_SMALL.search(t) or n < 25):
        return "chat", 1, "kurze Nachricht"
    if _RESEARCH.search(t):
        return "research", 3 if hard else 2, "Recherche"
    if _WRITE.search(t):
        return "writing", 3 if (hard or n > 2000) else 2 if n > 150 else 1, "Text schreiben"
    if hard or n > 1500:
        return "general", 3, "schwierig/umfangreich"
    return "general", 2 if (n > 200 or files) else 1, "Allgemeine Frage"


# ---- learning from the ratings ----

RATINGS = ("ok", "weak", "strong")
LEARN_WINDOW = 40  # newest ratings per kind that count
LEARN_PRIOR = 3  # as if there were 3 extra "passte" – one rating alone moves nothing
LEARN_STEP = 0.34  # this share of (weak − strong) shifts the difficulty by one


async def learned_shift(db: AsyncSession, kind: str) -> int:
    """+1 = this kind was too often too weak, −1 = too often stronger than needed, 0 = fits."""
    rows = (await db.scalars(select(RouteFeedback.rating).where(RouteFeedback.kind == kind)
                             .order_by(RouteFeedback.at.desc()).limit(LEARN_WINDOW))).all()
    score = sum(1 if r == "weak" else -1 if r == "strong" else 0 for r in rows) / (len(rows) + LEARN_PRIOR)
    return 1 if score > LEARN_STEP else -1 if score < -LEARN_STEP else 0


async def rate(db: AsyncSession, message_id: str, rating: str, better_model: str | None = None) -> dict:
    """Saves the rating of one Auto answer (again = overwrites it) and marks it on the message."""
    if rating not in RATINGS:
        raise Invalid("Unbekannte Bewertung")
    msg = await db.get(ChatMessage, message_id)
    route_info = (msg.meta or {}).get("route") if msg else None
    if not route_info:
        raise Invalid("Diese Antwort wurde nicht von Auto gewählt")
    question = await db.scalar(select(ChatMessage.text).where(ChatMessage.chat_id == msg.chat_id, ChatMessage.role == "user",
                                                              ChatMessage.at <= msg.at).order_by(ChatMessage.at.desc()).limit(1))
    fb = await db.scalar(select(RouteFeedback).where(RouteFeedback.message_id == message_id))
    if not fb:
        fb = RouteFeedback(message_id=message_id)
        db.add(fb)
    fb.kind, fb.model = route_info["kind"], route_info["model"]
    fb.difficulty = route_info.get("rule_difficulty") or route_info["difficulty"]
    fb.rating, fb.better_model, fb.snippet = rating, better_model, (question or "")[:300]
    msg.meta = {**msg.meta, "route": {**route_info, "rating": rating}}
    await db.flush()
    return msg.meta["route"]


async def stats(db: AsyncSession) -> list[dict]:
    """Per kind: how many ratings, how many fitted, and where learning shifts it now."""
    rows = (await db.execute(select(RouteFeedback.kind, RouteFeedback.rating, func.count()).group_by(RouteFeedback.kind, RouteFeedback.rating))).all()
    out = {}
    for kind, rating, n in rows:
        out.setdefault(kind, {"kind": kind, "name": KIND_NAMES.get(kind, kind), "ok": 0, "weak": 0, "strong": 0})[rating] = n
    for k, v in out.items():
        v["total"] = v["ok"] + v["weak"] + v["strong"]
        v["shift"] = await learned_shift(db, k)
    return sorted(out.values(), key=lambda v: -v["total"])


async def reset_learning(db: AsyncSession) -> None:
    await db.execute(delete(RouteFeedback))


# ---- 2. which model ----

def _pick_in(cands: list[Profile], difficulty: int) -> Profile | None:
    """The model of the wanted tier (else the next stronger, else the next weaker); Antigravity: the variant whose
    thinking level fits the difficulty."""
    known = [p for p in cands if p.tier]
    for tier in [difficulty] + [t for t in (difficulty + 1, difficulty + 2) if t <= 3] + [t for t in (difficulty - 1, difficulty - 2) if t >= 1]:
        same = [p for p in known if p.tier == tier]
        if same:
            levelled = [p for p in same if p.level]
            if levelled:
                return min(levelled, key=lambda p: (abs(p.level - difficulty), same.index(p)))
            return same[0]
    return cands[0] if cands else None


async def _usable(db: AsyncSession, provider: str, cache: dict) -> bool:
    """At least one logged-in account of the provider that is not at its limit."""
    if provider not in cache:
        accs = [a for a in await providers.all_accounts(db) if a.provider == provider and providers.logged_in(a)]
        ok = False
        for a in accs:
            if not await providers.exhausted(a):
                ok = True
                break
        cache[provider] = ok
    return cache[provider]


async def route(db: AsyncSession, text: str, *, code_thread: bool = False, files: int = 0,
                last_model: str | None = None) -> dict:
    """The decision for one message: {"model", "effort", "kind", "difficulty", "reason"}.
    last_model: the model Auto took for the chat's previous answer (keeps the provider in "per_chat" mode)."""
    cfg = await config(db)
    profiles = await available(db)
    allowed = set(cfg["models"]) if cfg["models"] is not None else set(default_allowed(profiles))
    pool = [p for p in profiles if p.id in allowed] or [p for p in profiles if p.id in default_allowed(profiles)] or profiles
    if not pool:
        raise Invalid("Auto hat kein Modell zur Auswahl – erst einen Account einrichten (Einstellungen → Accounts)")
    kind, rule_difficulty, why = classify(text, code_thread=code_thread, files=files)
    shift = await learned_shift(db, kind)
    difficulty = min(3, max(1, rule_difficulty + shift))

    mode = cfg["provider_mode"]
    last_provider = providers.of_model(last_model) if last_model else None
    default_provider = providers.of_model(get_settings().default_model)
    if mode in ("claude", "antigravity"):
        order = [mode]
    elif mode == "per_chat" and last_provider:
        order = [last_provider]
    else:
        first = PREFER.get(kind) or last_provider or default_provider
        order = [first, "antigravity" if first == "claude" else "claude"]

    cache: dict = {}
    chosen, note = None, ""
    for i, prov in enumerate(order):
        cands = [p for p in pool if p.provider == prov]
        if not cands:
            continue
        if not await _usable(db, prov, cache):
            note = f" · {providers.PROVIDERS[prov]} am Limit"
            continue
        chosen = _pick_in(cands, difficulty)
        if chosen:
            break
    if not chosen:  # everything at the limit or nothing of the allowed provider: take what fits anyway (the run reports the limit)
        chosen = _pick_in([p for p in pool if p.provider == order[0]] or pool, difficulty)

    effort = EFFORT_OF[difficulty] if supports_effort(chosen.id) else None
    learned = " · gelernt: stärker" if difficulty > rule_difficulty else " · gelernt: sparsamer" if difficulty < rule_difficulty else ""
    reason = f"{KIND_NAMES[kind]}, {LEVEL_NAMES[difficulty]} ({why}){learned}{note}"
    return {"model": chosen.id, "effort": effort, "kind": kind, "difficulty": difficulty, "rule_difficulty": rule_difficulty, "reason": reason}
