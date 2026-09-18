"""Planted defect classes and the detection the engine is expected to show.

Mirrors specification section 17.2. Each planted instance is written to the
``Planted_Defects`` tab so detection can be scored rather than asserted.
"""
from __future__ import annotations

from dataclasses import dataclass, field

DEFECT_CLASSES = {
    "IDENTICAL_KPI": (
        "Identical KPI in many reports",
        "Same expression, same filter, repeated across 5-15 reports",
        "Collapse to one canonical metric",
    ),
    "THRESHOLD_DRIFT": (
        "Threshold drift",
        "Same measure with a different hard-coded threshold inside the expression",
        "Nominal conflict; parameterized threshold in the Stage 6 semantic model",
    ),
    "EXCLUSION_DRIFT": (
        "Exclusion drift",
        "One variant adds a filter the others lack",
        "Variant with a filter dimension",
    ),
    "DENOMINATOR_SWAP": (
        "Denominator swap",
        "Same label, different denominator column",
        "Nominal conflict for steward adjudication",
    ),
    "TIME_BASIS_DRIFT": (
        "Time-basis drift",
        "Calendar period column in one report, fiscal period column in another",
        "Nominal conflict; explicit time grain",
    ),
    "CROSS_TOOL_DUPLICATION": (
        "Cross-tool duplication",
        "The same KPI exists in Cognos and as a Power BI DAX measure",
        "One canonical metric spanning both tools",
    ),
    "GRAIN_MIXING": (
        "Grain mixing",
        "A community with measures at two grains",
        "Grain split into parent and child candidates",
    ),
    "BROKEN_LINEAGE": (
        "Broken lineage",
        "Lineage rows point at a column absent from the catalog",
        "Quarantine with a reason code; feasibility penalty",
    ),
    "MISSING_DEFINITION": (
        "Missing definitions",
        "A share of columns carry no business term",
        "Definition-coverage penalty and a gap list entry",
    ),
    "UNASSIGNED_STEWARD": (
        "Unassigned steward",
        "A share of glossary terms have no steward",
        "Steward gap on the candidate card",
    ),
    "SUNSET_SOURCE": (
        "Sunset source",
        "One source system marked sunset with no successor",
        "Gate G4; candidate status Blocked",
    ),
    "OPAQUE_EXPRESSION": (
        "Opaque expression",
        "Expression contains a report prompt or macro the parser cannot read",
        "Opaque tier, feasibility penalty, manual definition queue",
    ),
    "ZOMBIE_REPORT": (
        "Zombie report",
        "High historic run counts but no run in over 14 months",
        "Recency decay drops it out of demand",
    ),
    "REGULATORY_LOW_USAGE": (
        "Regulatory low-usage report",
        "Few runs, high consequence",
        "Under-ranked unless a reviewer marks it decision-critical (section 15.1)",
    ),
    "STRUCTURAL_COUSIN": (
        "Structural cousin",
        "Same operand column, different aggregation (total vs average)",
        "Separate metrics, cross-linked",
    ),
}


@dataclass
class PlantedDefect:
    defect_id: str
    defect_class: str
    name: str
    how_planted: str
    expected_detection: str
    objects: list[str] = field(default_factory=list)
    count: int = 1
    detail: str = ""

    def to_row(self) -> dict:
        return {
            "defect_id": self.defect_id,
            "defect_class": self.defect_class,
            "defect_name": self.name,
            "how_planted": self.how_planted,
            "expected_detection": self.expected_detection,
            "affected_objects": "; ".join(self.objects[:12]),
            "affected_count": self.count,
            "detail": self.detail,
        }


class DefectLog:
    """Collects planted defects and hands out stable ids."""

    def __init__(self) -> None:
        self.items: list[PlantedDefect] = []
        self._counter: dict[str, int] = {}

    def plant(self, defect_class: str, objects: list[str], count: int = 1, detail: str = "") -> PlantedDefect:
        name, how, expected = DEFECT_CLASSES[defect_class]
        seq = self._counter.get(defect_class, 0) + 1
        self._counter[defect_class] = seq
        defect = PlantedDefect(
            defect_id=f"{defect_class}-{seq:02d}",
            defect_class=defect_class,
            name=name,
            how_planted=how,
            expected_detection=expected,
            objects=list(objects),
            count=count,
            detail=detail,
        )
        self.items.append(defect)
        return defect

    def rows(self) -> list[dict]:
        return [d.to_row() for d in self.items]

    def summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for item in self.items:
            out[item.defect_class] = out.get(item.defect_class, 0) + 1
        return out
