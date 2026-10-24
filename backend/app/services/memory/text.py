"""Text helpers for the memory: tokens, near-duplicate test, BM25 and rank fusion."""
import math
import re

STOP = set(
    "der die das den dem des ein eine einen einem einer und oder ist sind war wird wurde hat haben nicht auch sich "
    "bei zum zur von mit für fuer auf wie wer was wen wem wenn dass dann aber nur noch schon sehr mehr als aus nach "
    "über ueber the and for with that this are".split()
)
WORD = re.compile(r"[^\W_]{3,}", re.UNICODE)
K1, B = 1.2, 0.75
RRF_K = 60


def stem(w: str) -> str:
    """Long words are cut to 6 chars, so "Kaffees"/"Kaffee" or "Mails"/"Mail" match."""
    return w[:6] if len(w) > 6 else w


def terms(text: str) -> list[str]:
    return [stem(w) for w in WORD.findall(str(text).lower()) if w not in STOP]


def tokens(text: str) -> set[str]:
    return set(terms(text))


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    n = len(a & b)
    return n / (len(a) + len(b) - n)


def bm25_ranking(docs: dict[str, str], query: str) -> list[str]:
    """Ids of the documents containing a query term, best BM25 score first."""
    q = list(tokens(query))
    if not q or not docs:
        return []
    toks = {i: terms(t) for i, t in docs.items()}
    avg = sum(len(d) for d in toks.values()) / len(toks) or 1
    idf = {w: math.log(1 + (len(toks) - (df := sum(1 for d in toks.values() if w in d)) + 0.5) / (df + 0.5)) for w in q}
    scores = {}
    for i, d in toks.items():
        s = 0.0
        for w in q:
            tf = d.count(w)
            if tf:
                s += idf[w] * tf * (K1 + 1) / (tf + K1 * (1 - B + B * len(d) / avg))
        if s > 0:
            scores[i] = s
    return sorted(scores, key=scores.get, reverse=True)


def rrf(rankings: list[list[str]]) -> dict[str, float]:
    """Reciprocal Rank Fusion, normalised to 0..1 (1 = first in every ranking)."""
    if not rankings:
        return {}
    out: dict[str, float] = {}
    for r in rankings:
        for pos, i in enumerate(r):
            out[i] = out.get(i, 0) + 1 / (RRF_K + pos + 1)
    top = len(rankings) / (RRF_K + 1)
    return {i: min(1.0, s / top) for i, s in out.items()}
