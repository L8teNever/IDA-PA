"""Model "auto": picks provider, model and strength for each message by itself.

Step 1 (rules + profiles): the message is sorted into a kind (code, writing, research, chat, general) and a
difficulty (1 light … 3 hard). Each model has a profile (CURATED below, changeable in the settings): strength
1–3, which difficulties it serves, and skills 1–10 for code, writing, research and speed. Among the models that
fit the difficulty Auto takes the one best at this kind (ties: the lighter one), sets the thinking level and skips
accounts at their limit. Which models it may use and whether it may change provider are set under
Einstellungen → Auto (settings key "autoroute").

Step 2 (learning): after an answer the user can rate the choice (passte / zu schwach / zu stark). Per kind the
ratings shift the difficulty one step up or down once enough of them point the same way (table route_feedback).

Step 3 (similar questions): with an embedding model in Ollama (same as the memory), each rated question is stored
as a vector. A new message that closely resembles rated ones takes the strength those ratings say was right –
this beats the per-kind shift. Without Ollama this step is skipped.
"""
import asyncio
import re
from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models import ChatMessage, RouteFeedback
from . import antigravity, providers, settings_store
from .memory import embeddings
from .errors import Invalid

AUTO = "auto"
KEY = "autoroute"
PROVIDER_MODES = ("per_chat", "free", "claude", "antigravity")
DEFAULTS = {"provider_mode": "per_chat", "models": None, "profiles": {}}  # models None = default pool; profiles = own changes per model
EFFORT_OF = {1: "low", 2: "medium", 3: "high"}
KIND_NAMES = {"code": "Code", "writing": "Schreiben", "research": "Recherche", "chat": "Smalltalk", "general": "Allgemein"}
LEVEL_NAMES = {1: "leicht", 2: "mittel", 3: "schwer"}
SKILLS = ("code", "writing", "research", "speed")
SKILL_NAMES = {"code": "Code", "writing": "Schreiben", "research": "Wissen/Recherche", "speed": "Tempo"}


def is_auto(model: str | None) -> bool:
    return model == AUTO


# Curated profiles of the known models (matched in order against the id without "agy:" and without the level suffix).
# tier = natural strength (1 light, 2 middle, 3 strong); covers = difficulties it may serve (Flash on "High" also
# handles middle ones); skills 1–10 from public benchmarks (Stand 2026-11, mostly vendor/aggregator figures – rough);
# default = in the pool without an own choice. Everything can be changed under Einstellungen → Auto.
CURATED: list[tuple[str, dict]] = [
    (r"claude-opus-5-5", {"tier": 3, "code": 10, "writing": 9, "research": 8, "speed": 3,
                          "note": "stärkstes Claude – v. a. Code und Agenten-Aufgaben"}),
    (r"claude-sonnet-5-5", {"tier": 2, "code": 8, "writing": 8, "research": 7, "speed": 6, "note": "ausgewogen"}),
    (r"claude-haiku-5-5", {"tier": 1, "code": 6, "writing": 6, "research": 5, "speed": 9, "note": "schnell und sparsam"}),
    (r"claude-fable-5-1", {"tier": 2, "code": 5, "writing": 8, "research": 6, "speed": 6, "note": "kreatives Schreiben"}),
    (r"claude-(opus|sonnet|haiku|fable)-[45]", {"old": True}),
    (r"gemini-4-argon", {"tier": 3, "code": 9, "writing": 8, "research": 10, "speed": 4,
                         "note": "stärkstes Gemini – Wissen, lange Texte, Recherche"}),
    (r"gemini-3\.8-flash", {"tier": 1, "covers": (1, 2), "code": 7, "writing": 6, "research": 7, "speed": 9,
                            "note": "schnell; auf „Hoch“ auch für Mittleres"}),
    (r"image", {"chat": False, "note": "erzeugt Bilder – nicht für Chat"}),
    (r"gemini-(2\.5|3-|3\.1|3\.5)", {"old": True}),
    (r"gpt-oss", {"tier": 1, "code": 4, "writing": 5, "research": 5, "speed": 5, "default": False,
                  "note": "offenes Modell, schwächer als Gemini Flash"}),
]


@dataclass
class Profile:
    id: str
    name: str
    provider: str
    tier: int | None  # 1 light, 2 middle, 3 strong; None = unknown (only used when nothing else fits)
    level: int | None = None  # Antigravity variants "(Low|Medium|High)": thinking level of this variant
    skills: dict | None = None
    covers: tuple = ()
    chat: bool = True  # usable for chat answers at all
    default: bool = True  # in the pool without an own choice
    note: str = ""
    curated: bool = False

    @property
    def base(self) -> str:
        """The model without its level variant (agy:gemini-3.8-flash-high -> agy:gemini-3.8-flash)."""
        return re.sub(r"-(low|medium|high)$", "", self.id)

    def skill(self, kind: str) -> float:
        k = self.skills or {}
        if kind == "chat":
            return k.get("speed", 5)
        if kind == "general":
            return (k.get("code", 5) + k.get("writing", 5) + k.get("research", 5)) / 3
        return k.get(kind, 5)


def _tier(text: str) -> int | None:
    t = text.lower()
    if re.search(r"haiku|flash|\blite\b|\bmini\b|\bnano\b", t):
        return 1
    if re.search(r"opus|\bpro\b|ultra|argon", t):
        return 3
    if re.search(r"sonnet|gpt|claude", t):
        return 2
    return None


def profile(model: str, name: str | None = None, override: dict | None = None) -> Profile:
    name = name or model
    agy = antigravity.is_model(model)
    level = None
    if agy:
        m = re.search(r"\((low|medium|high)\)\s*$", name, re.I) or re.search(r"-(low|medium|high)$", model)
        level = {"low": 1, "medium": 2, "high": 3}[m.group(1).lower()] if m else None
    bare = re.sub(r"-(low|medium|high)$", "", model[len(antigravity.PREFIX):] if agy else model)
    p = Profile(model, name, "antigravity" if agy else "claude", _tier(name + " " + model), level)
    cur = next((c for rx, c in CURATED if re.search(rx, bare)), None)
    if cur:
        p.curated = True
        p.note = cur.get("note", "")
        if cur.get("old"):
            p.default, p.note = False, "ältere Version"
            p.skills = {k: 4 for k in SKILLS}
        elif cur.get("chat") is False:
            p.chat = p.default = False
        else:
            p.tier = cur["tier"]
            p.skills = {k: cur[k] for k in SKILLS}
            p.default = cur.get("default", True)
            p.covers = tuple(cur.get("covers", ()))
    elif p.tier:
        p.skills = {k: 5 for k in SKILLS}
    else:
        p.default = False  # unknown name: only when chosen by hand
    if override:
        if override.get("tier") in (1, 2, 3):
            if override["tier"] != p.tier:
                p.covers = ()
            p.tier = override["tier"]
        p.skills = {**(p.skills or {k: 5 for k in SKILLS}), **{k: int(v) for k, v in override.items() if k in SKILLS and 1 <= int(v) <= 10}}
    if not p.covers and p.tier:
        p.covers = (p.tier,)
    return p


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
    own = (await config(db))["profiles"] or {}
    return [profile(m, names.get(m), own.get(m)) for m in await providers.model_list(db)]


def default_allowed(profiles: list[Profile]) -> list[str]:
    """Without an own choice: the curated current models (no old versions, no image models) – Claude only when it is
    one of the configured MODELS."""
    configured = set(get_settings().model_list)
    return [p.id for p in profiles if p.default and p.chat and (p.provider == "antigravity" or p.id in configured)]


async def save(db: AsyncSession, provider_mode: str, models: list[str] | None, profiles: dict | None = None) -> dict:
    if provider_mode not in PROVIDER_MODES:
        raise Invalid("Unbekannte Anbieter-Einstellung")
    cur = await config(db)
    clean = {}
    for mid, o in (cur["profiles"] if profiles is None else profiles).items():
        o = {k: int(v) for k, v in (o or {}).items() if k in (*SKILLS, "tier") and v is not None}
        if any(not (1 <= v <= (3 if k == "tier" else 10)) for k, v in o.items()):
            raise Invalid(f"Ungültige Werte für {mid} (Stärke 1–3, Fähigkeiten 1–10)")
        if o:
            clean[mid] = o
    await settings_store.put(db, KEY, {"provider_mode": provider_mode, "models": models, "profiles": clean})
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


async def rate(db: AsyncSession, message_id: str, rating: str | None, better_model: str | None = None) -> dict:
    """Saves the rating of one Auto answer (again = overwrites it) and marks it on the message.
    With better_model and no rating, the rating follows from it: stronger tier = weak, weaker = strong, same = ok."""
    if better_model and not providers.known_model(better_model):
        raise Invalid(f"Unbekanntes Modell „{better_model}“")
    if (rating or not better_model) and rating not in RATINGS:
        raise Invalid("Unbekannte Bewertung")
    msg = await db.get(ChatMessage, message_id)
    route_info = (msg.meta or {}).get("route") if msg else None
    if not route_info:
        raise Invalid("Diese Antwort wurde nicht von Auto gewählt")
    question = await db.scalar(select(ChatMessage.text).where(ChatMessage.chat_id == msg.chat_id, ChatMessage.role == "user",
                                                              ChatMessage.at <= msg.at).order_by(ChatMessage.at.desc()).limit(1))
    if not rating:
        have, want = profile(route_info["model"]).tier or 2, profile(better_model).tier or 2
        rating = "weak" if want > have else "strong" if want < have else "ok"
    fb = await db.scalar(select(RouteFeedback).where(RouteFeedback.message_id == message_id))
    if not fb:
        fb = RouteFeedback(message_id=message_id)
        db.add(fb)
    fb.kind, fb.model = route_info["kind"], route_info["model"]
    fb.difficulty = route_info.get("rule_difficulty") or route_info["difficulty"]
    snippet = (question or "")[:300]
    if snippet != fb.snippet or not fb.embedding:
        vec = await _embed(snippet)
        fb.embedding, fb.embed_model = (embeddings.to_bytes(vec[1]), vec[0]) if vec else (None, None)
    fb.rating, fb.better_model, fb.snippet = rating, better_model, snippet
    msg.meta = {**msg.meta, "route": {**{k: v for k, v in route_info.items() if k != "better"}, "rating": rating,
                                      **({"better": better_model} if better_model else {})}}
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


# ---- similar earlier questions (embeddings) ----

SIMILAR_MIN = 0.78  # cosine from which an earlier question counts as "the same kind of request"
SIMILAR_K = 5
SIMILAR_WEIGHT = 1.5  # summed similarity needed before they decide (≈ two close matches)
SIMILAR_SCAN = 500  # newest ratings compared


async def _embed(text: str):
    """(model, vector) or None – never holds up a message for long."""
    if not text.strip():
        return None
    try:
        got = await asyncio.wait_for(embeddings.embed([text[:1000]]), timeout=4)
    except (asyncio.TimeoutError, Exception):  # noqa: BLE001 – Ollama is optional
        return None
    return (got[0], got[1][0]) if got else None


def _ideal(fb: RouteFeedback) -> int | None:
    """Which difficulty the rating says would have been right for that question."""
    if fb.better_model:
        return profile(fb.better_model).tier
    tier = profile(fb.model).tier or fb.difficulty
    return min(3, max(1, tier + (1 if fb.rating == "weak" else -1 if fb.rating == "strong" else 0)))


async def similar_difficulty(db: AsyncSession, text: str) -> tuple[int, int] | None:
    """(difficulty, number of similar questions) from the closest rated questions, or None (too few / no Ollama)."""
    if not await db.scalar(select(func.count()).select_from(RouteFeedback).where(RouteFeedback.embedding.is_not(None))):
        return None  # nothing to compare – do not even ask Ollama
    vec = await _embed(text)
    if not vec:
        return None
    model, q = vec
    rows = (await db.scalars(select(RouteFeedback).where(RouteFeedback.embed_model == model, RouteFeedback.embedding.is_not(None))
                             .order_by(RouteFeedback.at.desc()).limit(SIMILAR_SCAN))).all()
    near = sorted(((embeddings.cosine(q, embeddings.from_bytes(r.embedding)), r) for r in rows), key=lambda x: -x[0])
    near = [(c, r) for c, r in near[:SIMILAR_K] if c >= SIMILAR_MIN]
    ideals = [(c, _ideal(r)) for c, r in near]
    ideals = [(c, d) for c, d in ideals if d]
    weight = sum(c for c, _ in ideals)
    if weight < SIMILAR_WEIGHT:
        return None
    return min(3, max(1, round(sum(c * d for c, d in ideals) / weight))), len(ideals)


# ---- 2. which model ----

def _wanted_level(p: Profile, difficulty: int) -> int:
    """Thinking level for a model: full when it works above its natural strength, else by the difficulty."""
    return 3 if (p.tier or 2) < difficulty else difficulty


def _choose(cands: list[Profile], kind: str, difficulty: int, prefer: str | None) -> Profile | None:
    """Among the models that may serve this difficulty, the one best at this kind; ties -> the lighter one, then the
    preferred provider. Nothing fits -> the closest strength. Antigravity: then the variant with the fitting level."""
    cands = [p for p in cands if p.chat and p.tier]
    if not cands:
        return None
    fit = [p for p in cands if difficulty in p.covers]
    if not fit:  # e.g. only light models allowed for a hard question: the closest strength, rather stronger
        dist = min(abs((p.tier or 2) - difficulty) for p in cands)
        fit = [p for p in cands if abs((p.tier or 2) - difficulty) == dist]
        fit = [p for p in fit if (p.tier or 2) >= difficulty] or fit
    order = {p.id: i for i, p in enumerate(cands)}
    best = max(fit, key=lambda p: (p.skill(kind), -(p.tier or 2), p.provider == prefer, -order[p.id]))
    variants = [p for p in fit if p.base == best.base and p.level]
    if variants:
        want = _wanted_level(best, difficulty)
        best = min(variants, key=lambda p: (abs(p.level - want), order[p.id]))
    return best


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
    similar = await similar_difficulty(db, text)
    if similar:
        difficulty = similar[0]

    mode = cfg["provider_mode"]
    last_provider = providers.of_model(last_model) if last_model else None
    prefer = last_provider or providers.of_model(get_settings().default_model)
    if mode in ("claude", "antigravity"):
        provs = [mode]
    elif mode == "per_chat" and last_provider:
        provs = [last_provider]
    else:  # free choice (or first message of a chat): the best model of both providers
        provs = ["claude", "antigravity"]

    cache: dict = {}
    note, usable = "", []
    for prov in provs:
        if await _usable(db, prov, cache):
            usable.append(prov)
        elif any(p.provider == prov for p in pool):
            note = f" · {providers.PROVIDERS[prov]} am Limit"
    chosen = _choose([p for p in pool if p.provider in usable], kind, difficulty, prefer)
    if not chosen:  # everything at the limit or nothing of the allowed provider: take what fits anyway (the run reports the limit)
        chosen = _choose([p for p in pool if p.provider in provs] or pool, kind, difficulty, prefer) or pool[0]

    effort = EFFORT_OF[_wanted_level(chosen, difficulty)] if supports_effort(chosen.id) else None
    learned = " · gelernt: stärker" if difficulty > rule_difficulty else " · gelernt: sparsamer" if difficulty < rule_difficulty else ""
    if similar:
        learned = f" · wie {similar[1]} ähnliche bewertete Anfragen"
    strong_at = f" · stark bei {KIND_NAMES[kind]}" if kind != "chat" and chosen.skills and chosen.skill(kind) >= 8 else ""
    reason = f"{KIND_NAMES[kind]}, {LEVEL_NAMES[difficulty]} ({why}){learned}{strong_at}{note}"
    return {"model": chosen.id, "effort": effort, "kind": kind, "difficulty": difficulty, "rule_difficulty": rule_difficulty, "reason": reason}
