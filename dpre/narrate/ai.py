"""The language-model seam.

In Snowflake this is Cortex ``AI_COMPLETE`` and ``EMBED_TEXT_768``. Offline the
engine uses deterministic templates instead, so a run is reproducible and needs
no external service. Either way the output is marked ``AI_DRAFT`` until a human
accepts it, and a model is never allowed to decide equivalence - only to name
and describe what the deterministic steps already grouped.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable

Completion = Callable[[str, str], str]
_PROVIDER: Completion | None = None


@dataclass
class AiResult:
    text: str
    source: str            # template | model
    model: str = ""

    @property
    def status(self) -> str:
        return "AI_DRAFT"


def register_provider(provider: Completion | None, model: str = "") -> None:
    """Install a completion function ``(system_prompt, user_prompt) -> text``."""
    global _PROVIDER, _MODEL
    _PROVIDER = provider
    _MODEL = model


_MODEL = os.environ.get("DPRE_AI_MODEL", "")


def available() -> bool:
    return _PROVIDER is not None


def complete(system_prompt: str, user_prompt: str, fallback: str) -> AiResult:
    """Ask the configured model, or fall back to the deterministic template."""
    if _PROVIDER is None:
        return AiResult(fallback, "template")
    try:
        text = _PROVIDER(system_prompt, user_prompt)
    except Exception:            # a naming service must never fail a run
        return AiResult(fallback, "template")
    text = (text or "").strip()
    if not text:
        return AiResult(fallback, "template")
    return AiResult(text, "model", _MODEL)


NARRATOR_SYSTEM = (
    "You name and describe data product candidates for a governed backlog. You are "
    "given one candidate record and nothing else. Never invent a metric, a consumer "
    "or a source that is not in the record. Never assert that a calculation is "
    "correct. Write plainly, in the language of the decision the product serves."
)
CRITIC_SYSTEM = (
    "You review a data product candidate against the Data Product Factory Stage 1 "
    "and Stage 2 exit criteria and list what a reviewer will reject. Be specific "
    "and cite the field that is missing. Do not propose a score."
)
