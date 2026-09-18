"""Deterministic identifier helpers so runs are replayable (section 13.1)."""
from __future__ import annotations

import datetime as _dt
import hashlib


def stable_id(prefix: str, *parts: object, length: int = 10) -> str:
    raw = "|".join("" if p is None else str(p) for p in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length].upper()
    return f"{prefix}-{digest}"


def run_id(seed: str, as_of: _dt.date | None = None) -> str:
    stamp = (as_of or _dt.date.today()).strftime("%Y%m%d")
    return stable_id(f"RUN{stamp}", seed, length=8)
