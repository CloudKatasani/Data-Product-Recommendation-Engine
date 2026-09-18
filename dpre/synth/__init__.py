"""Synthetic data pack: one parameterized model, one workbook per industry."""
from __future__ import annotations

import datetime as _dt
from pathlib import Path

from .generator import GenerationParams, SyntheticGenerator, SyntheticPack
from .industries import INDUSTRY_KEYS, IndustryPack, get_pack, list_industries
from .workbook import TAB_ORDER, write_pack_workbook

__all__ = [
    "GenerationParams", "SyntheticGenerator", "SyntheticPack", "INDUSTRY_KEYS",
    "IndustryPack", "get_pack", "list_industries", "TAB_ORDER", "write_pack_workbook",
    "generate_pack", "generate_all_workbooks",
]


def generate_pack(industry: str = "generic", seed: int | None = None,
                  as_of: _dt.date | None = None) -> SyntheticPack:
    return SyntheticGenerator(industry, seed=seed, as_of=as_of).generate()


def generate_all_workbooks(out_dir: str | Path, as_of: _dt.date | None = None,
                           seed: int | None = None) -> list[Path]:
    """Write one workbook per industry into ``out_dir``."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for key in INDUSTRY_KEYS:
        pack = generate_pack(key, seed=seed, as_of=as_of)
        target = out_dir / f"synthetic_pack_{key}.xlsx"
        write_pack_workbook(pack, target)
        written.append(target)
    return written
