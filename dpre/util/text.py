"""Text normalization and similarity helpers (stdlib only)."""
from __future__ import annotations

import hashlib
import re
import unicodedata

_TOKEN_SPLIT = re.compile(r"[^0-9a-zA-Z]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

# Common abbreviations seen in physical column names, expanded before comparison
# so that `cust_acct_no` and `customer_account_number` tokenize alike (ER-4).
ABBREVIATIONS = {
    "acct": "account", "acc": "account", "cust": "customer", "custm": "customer",
    "no": "number", "num": "number", "nbr": "number", "id": "identifier",
    "amt": "amount", "qty": "quantity", "dt": "date", "ts": "timestamp",
    "desc": "description", "cd": "code", "cde": "code", "typ": "type",
    "flg": "flag", "ind": "indicator", "pct": "percent", "avg": "average",
    "bal": "balance", "txn": "transaction", "trx": "transaction",
    "addr": "address", "prem": "premise", "mtr": "meter", "svc": "service",
    "pmt": "payment", "pymt": "payment", "inv": "invoice", "org": "organization",
    "emp": "employee", "prod": "product", "rev": "revenue", "yr": "year",
    "mth": "month", "mo": "month", "dtl": "detail", "hdr": "header",
    "src": "source", "tgt": "target", "ref": "reference", "seq": "sequence",
}

STOP_TOKENS = {"the", "of", "a", "an", "and", "for", "to", "in", "by"}


def strip_accents(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c))


def normalize_identifier(value: str | None) -> str:
    """Case/quote normalization used by ER-1 (exact match)."""
    if value is None:
        return ""
    text = strip_accents(str(value)).strip()
    text = text.strip('"').strip("'").strip("[").strip("]").strip("`")
    return text.lower()


def tokenize(value: str | None) -> list[str]:
    """Split on non-alphanumerics and camelCase, lowercase, expand abbreviations."""
    if not value:
        return []
    text = _CAMEL.sub(" ", strip_accents(str(value)))
    raw = [t for t in _TOKEN_SPLIT.split(text) if t]
    out: list[str] = []
    for token in raw:
        low = token.lower()
        out.append(ABBREVIATIONS.get(low, low))
    return [t for t in out if t and t not in STOP_TOKENS]


def token_key(value: str | None) -> str:
    return " ".join(sorted(tokenize(value)))


def jaro(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    window = max(len(a), len(b)) // 2 - 1
    window = max(window, 0)
    a_flags = [False] * len(a)
    b_flags = [False] * len(b)
    matches = 0
    for i, ch in enumerate(a):
        start = max(0, i - window)
        end = min(i + window + 1, len(b))
        for j in range(start, end):
            if not b_flags[j] and b[j] == ch:
                a_flags[i] = b_flags[j] = True
                matches += 1
                break
    if matches == 0:
        return 0.0
    transpositions = 0
    k = 0
    for i, flagged in enumerate(a_flags):
        if not flagged:
            continue
        while not b_flags[k]:
            k += 1
        if a[i] != b[k]:
            transpositions += 1
        k += 1
    transpositions //= 2
    m = float(matches)
    return (m / len(a) + m / len(b) + (m - transpositions) / m) / 3.0


def jaro_winkler(a: str, b: str, prefix_scale: float = 0.1) -> float:
    """Jaro-Winkler similarity on tokenized names (ER-4)."""
    base = jaro(a, b)
    prefix = 0
    for x, y in zip(a, b):
        if x != y:
            break
        prefix += 1
        if prefix == 4:
            break
    return base + prefix * prefix_scale * (1 - base)


def name_similarity(a: str | None, b: str | None) -> float:
    """Similarity over normalized, abbreviation-expanded token strings."""
    ta, tb = tokenize(a), tokenize(b)
    if not ta or not tb:
        return 0.0
    if sorted(ta) == sorted(tb):
        return 1.0
    joined_a, joined_b = " ".join(ta), " ".join(tb)
    jw = jaro_winkler(joined_a, joined_b)
    sa, sb = set(ta), set(tb)
    jaccard = len(sa & sb) / float(len(sa | sb))
    return max(jw, 0.5 * jw + 0.5 * jaccard)


def pseudo_embedding(text: str, dims: int = 64) -> list[float]:
    """Deterministic hashed bag-of-tokens vector.

    Stands in for Snowflake Cortex ``EMBED_TEXT_768`` so the engine runs
    offline; :mod:`dpre.ai` swaps in a real embedder when one is configured.
    """
    vec = [0.0] * dims
    tokens = tokenize(text)
    if not tokens:
        return vec
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        for k in range(4):
            idx = digest[k] % dims
            sign = 1.0 if digest[k + 4] % 2 == 0 else -1.0
            vec[idx] += sign
    norm = sum(v * v for v in vec) ** 0.5
    if norm == 0:
        return vec
    return [v / norm for v in vec]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def embedding_similarity(a: str | None, b: str | None) -> float:
    """Label/definition similarity in the 0..1 range used by ER-6 and §5.2."""
    if not a or not b:
        return 0.0
    raw = cosine_similarity(pseudo_embedding(a), pseudo_embedding(b))
    lexical = name_similarity(a, b)
    # Blend so that the offline stand-in stays monotonic with human judgement.
    return max(0.0, min(1.0, 0.45 * max(raw, 0.0) + 0.55 * lexical))


def snake_case(value: str) -> str:
    tokens = tokenize(value)
    return "_".join(tokens) if tokens else "unnamed"


def title_case(value: str) -> str:
    tokens = tokenize(value)
    return " ".join(t.capitalize() for t in tokens) if tokens else value


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
