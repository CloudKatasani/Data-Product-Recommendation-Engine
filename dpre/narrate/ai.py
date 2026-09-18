"""The language-model seam, with provenance (review finding R-42).

In Snowflake this is Cortex ``AI_COMPLETE`` and ``EMBED_TEXT_768``. Offline the
engine uses deterministic templates instead, so a run is reproducible and needs
no external service. Either way the output is marked ``AI_DRAFT`` until a human
accepts it, and a model is never allowed to decide equivalence - only to name
and describe what the deterministic steps already grouped.

Governance expectations the seam now meets:

* **Every call is recorded**, template or model, in an in-memory ledger the
  pipeline drains after the Narrator step (``drain_ledger``) and persists to
  ``RUN_AI_CALL`` (``save_ledger``). A row carries the purpose, the model (or
  ``template``), the prompt version, SHA-256 of prompt and response, and the
  reason a configured provider fell back to the template, so a governance
  board can answer "what left the estate, to which model, and did it work".
* **Only allow-listed fields leave the estate.** ``build_prompt`` drops any
  record key outside ``PROMPT_FIELD_ALLOWLIST`` before serialising.
* **PII-flagged column names are redacted** before prompting: the narrator
  passes the PII column names of the candidate and every occurrence in the
  prompt is replaced by a placeholder.
* **A configured provider that fails is not silent**: the fallback reason is on
  the ledger row and ``provenance_summary`` lists it as a warning for the run
  manifest.

The ledger is process-wide so the seam works without a store; it is cleared by
``drain_ledger`` and ``reset``. Nothing here reads the clock.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import asdict, dataclass
from typing import Any, Callable

Completion = Callable[[str, str], str]
_PROVIDER: Completion | None = None
_MODEL = os.environ.get("DPRE_AI_MODEL", "")
_LEDGER: list["AiCall"] = []
_SEQUENCE = 0

TEMPLATE = "template"
PROMPT_VERSION = "narrator-prompts-1.1"
PII_PLACEHOLDER = "[PII column withheld]"

# Fields a prompt may carry. Anything else in a record is dropped before
# serialisation, so a new field added to the candidate does not leak by default.
PROMPT_FIELD_ALLOWLIST = frozenset({
    "proposed_name", "grain", "domain", "sub_domain", "archetype", "tier",
    "consumers", "business_unit", "users", "cadence", "metrics", "metric_labels",
    "aggregation", "conflicts", "titles", "candidate", "questions",
})

SCHEMA = """
CREATE TABLE IF NOT EXISTS RUN_AI_CALL (
    run_id TEXT, seq INTEGER, candidate_id TEXT, purpose TEXT, source TEXT, model TEXT,
    prompt_version TEXT, prompt_sha256 TEXT, response_sha256 TEXT, fallback_reason TEXT,
    redactions INTEGER, prompt_chars INTEGER, response_chars INTEGER,
    PRIMARY KEY (run_id, seq)
);
"""


def ensure_schema(connection: sqlite3.Connection) -> None:
    """Idempotent; called at the top of every function that touches the table."""
    connection.executescript(SCHEMA)
    connection.commit()


@dataclass
class AiCall:
    """One ledger row: what was asked, of whom, and what came back."""

    seq: int
    purpose: str
    source: str                      # template | model
    model: str                       # model id, or 'template'
    prompt_version: str
    prompt_sha256: str
    response_sha256: str
    fallback_reason: str = ""        # empty when the configured provider answered
    candidate_id: str = ""
    redactions: int = 0
    prompt_chars: int = 0
    response_chars: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AiResult:
    text: str
    source: str                      # template | model
    model: str = ""
    call: AiCall | None = None

    @property
    def status(self) -> str:
        return "AI_DRAFT"

    @property
    def provenance(self) -> dict[str, Any]:
        """What a card or a seed shows next to the AI_DRAFT badge."""
        if self.call is None:
            return {"source": self.source, "model": self.model or TEMPLATE,
                    "prompt_version": PROMPT_VERSION}
        return {
            "source": self.call.source, "model": self.call.model,
            "prompt_version": self.call.prompt_version,
            "prompt_sha256": self.call.prompt_sha256,
            "response_sha256": self.call.response_sha256,
            "fallback_reason": self.call.fallback_reason,
            "ledger_seq": self.call.seq,
        }


# --------------------------------------------------------------------------
# provider registration
# --------------------------------------------------------------------------

def register_provider(provider: Completion | None, model: str = "") -> None:
    """Install a completion function ``(system_prompt, user_prompt) -> text``."""
    global _PROVIDER, _MODEL
    _PROVIDER = provider
    _MODEL = model or (os.environ.get("DPRE_AI_MODEL", "") if provider else "")


def available() -> bool:
    return _PROVIDER is not None


def model_id() -> str:
    return _MODEL if _PROVIDER is not None else ""


def reset() -> None:
    """Forget the provider and the ledger (tests, and a fresh process)."""
    global _PROVIDER, _MODEL, _SEQUENCE
    _PROVIDER = None
    _MODEL = ""
    _SEQUENCE = 0
    _LEDGER.clear()


# --------------------------------------------------------------------------
# prompt construction: allow-list and redaction
# --------------------------------------------------------------------------

def sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def filter_fields(record: dict[str, Any],
                  allow: frozenset[str] | set[str] = PROMPT_FIELD_ALLOWLIST) -> dict[str, Any]:
    """Keep only allow-listed keys, recursively for nested dicts and lists of dicts."""
    out: dict[str, Any] = {}
    for key, value in record.items():
        if key not in allow:
            continue
        out[key] = _filter_value(value, allow)
    return out


def _filter_value(value: Any, allow) -> Any:
    if isinstance(value, dict):
        return filter_fields(value, allow)
    if isinstance(value, list):
        return [_filter_value(v, allow) for v in value]
    return value


def redact(text: str, pii_names: set[str] | list[str] | tuple[str, ...]) -> tuple[str, int]:
    """Replace every occurrence of a PII column name (fully qualified or leaf).

    Longer names are replaced first so a leaf name inside a qualified name does
    not leave a fragment behind.
    """
    names: set[str] = set()
    for name in pii_names or ():
        if not name:
            continue
        names.add(str(name))
        names.add(str(name).rsplit(".", 1)[-1])
    count = 0
    for name in sorted(names, key=len, reverse=True):
        if len(name) < 3 or name not in text:
            continue
        count += text.count(name)
        text = text.replace(name, PII_PLACEHOLDER)
    return text, count


def build_prompt(instruction: str, record: dict[str, Any],
                 pii_names: set[str] | list[str] | tuple[str, ...] = (),
                 allow: frozenset[str] | set[str] = PROMPT_FIELD_ALLOWLIST) -> tuple[str, int]:
    """Instruction plus an allow-listed, PII-redacted JSON record.

    Returns the prompt and the number of redactions, so the ledger can show
    that something was withheld.
    """
    payload = json.dumps(filter_fields(record, allow), indent=2, sort_keys=True, default=str)
    text, redactions = redact(f"{instruction}\n\n{payload}", pii_names)
    return text, redactions


# --------------------------------------------------------------------------
# completion with ledger
# --------------------------------------------------------------------------

def complete(system_prompt: str, user_prompt: str, fallback: str, purpose: str = "",
             candidate_id: str = "", redactions: int = 0) -> AiResult:
    """Ask the configured model, or fall back to the deterministic template.

    Every call lands on the ledger, with the fallback reason when a configured
    provider failed or returned nothing. A naming service must never fail a
    run, so exceptions are recorded, not raised.
    """
    prompt_hash = sha256(system_prompt + "\n" + user_prompt)
    source, model, text, reason = TEMPLATE, TEMPLATE, fallback, ""
    if _PROVIDER is None:
        reason = "no provider configured"
    else:
        try:
            answer = (_PROVIDER(system_prompt, user_prompt) or "").strip()
        except Exception as exc:                  # noqa: BLE001 - recorded, never raised
            answer = ""
            reason = f"provider raised {type(exc).__name__}: {exc}"[:300]
        if answer:
            source, model, text = "model", _MODEL or "model", answer
        elif not reason:
            reason = "provider returned an empty response"
    call = _record(purpose, source, model, prompt_hash, sha256(text), reason, candidate_id,
                   redactions, len(user_prompt), len(text))
    return AiResult(text, source, model if source == "model" else "", call)


def _record(purpose: str, source: str, model: str, prompt_hash: str, response_hash: str,
            reason: str, candidate_id: str, redactions: int, prompt_chars: int,
            response_chars: int) -> AiCall:
    global _SEQUENCE
    _SEQUENCE += 1
    call = AiCall(seq=_SEQUENCE, purpose=purpose or "unspecified", source=source, model=model,
                  prompt_version=PROMPT_VERSION, prompt_sha256=prompt_hash,
                  response_sha256=response_hash, fallback_reason=reason,
                  candidate_id=candidate_id, redactions=redactions,
                  prompt_chars=prompt_chars, response_chars=response_chars)
    _LEDGER.append(call)
    return call


def ledger() -> list[AiCall]:
    """The calls recorded since the last drain, oldest first (a copy)."""
    return list(_LEDGER)


def drain_ledger() -> list[AiCall]:
    """Return every recorded call and clear the ledger; the pipeline calls this
    after the Narrator and Critic steps and persists the rows with ``save_ledger``."""
    global _SEQUENCE
    rows = list(_LEDGER)
    _LEDGER.clear()
    _SEQUENCE = 0
    return rows


def provenance_summary(calls: list[AiCall] | None = None) -> dict[str, Any]:
    """Counts by purpose and source, plus the warnings a run manifest should carry."""
    rows = list(_LEDGER) if calls is None else list(calls)
    by_purpose: dict[str, dict[str, int]] = {}
    fallbacks: list[dict[str, Any]] = []
    for call in rows:
        bucket = by_purpose.setdefault(call.purpose, {"model": 0, "template": 0})
        bucket[call.source] = bucket.get(call.source, 0) + 1
        if call.fallback_reason and call.fallback_reason != "no provider configured":
            fallbacks.append({"seq": call.seq, "purpose": call.purpose,
                              "candidate_id": call.candidate_id,
                              "reason": call.fallback_reason})
    model_calls = sum(1 for c in rows if c.source == "model")
    warnings: list[str] = []
    if fallbacks:
        warnings.append(
            f"AI provider {_MODEL or 'configured'} fell back to the template on "
            f"{len(fallbacks)} of {len(rows)} calls; first reason: {fallbacks[0]['reason']}")
    return {
        "ai_enabled": available(),
        "model": _MODEL if available() else "",
        "prompt_version": PROMPT_VERSION,
        "calls": len(rows),
        "model_calls": model_calls,
        "template_calls": len(rows) - model_calls,
        "redactions": sum(c.redactions for c in rows),
        "by_purpose": by_purpose,
        "fallbacks": fallbacks,
        "warnings": warnings,
        "egress": ("no text left the estate; every draft came from a deterministic template"
                   if model_calls == 0 else
                   f"{model_calls} prompts were sent to {_MODEL or 'the configured model'}; "
                   "prompts carry allow-listed fields only and PII column names are redacted"),
    }


# --------------------------------------------------------------------------
# persistence
# --------------------------------------------------------------------------

def save_ledger(connection: sqlite3.Connection, run_id: str, calls: list[AiCall]) -> int:
    """Write ledger rows for one run; replaces any earlier rows for that run."""
    ensure_schema(connection)
    connection.execute("DELETE FROM RUN_AI_CALL WHERE run_id = ?", (run_id,))
    for call in calls:
        connection.execute(
            "INSERT INTO RUN_AI_CALL VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, call.seq, call.candidate_id, call.purpose, call.source, call.model,
             call.prompt_version, call.prompt_sha256, call.response_sha256,
             call.fallback_reason, call.redactions, call.prompt_chars, call.response_chars))
    connection.commit()
    return len(calls)


def load_ledger(connection: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    ensure_schema(connection)
    connection.row_factory = sqlite3.Row
    rows = connection.execute("SELECT * FROM RUN_AI_CALL WHERE run_id = ? ORDER BY seq",
                              (run_id,)).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# system prompts (versioned through PROMPT_VERSION)
# --------------------------------------------------------------------------

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
