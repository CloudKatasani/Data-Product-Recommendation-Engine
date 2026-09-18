"""Who the pack is for and who produced it (review finding R-12).

The engine knows the estate; it does not know the client's name, the engagement
code or the committee the pack is tabled at. Those come from the engagement
team, so they are a small, explicit input with safe defaults rather than
something inferred from the data. Nothing here affects a score, a status or a
run: it is cover-page material only.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class Engagement:
    """Cover-page facts for one engagement.

    ``as_of_override`` exists only so a pack can be re-cut with the client's own
    reporting date on the cover while the run's own as-of stays the truth for
    every number; the summary prints both when they differ.
    """

    client: str = "Client"
    engagement: str = "Data product rationalisation"
    reference: str = ""
    prepared_by: str = "Data product engagement team"
    prepared_for: str = "Data product council"
    partner: str = ""
    manager: str = ""
    confidentiality: str = "Confidential - prepared for the named recipients only"
    as_of_override: str = ""

    @classmethod
    def coerce(cls, value: Any) -> "Engagement":
        """Accept an Engagement, a dict of its fields, or nothing at all."""
        if value is None:
            return cls()
        if isinstance(value, cls):
            return value
        if isinstance(value, dict):
            fields = {f for f in cls.__dataclass_fields__}
            return cls(**{k: v for k, v in value.items() if k in fields})
        raise TypeError(f"engagement must be an Engagement, a dict or None, not {type(value)!r}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def title(self) -> str:
        return f"{self.client} - {self.engagement}"

    def cover_rows(self) -> list[tuple[str, str]]:
        """Label/value pairs for the cover block, skipping what was not supplied."""
        rows = [("Client", self.client), ("Engagement", self.engagement)]
        if self.reference:
            rows.append(("Engagement reference", self.reference))
        rows += [("Prepared for", self.prepared_for), ("Prepared by", self.prepared_by)]
        if self.partner:
            rows.append(("Engagement partner", self.partner))
        if self.manager:
            rows.append(("Engagement manager", self.manager))
        return rows
