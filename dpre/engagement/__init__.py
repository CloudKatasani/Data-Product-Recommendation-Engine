"""Engagement model, branding context, scope checks and the extract-request pack (R-24, R-55).

* ``model``        - EngagementRecord, the reviewer roster (D-08), the
                     ENGAGEMENT / ENGAGEMENT_REVIEWER / RUN_ENGAGEMENT tables
                     and the converter to the export layer's cover-page view
* ``branding``     - the client / firm / confidentiality block for screens,
                     seeds and packs, loadable from JSON or the environment
* ``scope``        - cut-date and domain checks between the engagement and
                     the extract
* ``extract_pack`` - the T-5 request pack built from the ingestion contracts

Nothing here imports ``dpre.pipeline`` or ``dpre.store``; the pipeline and
the server call in with a connection.
"""
from .branding import (
    Branding, branding_from_engagement, footer_line, load_branding, logo_data_uri,
    seed_header_line, topbar, written_by,
)
from .extract_pack import (
    SOURCES, extract_request_pack, request_letter, write_extract_request_pack,
)
from .model import (
    DECISION_ROLES, ROSTER_ROLES, SCHEMA, EngagementError, EngagementRecord, RosterEntry,
    attach_run, close_engagement, engagement_for_run, engagement_id_for, ensure_schema,
    from_export_engagement, get_engagement, hosted_roles, list_engagements, reviewer_may,
    run_attribution, run_context, runs_for_engagement, save_engagement, to_export_engagement,
    token_map_entries, unattributed_runs,
)
from .scope import check_scope, scope_summary

__all__ = [
    "Branding", "branding_from_engagement", "footer_line", "load_branding", "logo_data_uri",
    "seed_header_line", "topbar", "written_by", "SOURCES", "extract_request_pack",
    "request_letter", "write_extract_request_pack", "DECISION_ROLES", "ROSTER_ROLES", "SCHEMA",
    "EngagementError", "EngagementRecord", "RosterEntry", "attach_run", "close_engagement",
    "engagement_for_run", "engagement_id_for", "ensure_schema", "from_export_engagement",
    "get_engagement", "hosted_roles", "list_engagements", "reviewer_may", "run_attribution",
    "run_context", "runs_for_engagement", "save_engagement", "to_export_engagement",
    "token_map_entries", "unattributed_runs", "check_scope", "scope_summary",
]
