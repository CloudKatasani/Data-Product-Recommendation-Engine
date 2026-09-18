"""Engagement model, branding, scope checks, extract pack and accelerators (R-24, R-55, R-38)."""
from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from pathlib import Path

import pytest

from dpre.accelerators import (
    ACCELERATOR_KEYS, STARTER_STATUS, accelerator_sheets, backbone_for_industry,
    get_accelerator, is_regulatory_report, kpi_catalogue, list_accelerators,
    merge_starter_glossary, persona_for, starter_glossary_records, steward_role_for,
    write_accelerator_workbook,
)
from dpre.engagement import (
    Branding, EngagementError, EngagementRecord, RosterEntry, attach_run,
    branding_from_engagement, check_scope, close_engagement, engagement_for_run,
    engagement_id_for, extract_request_pack, footer_line, from_export_engagement,
    get_engagement, list_engagements, load_branding, reviewer_may, request_letter,
    run_attribution, run_context, runs_for_engagement, save_engagement, scope_summary,
    seed_header_line, to_export_engagement, token_map_entries, topbar, unattributed_runs,
    write_extract_request_pack, written_by,
)
from dpre.export.engagement import Engagement as ExportEngagement
from dpre.graph import build_graph
from dpre.ingest import ingest_automated
from dpre.ingest.schemas import SCHEMAS
from dpre.ingest.validator import validate
from dpre.pipeline import run_pipeline
from dpre.registers import config_version
from dpre.store import Store
from dpre.synth import get_pack
from dpre.util.xlsx import read_workbook

ROOT = Path(__file__).resolve().parents[1]


def _record(**overrides) -> EngagementRecord:
    base = dict(
        client="Acme Utilities", code="ACM-2026-07", name="Data product rationalisation",
        sponsor="Chief Data Officer", lead_partner="a.partner", manager="b.manager",
        firm="The Firm", start_date="2026-09-01", end_date="2026-12-19",
        scope="Billing and collections estate, Cognos and Power BI",
        domains=("Billing & Collections", "Finance"), data_cut_date="2026-09-17",
        reviewers=(
            RosterEntry("priya.silva", "reviewer", ("Billing & Collections",)),
            RosterEntry("j.finance", "reviewer", ("Finance",)),
            RosterEntry("s.steward", "steward"),
            RosterEntry("cdo.office", "council"),
            RosterEntry("p.privacy", "privacy_officer"),
        ),
    )
    base.update(overrides)
    return EngagementRecord(**base)


# --------------------------------------------------------------------------
# Engagement model (R-24)
# --------------------------------------------------------------------------

def test_an_engagement_needs_a_client_and_valid_roster_roles():
    with pytest.raises(EngagementError):
        EngagementRecord(client="")
    with pytest.raises(EngagementError):
        _record(reviewers=(RosterEntry("x", "auditor"),))
    with pytest.raises(EngagementError):
        _record(status="archived")
    with pytest.raises(ValueError):
        _record(data_cut_date="17/09/2026")
    record = _record()
    assert record.engagement_id == engagement_id_for("Acme Utilities", "ACM-2026-07")
    assert record.engagement_id == engagement_id_for("acme utilities", "acm-2026-07")
    assert record.title == "Acme Utilities - Data product rationalisation"
    assert EngagementRecord.from_dict(record.to_dict()) == record


def test_a_run_is_attributed_to_one_engagement(tmp_path, as_of):
    store = Store(tmp_path / "engine.db")
    result = run_pipeline(ingest_automated("utility", as_of=as_of), store=store, label="pilot")
    assert unattributed_runs(store.connection) == [result.run_id]
    record = save_engagement(store.connection, _record(), created_by="b.manager")
    assert record.created_by == "b.manager" and record.created_at
    with pytest.raises(EngagementError):
        attach_run(store.connection, result.run_id, "ENG-NOPE")
    link = attach_run(store.connection, result.run_id, record.engagement_id,
                      config_version=config_version(result.config), attached_by="b.manager")
    assert link["config_version"].startswith("cfg-")
    assert unattributed_runs(store.connection) == []
    assert engagement_for_run(store.connection, result.run_id).client == "Acme Utilities"
    assert run_attribution(store.connection, result.run_id)["engagement_id"] == record.engagement_id
    runs = runs_for_engagement(store.connection, record.engagement_id)
    assert [r["run_id"] for r in runs] == [result.run_id]
    assert runs[0]["label"] == "pilot" and runs[0]["config_version"] == link["config_version"]
    # A run is never moved to another client.
    other = save_engagement(store.connection, _record(client="Beta Bank", code="BB-1"))
    with pytest.raises(EngagementError, match="never moved"):
        attach_run(store.connection, result.run_id, other.engagement_id)
    listing = {e["client"]: e for e in list_engagements(store.connection)}
    assert listing["Acme Utilities"]["runs"] == 1 and listing["Beta Bank"]["runs"] == 0
    closed = close_engagement(store.connection, other.engagement_id)
    assert closed.status == "closed"
    assert [e["client"] for e in list_engagements(store.connection, status="active")] == \
        ["Acme Utilities"]
    # Re-saving keeps the creation stamp and refreshes the roster.
    again = save_engagement(store.connection, _record(reviewers=(RosterEntry("only", "reviewer"),)))
    assert again.created_by == "b.manager"
    assert [r.identity for r in get_engagement(store.connection, again.engagement_id).reviewers] \
        == ["only"]
    store.close()


def test_the_roster_answers_who_may_accept_per_domain():
    record = _record()
    ok, why = reviewer_may(record, "priya.silva", "Accept", "Billing & Collections")
    assert ok and "appointed reviewer" in why
    ok, why = reviewer_may(record, "priya.silva", "Accept", "Finance")
    assert not ok and "covering Finance" in why
    assert reviewer_may(record, "j.finance", "Accept", "Finance")[0]
    assert reviewer_may(record, "s.steward", "resolve_conflict", "Finance")[0]
    assert not reviewer_may(record, "s.steward", "Accept", "Finance")[0]
    assert reviewer_may(record, "cdo.office", "approve_weights")[0]
    assert reviewer_may(record, "p.privacy", "decide")[0]
    assert not reviewer_may(record, "nobody", "Accept")[0]
    assert not reviewer_may(record, "cdo.office", "teleport")[0]
    tokens = token_map_entries(record)
    assert tokens["priya.silva"] == {"identity": "priya.silva", "roles": ["reviewer"],
                                     "domains": ["Billing & Collections"]}
    assert "p.privacy" not in tokens                 # not an HTTP role
    # The HTTP matrix and the roster agree on the roles they share.
    from dpre.server.security import ROLE_ACTIONS
    assert set(ROLE_ACTIONS) <= set(EngagementRecord.__dataclass_fields__ and
                                    __import__("dpre.engagement.model", fromlist=["ROSTER_ROLES"]).ROSTER_ROLES)


def test_the_export_layer_view_round_trips():
    record = _record()
    cover = to_export_engagement(record)
    assert isinstance(cover, ExportEngagement)
    assert cover.client == "Acme Utilities" and cover.reference == "ACM-2026-07"
    assert cover.prepared_by == "The Firm" and cover.prepared_for == "Chief Data Officer"
    assert cover.as_of_override == "2026-09-17"
    assert ("Engagement reference", "ACM-2026-07") in cover.cover_rows()
    lifted = from_export_engagement(cover, domains=("Finance",))
    assert lifted.client == record.client and lifted.code == record.code
    assert lifted.data_cut_date == "2026-09-17" and lifted.domains == ("Finance",)
    context = run_context(record, label="pilot")
    assert context["client"] == "Acme Utilities" and context["label"] == "pilot"
    assert run_context(None)["client"] == ""


# --------------------------------------------------------------------------
# Branding
# --------------------------------------------------------------------------

def test_branding_loads_from_json_and_the_environment(tmp_path):
    assert load_branding(env={}) == Branding()
    path = tmp_path / "branding.json"
    path.write_text(json.dumps({"firm_name": "The Firm", "client_name": "Acme",
                                "unknown": "ignored", "version_stamp": 7}), encoding="utf-8")
    loaded = load_branding(env={"DPRE_BRANDING": str(path), "DPRE_CLIENT_NAME": "Acme Utilities"})
    assert loaded.firm_name == "The Firm"
    assert loaded.client_name == "Acme Utilities"          # environment wins
    assert loaded.version_stamp == Branding().version_stamp  # non-string ignored
    assert load_branding(path=tmp_path / "missing.json", env={}) == Branding()
    derived = branding_from_engagement(_record(), loaded)
    assert derived.engagement_name == "Data product rationalisation"
    header = seed_header_line(derived, run_id="RUN-1", as_of="2026-09-17")
    assert "The Firm" in header and "Acme Utilities" in header and "RUN-1" in header
    assert "Data Product Recommendation Engine" in written_by(derived)
    footer = footer_line(derived, "RUN-1", "2026-09-17")
    assert footer.startswith("Confidential") and "Prepared for Acme Utilities" in footer
    bar = topbar(derived, {"label": "pilot", "data_cut_date": "2026-09-17"})
    assert bar["client_name"] == "Acme Utilities" and bar["run_label"] == "pilot"
    assert bar["has_logo"] is False


def test_a_logo_is_inlined_only_when_it_is_a_real_image(tmp_path):
    from dpre.engagement.branding import logo_data_uri
    png = tmp_path / "logo.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    assert logo_data_uri(Branding(logo_path=str(png))).startswith("data:image/png;base64,")
    assert logo_data_uri(Branding(logo_path=str(tmp_path / "logo.exe"))) == ""
    assert logo_data_uri(Branding(logo_path=str(tmp_path / "absent.png"))) == ""
    big = tmp_path / "big.png"
    big.write_bytes(b"\x00" * (600 * 1024))
    assert logo_data_uri(Branding(logo_path=str(big))) == ""


# --------------------------------------------------------------------------
# Scope and cut date
# --------------------------------------------------------------------------

def test_scope_check_flags_cut_date_and_domain_drift(as_of):
    bundle = ingest_automated("utility", as_of=as_of).bundle
    record = _record(data_cut_date="2026-09-01", domains=("Billing & Collections", "Nowhere"))
    issues = {i["code"]: i for i in check_scope(record, bundle)}
    assert issues["AS_OF_AFTER_CUT_DATE"]["severity"] == "warning"
    assert "USAGE_AFTER_CUT_DATE" in issues
    assert "DOMAINS_OUT_OF_SCOPE" in issues and "Finance" in issues["DOMAINS_OUT_OF_SCOPE"]["detail"]
    assert "Nowhere" in issues["SCOPED_DOMAIN_ABSENT"]["detail"]
    summary = scope_summary(record, bundle)
    assert summary["ok"] is False and summary["extract_as_of"] == as_of.isoformat()
    clean = _record(data_cut_date=as_of.isoformat(),
                    domains=tuple(get_pack("utility").domains))
    codes = {i["code"] for i in check_scope(clean, bundle)}
    assert "AS_OF_AFTER_CUT_DATE" not in codes and "DOMAINS_OUT_OF_SCOPE" not in codes
    assert scope_summary(clean, bundle)["ok"] is True
    bare = _record(data_cut_date="", domains=())
    assert {i["code"] for i in check_scope(bare, bundle)} >= {"NO_CUT_DATE", "NO_SCOPED_DOMAINS"}


# --------------------------------------------------------------------------
# Extract-request pack (R-55)
# --------------------------------------------------------------------------

def test_the_extract_request_pack_has_a_tab_per_contract_with_how_to_obtain(tmp_path):
    sheets = extract_request_pack(_record())
    assert set(sheets) == {"README", "Request_Summary"} | {s.tab for s in SCHEMAS.values()}
    summary = sheets["Request_Summary"]
    assert summary[0][0] == "Extract"
    by_label = {row[0]: row for row in summary[1:]}
    assert len(by_label) == len(SCHEMAS)
    for schema in SCHEMAS.values():
        row = by_label[schema.label]
        assert row[2] and row[4], schema.key            # owner and how-to-obtain
    assert by_label["Cognos KPI lineage report"][-1] == "required"
    assert "COGIPF" in by_label["Cognos rationalization report"][4]
    lineage = sheets["Cognos_KPI_Lineage"]
    header, rows = lineage[0], lineage[1:]
    assert header[:2] == ["Field", "Required"]
    assert {r[0] for r in rows} == {f.name for f in SCHEMAS["cognos_kpi_lineage"].fields}
    expression = next(r for r in rows if r[0] == "calculation_expression")
    assert expression[1] == "yes" and "parser" in expression[5]
    assert all(r[4] for r in rows)                      # every field has an example
    assert any("Acme Utilities" in str(cell) for row in sheets["README"] for cell in row)
    path = write_extract_request_pack(tmp_path / "extract_request_pack.xlsx", _record())
    book = read_workbook(path)
    assert set(book) == set(sheets)
    assert book["Collibra_Metadata"][0][0] == "Field"
    letter = request_letter(_record())
    assert "Acme Utilities" in letter and "[REQUIRED]" in letter and "2026-09-17" in letter


# --------------------------------------------------------------------------
# Accelerators (R-38)
# --------------------------------------------------------------------------

def test_every_industry_has_an_accelerator_aligned_with_its_synthetic_pack():
    listing = {a["key"]: a for a in list_accelerators()}
    assert set(listing) == set(ACCELERATOR_KEYS)
    for key in ACCELERATOR_KEYS:
        acc = get_accelerator(key)
        pack = get_pack(key)
        assert acc.backbone == pack.backbone, key
        assert acc.domains == pack.domains, key
        assert set(acc.personas) == set(pack.business_units), key
        assert set(acc.steward_roles) == set(pack.domains), key
        assert len(acc.kpis) >= 8 and len(acc.glossary) >= 10, key
        assert acc.regulatory, key
        for kpi in acc.kpis:
            assert kpi.formula and kpi.grain in acc.backbone and kpi.domain in acc.domains, kpi
        for term in acc.glossary:
            assert term.definition.endswith(".") and len(term.definition) > 30, term.term
            assert term.domain in acc.domains, term.term
        related = {g.related_kpi for g in acc.glossary if g.related_kpi}
        assert related <= {k.canonical_name for k in acc.kpis}, key
    assert get_accelerator("bank").key == "banking" and get_accelerator("telco").key == "telecom"
    with pytest.raises(KeyError):
        get_accelerator("aerospace")


def test_starter_glossary_has_real_definitions_and_no_invented_stewards(as_of):
    records = starter_glossary_records("banking")
    assert all(r.status == STARTER_STATUS and r.steward == "" for r in records)
    assert all("attribute of" not in r.definition for r in records)
    by_term = {r.term: r for r in records}
    assert "Charge Off" in by_term and "recoveries" in by_term["Charge Off"].definition
    bundle = ingest_automated("banking", as_of=as_of).bundle
    before = len(bundle.glossary)
    existing = {t.term.casefold() for t in bundle.glossary}
    added = merge_starter_glossary(bundle, "banking")
    assert added == len([r for r in records if r.term.casefold() not in existing])
    assert len(bundle.glossary) == before + added
    assert merge_starter_glossary(bundle, "banking") == 0     # idempotent
    assert validate(bundle).ok                                 # starter terms do not break ingest
    graph = build_graph(bundle, backbone=backbone_for_industry("banking"))
    assert graph.stats["backbone"] == list(get_accelerator("banking").backbone)


def test_personas_stewards_and_regulatory_patterns_are_usable_hints():
    assert persona_for("utility", "Credit & Collections") == "Collections manager"
    assert persona_for("utility", "credit and collections team").startswith("Collections")
    assert persona_for("utility", "Astrophysics") == ""
    assert steward_role_for("banking", "Risk") == "Credit Risk Reporting Manager"
    assert steward_role_for("banking", "Nowhere") == ""
    hit = is_regulatory_report("utility", "Quarterly Regulatory Filing Summary")
    assert hit is not None and hit.regime
    assert is_regulatory_report("utility", "Weekly Arrears Aging") is None
    catalogue = kpi_catalogue("healthcare")
    readmission = next(k for k in catalogue if k["canonical_name"] == "readmission_rate")
    assert readmission["regulatory"] is True and readmission["typical_conflicts"]


def test_the_accelerator_workbook_carries_the_seven_tabs(tmp_path):
    sheets = accelerator_sheets("insurance")
    assert list(sheets) == ["README", "Backbone", "KPI_Dictionary", "Starter_Glossary",
                            "Personas", "Steward_Roles", "Regulatory_Patterns"]
    glossary = sheets["Starter_Glossary"]
    assert glossary[0] == ["Term", "Definition", "Domain", "Related KPI", "Steward", "Status"]
    assert all(row[4] == "" and row[5] == STARTER_STATUS for row in glossary[1:])
    path = write_accelerator_workbook("insurance", tmp_path / "insurance_accelerator.xlsx")
    book = read_workbook(path)
    assert set(book) == set(sheets)
    assert book["KPI_Dictionary"][1][0] == "Gross Written Premium"


def test_the_documents_name_the_real_entry_points():
    docs = ROOT / "docs"
    for name in ("engagement-model.md", "accelerators.md", "extract-request-pack.md",
                 "demo-script.md", "diagnostic-runbook.md", "snowflake-cortex-migration.md"):
        assert (docs / name).is_file(), name
    engagement_doc = (docs / "engagement-model.md").read_text(encoding="utf-8")
    assert "dpre/engagement/model.py" in engagement_doc and "RUN_ENGAGEMENT" in engagement_doc
    accelerators_doc = (docs / "accelerators.md").read_text(encoding="utf-8")
    assert "merge_starter_glossary" in accelerators_doc and "backbone_for_industry" in accelerators_doc
    snowflake_doc = (docs / "snowflake-cortex-migration.md").read_text(encoding="utf-8")
    assert "register_provider" in snowflake_doc and "embedding_similarity" in snowflake_doc
    assert "EMBED_TEXT_768" in snowflake_doc and "GRANT" in snowflake_doc
    demo = (docs / "demo-script.md").read_text(encoding="utf-8")
    assert "Planted_Defects" in demo and "run automated" in demo
