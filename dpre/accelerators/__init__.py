"""Industry accelerators: curated content a client run can borrow (R-38).

The synthetic generator in ``dpre/synth`` stays the fixture; this package is
the accelerator. See ``packs.py`` for what each industry carries and how the
pipeline, the narrator and the generator are meant to consume it.
"""
from .packs import (
    ACCELERATOR_KEYS, STARTER_STATUS, GlossaryEntry, IndustryAccelerator, KpiEntry,
    RegulatoryPattern, accelerator_sheets, all_kpi_labels, backbone_for_industry,
    get_accelerator, is_regulatory_report, kpi_catalogue, list_accelerators,
    merge_starter_glossary, normalize_key, persona_for, regulatory_patterns,
    starter_glossary_records, steward_role_for, write_accelerator_workbook,
)

__all__ = [
    "ACCELERATOR_KEYS", "STARTER_STATUS", "GlossaryEntry", "IndustryAccelerator", "KpiEntry",
    "RegulatoryPattern", "accelerator_sheets", "all_kpi_labels", "backbone_for_industry",
    "get_accelerator", "is_regulatory_report", "kpi_catalogue", "list_accelerators",
    "merge_starter_glossary", "normalize_key", "persona_for", "regulatory_patterns",
    "starter_glossary_records", "steward_role_for", "write_accelerator_workbook",
]
