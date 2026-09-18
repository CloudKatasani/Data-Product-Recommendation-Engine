"""Branding and engagement context for screens, seeds and packs (R-24).

The product name is hard-coded in ``dpre/server/static/index.html``, seed
headers say "Seeded by the Data Product Recommendation Engine"
(``dpre/seeds/charter.py``, ``decision_register.py``) and the catalog payload
sets ``written_by`` the same way. No client name, firm name, confidentiality
marking or version stamp reaches a screen or an artefact. An audit committee
receiving a candidate dossier needs all four on every page.

``Branding`` is the one block that carries them. It loads from a JSON file
(``DPRE_BRANDING``) with environment overrides so a deployment can set it
without a code change, and ``branding_from_engagement`` derives it from the
engagement record so the two never disagree. The helpers here produce the
strings the seeds, the payload and the HTML footer should use; they do not
patch those modules, which belong to other owners (see ``wiring_needed`` in
docs/engagement-model.md).
"""
from __future__ import annotations

import base64
import json
import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from ..config import ENGINE_VERSION

DEFAULT_PRODUCT_NAME = "Data Product Recommendation Engine"
DEFAULT_CONFIDENTIALITY = "Confidential - prepared for the named recipients only"

ENV_FILE = "DPRE_BRANDING"
ENV_FIELDS = {
    "DPRE_PRODUCT_NAME": "product_display_name",
    "DPRE_FIRM_NAME": "firm_name",
    "DPRE_CLIENT_NAME": "client_name",
    "DPRE_ENGAGEMENT_NAME": "engagement_name",
    "DPRE_LOGO_PATH": "logo_path",
    "DPRE_CONFIDENTIALITY": "confidentiality_notice",
}

_LOGO_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".svg": "image/svg+xml", ".gif": "image/gif"}
_LOGO_MAX_BYTES = 512 * 1024


@dataclass(frozen=True)
class Branding:
    product_display_name: str = DEFAULT_PRODUCT_NAME
    firm_name: str = ""
    client_name: str = ""
    engagement_name: str = ""
    logo_path: str = ""
    confidentiality_notice: str = DEFAULT_CONFIDENTIALITY
    version_stamp: str = ENGINE_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def prepared_by(self) -> str:
        return self.firm_name or "Data product engagement team"

    @property
    def title(self) -> str:
        parts = [p for p in (self.client_name, self.engagement_name) if p]
        return " - ".join(parts) if parts else self.product_display_name


def load_branding(path: str | Path | None = None,
                  env: Mapping[str, str] | None = None) -> Branding:
    """JSON file first (``path`` or ``$DPRE_BRANDING``), then environment overrides.

    Unknown keys in the file are ignored; a missing file is not an error, so a
    bare deployment gets the defaults and a branded one gets its block.
    """
    env = os.environ if env is None else env
    data: dict[str, Any] = {}
    source = path or env.get(ENV_FILE)
    if source:
        target = Path(source)
        if target.is_file():
            try:
                loaded = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                loaded = {}
            if isinstance(loaded, dict):
                data.update({k: v for k, v in loaded.items()
                             if k in Branding.__dataclass_fields__ and isinstance(v, str)})
    for variable, field_name in ENV_FIELDS.items():
        value = env.get(variable)
        if value:
            data[field_name] = value
    return Branding(**data)


def branding_from_engagement(record: Any, base: Branding | None = None) -> Branding:
    """Derive the block from an EngagementRecord (or anything with its fields)."""
    base = base or Branding()
    return replace(
        base,
        client_name=getattr(record, "client", "") or base.client_name,
        engagement_name=getattr(record, "name", "") or base.engagement_name,
        firm_name=getattr(record, "firm", "") or base.firm_name,
        confidentiality_notice=getattr(record, "confidentiality", "") or base.confidentiality_notice,
    )


# --------------------------------------------------------------------------
# Strings the artefacts should carry
# --------------------------------------------------------------------------

def seed_header_line(branding: Branding, run_id: str = "", as_of: str = "") -> str:
    """Replacement for 'Seeded by the Data Product Recommendation Engine' in seed headers."""
    who = f"Seeded by the {branding.product_display_name} {branding.version_stamp}"
    if branding.firm_name:
        who += f" for {branding.firm_name}"
    if branding.client_name:
        who += f" on behalf of {branding.client_name}"
    if branding.engagement_name:
        who += f" ({branding.engagement_name})"
    if run_id:
        who += f"; run {run_id}"
    if as_of:
        who += f"; as of {as_of}"
    return who


def written_by(branding: Branding) -> str:
    """Value for the catalog payload's ``written_by``."""
    return f"{branding.product_display_name} {branding.version_stamp}" + (
        f" ({branding.firm_name})" if branding.firm_name else "")


def footer_line(branding: Branding, run_id: str = "", as_of: str = "") -> str:
    """Confidentiality footer for HTML exports and dossiers."""
    parts = [branding.confidentiality_notice]
    if branding.client_name:
        parts.append(f"Prepared for {branding.client_name}")
    parts.append(f"Prepared by {branding.prepared_by}")
    if run_id:
        parts.append(f"Run {run_id}" + (f" as of {as_of}" if as_of else ""))
    parts.append(f"{branding.product_display_name} {branding.version_stamp}")
    return ". ".join(parts) + "."


def topbar(branding: Branding, context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """What ``GET /api/v1/branding`` should return for the topbar and the run pill."""
    context = dict(context or {})
    return {
        "product_display_name": branding.product_display_name,
        "firm_name": branding.firm_name,
        "client_name": context.get("client") or branding.client_name,
        "engagement_name": context.get("engagement") or branding.engagement_name,
        "confidentiality_notice": branding.confidentiality_notice,
        "version_stamp": branding.version_stamp,
        "has_logo": bool(logo_data_uri(branding)),
        "run_label": context.get("label", ""),
        "data_cut_date": context.get("data_cut_date", ""),
    }


def logo_data_uri(branding: Branding) -> str:
    """The logo as a data URI for a self-contained HTML export; '' when absent or oversize."""
    if not branding.logo_path:
        return ""
    path = Path(branding.logo_path)
    media = _LOGO_TYPES.get(path.suffix.lower())
    if not media or not path.is_file():
        return ""
    try:
        raw = path.read_bytes()
    except OSError:
        return ""
    if len(raw) > _LOGO_MAX_BYTES:
        return ""
    return f"data:{media};base64," + base64.b64encode(raw).decode("ascii")
