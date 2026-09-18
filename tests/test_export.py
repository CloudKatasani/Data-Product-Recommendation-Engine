"""The executive pack, communications, seed provenance, narration, AI ledger, labels.

Package P3 of the engagement review: findings R-12 (no run-level pack), R-37
(no communications), R-36 and R-23 (seed provenance and disposition-aware
retirement), R-58 (drafts read as machine output), R-42 (ungoverned AI seam)
and R-41 (the UI speaks the engineer's vocabulary).

Every test runs on the deterministic synthetic pack, so a failure is a
regression and never a flake.
"""
from __future__ import annotations

import csv
import json

import pytest

from dpre import labels
from dpre.canonicalize.conflicts import PATTERN_DECISIONS
from dpre.config import DIMENSION_WEIGHTS, FEATURE_WEIGHTS, STATUS_ORDER
from dpre.export import (TAB_NAMES, Engagement, build_pack, build_workbook,
                         candidate_dossier, executive_summary, render_html, render_markdown,
                         write_pack)
from dpre.export.enrichment import EnrichmentView
from dpre.graph.resolver import QUARANTINE_REASONS
from dpre.narrate import ai
from dpre.narrate.critic import STAGE_CRITERIA, STANDING_CRITERION
from dpre.narrate.narrator import (draft_questions, persona_for, plain_label, question_for_metric,
                                   unique_titles)
from dpre.portfolio.views import is_hold, is_retirable, retirement_action
from dpre.seeds import build_seeds, write_run_seeds
from dpre.seeds.communications import build_communications, slug, write_communications
from dpre.seeds.provenance import SYNTHETIC_IMPORT_BLOCK
from dpre.util.tabular import workbook_tabs

ENGAGEMENT = {"client": "Northwind Mutual", "engagement": "Data product rationalisation",
              "partner": "A Partner", "manager": "A Manager"}


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def enrichment(run):
    """The programme enrichment, when the programme package is present.

    The pack must build without it, so a missing or failing enrichment is not a
    failure of this module - it just exercises the ``None`` path instead.
    """
    result, store = run
    try:
        from dpre.programme import enrich_run
    except Exception:
        return None
    try:
        payload = enrich_run(result, store)
    except Exception:
        return None
    try:
        from dpre.programme.status import status_report
        payload["status"] = status_report(store, result.run_id,
                                          as_of=result.manifest.finished_at)
    except Exception:
        pass
    return payload


@pytest.fixture(scope="module")
def pack(run, enrichment):
    result, store = run
    return build_pack(result, store=store, engagement=ENGAGEMENT, enrichment=enrichment)


# --------------------------------------------------------------------------
# R-12: the executive pack
# --------------------------------------------------------------------------

def test_pack_carries_all_three_deliverables(run, pack):
    """Summary in both formats, a workbook with every tab, a dossier per candidate."""
    result, _ = run
    assert pack.executive_summary_md.startswith("# Northwind Mutual")
    assert pack.executive_summary_html.lstrip().startswith("<!DOCTYPE html>")
    assert list(pack.workbook) == list(TAB_NAMES)
    assert {d.candidate_id for d in pack.dossiers} == {c.candidate_id for c in result.candidates}
    assert len(pack.dossiers) == len(result.candidates)
    for dossier in pack.dossiers:
        assert dossier.markdown.strip()
        assert "<!DOCTYPE html>" in dossier.html


def test_executive_summary_answers_the_committee_questions(run, pack):
    """Every section the review asked for is present and says something."""
    result, _ = run
    keys = [section.key for section in pack.summary_document.sections]
    for expected in ("cover", "headline", "estate", "gates", "top", "coverage", "retirement",
                     "risks", "decisions", "asks", "method"):
        assert expected in keys, f"{expected} missing from the executive summary"
    text = pack.executive_summary_md
    # the gate verdict is a sentence, not a snake_case key with a float beside it
    assert "ingest_reconciliation" not in text
    assert "Extracts reconciled" in text
    assert "Lineage resolved" in text
    # the top candidates, with what a reviewer needs beside each
    for candidate in result.ranked()[:5]:
        assert candidate.candidate_id in text
    assert "Retirable reports" in text and "Conflicts to settle" in text
    # the open decisions, with owners
    for reference in ("D-01", "D-04", "D-08"):
        assert reference in text
    assert "Data product council" in text


def test_summary_reports_de_duplicated_retirement_figures(run, pack):
    """Claimed coverage is never presented as the retirement figure (R-23)."""
    result, _ = run
    figures = result.portfolio["estate_retirement"]
    text = pack.executive_summary_md
    assert f"| Covered completely | {figures['reports_fully_covered_distinct']:,} |" in text
    assert f"| Retirable or mergeable | {figures['reports_retirable']:,} |" in text
    assert figures["reports_retirable"] <= figures["reports_fully_covered_distinct"]
    if figures["reports_fully_covered_claimed"] > figures["reports_fully_covered_distinct"]:
        assert "the same report covered by more than one candidate" in text


def test_pack_marks_a_synthetic_run_everywhere(run, pack, tmp_path):
    """R-36: a demo pack can never be mistaken for the client's estate."""
    result, _ = run
    assert result.manifest.synthetic is True
    assert pack.synthetic is True
    assert "synthetic data - not client data" in pack.executive_summary_md.lower()
    assert "synthetic" in pack.executive_summary_html.lower()
    for dossier in pack.dossiers[:3]:
        assert "not client data" in dossier.markdown.lower()
        assert "not client data" in dossier.html.lower()
    paths = write_pack(result, tmp_path, pack=pack)
    index = next(p for p in paths if p.name == "README.md")
    assert "SYNTHETIC DATA" in index.read_text(encoding="utf-8").upper()


def test_html_is_self_contained_and_printable(pack):
    """No external asset, no script, and print rules that yield pages not a viewport."""
    html = pack.executive_summary_html
    assert "@media print" in html
    assert "@page" in html
    for forbidden in ("<script", "src=", "@import", "http://", "https://"):
        assert forbidden not in html, f"{forbidden} makes the page non-self-contained"
    assert html.count("<style>") == 1


def test_workbook_round_trips_with_every_tab(run, pack, tmp_path):
    """The workbook is written by dpre.util.xlsx and reads back tab for tab."""
    result, _ = run
    paths = write_pack(result, tmp_path, pack=pack)
    workbook_path = next(p for p in paths if p.suffix == ".xlsx")
    tabs = workbook_tabs(workbook_path)
    assert list(tabs) == list(TAB_NAMES)
    assert len(tabs["Candidates"]) == len(result.candidates)
    first = tabs["Candidates"][0]
    for column in ("Rank", "Candidate id", "Composite", "Band", "Reports retirable"):
        assert column in first
    # the method appendix prints the knob set a reviewer would contest
    method = " ".join(str(row) for row in tabs["Method appendix"])
    assert result.manifest.weight_version in method
    assert "weights.dimensions.demand" in method
    assert "usage_window_months" in method


def test_workbook_scores_tab_pairs_every_feature_with_evidence(run, pack):
    """R-12 and the evidence guardrail: a score is shown with the rows behind it."""
    result, _ = run
    rows = pack.workbook["Scores & Evidence"]
    header = rows[0]
    assert "Evidence id" in header and "Feature label" in header
    candidate = result.ranked()[0]
    mine = [r for r in rows[1:] if r[0] == candidate.candidate_id]
    assert mine, "the top candidate has no rows on the scores tab"
    # Demand, consolidation and feasibility features are backed by graph rows; the
    # risk features are derived from the candidate itself, and the tab says so
    # rather than leaving the cell blank.
    # Evidence-required binds per feature, not per candidate (R-22): the risk
    # features cite the candidate's own rows - its sensitivity class, its grain
    # ambiguity, its open conflicts - rather than leaving a cell blank. A blank
    # would mean a weight moved a rank with nothing a reviewer could check.
    assert any(row[2] == labels.label("dimension", "risk") for row in mine), \
        "the top candidate has no risk features on the tab"
    for row in mine:
        assert row[10] and row[11], f"{row[3]} was written with no evidence"
        assert row[12] and row[12] != "no evidence row for this feature", row[3]


def test_workbook_tabs_without_input_say_so_rather_than_vanish(run):
    """A pack built with no store and no enrichment still has twelve usable tabs."""
    result, _ = run
    workbook = build_workbook(result, store=None, enrichment=None)
    assert list(workbook) == list(TAB_NAMES)
    for tab in ("Decisions", "Waves", "RAID", "Stakeholders", "Status"):
        assert len(workbook[tab]) >= 2, f"{tab} has no explanatory row"
        assert workbook[tab][1][0], f"{tab} does not say why it is empty"


def test_pack_reads_the_enrichment_defensively(run):
    """A missing, partial or malformed enrichment must never break the pack."""
    result, store = run
    bare = build_pack(result, store=store, engagement=ENGAGEMENT, enrichment=None)
    assert "value model" in " ".join(bare.omitted)
    assert bare.summary_document.section("value") is None
    assert bare.summary_document.section("waves") is None
    partial = build_pack(result, store=store, enrichment={"value": {"portfolio": {}}})
    assert partial.executive_summary_md
    for broken in (None, [], "nonsense", {"waves": "not a dict"}, {"raid": None}):
        view = EnrichmentView(broken)
        assert view.waves == [] and view.raid == [] and view.value_for("X") == {}


def test_dossier_reads_like_a_page_for_a_business_owner(run, enrichment):
    """R-12 and R-41: one page, plain words, no snake_case, and what happens next."""
    result, _ = run
    candidate = result.ranked()[0]
    document = candidate_dossier(candidate, result, engagement=ENGAGEMENT,
                                 enrichment=enrichment)
    text = render_markdown(document)
    assert candidate.proposed_name in text
    assert candidate.purpose in text
    assert "out of 100" in text                       # the score as a sentence with a band
    assert "lifted mainly by" in text
    assert "usage_weight" not in text and "consumer_breadth" not in text
    assert "Usage weight" in text                     # the label, not the key
    assert "What happens next" in text
    assert "only a named reviewer decides" in render_html(document)
    # the four gates, explained rather than named
    for gate in ("G1", "G2", "G3", "G4"):
        assert gate in text
    assert "At least one business unit" in text


def test_dossier_never_claims_a_keep_report_will_be_retired(run):
    """R-23: the Stage 12 action follows the report's own disposition."""
    result, _ = run
    for candidate in result.candidates:
        document = candidate_dossier(candidate, result)
        section = document.section("reports")
        if section is None:
            continue
        for block in section.blocks:
            if block.get("type") != "table":
                continue
            disposition = block["columns"].index("Disposition")
            action = block["columns"].index("Action")
            for row in block["rows"]:
                if row[disposition] == "Keep":
                    assert "retire on publication" not in row[action]
                    assert row[action] in (
                        "re-point source, report retained",
                        "hold: decision-critical or regulatory",
                        "partially covered - review before retirement")


def test_write_pack_writes_the_whole_folder(run, pack, tmp_path):
    result, _ = run
    paths = write_pack(result, tmp_path, pack=pack)
    names = {p.name for p in paths}
    assert "executive-summary.md" in names
    assert "executive-summary.html" in names
    assert "backlog-workbook.xlsx" in names
    assert "README.md" in names
    assert all(p.exists() and p.stat().st_size > 0 for p in paths)
    dossiers = [p for p in paths if p.parent.name == "dossiers"]
    assert len(dossiers) == 2 * len(result.candidates)
    index = (tmp_path / result.run_id / "README.md").read_text(encoding="utf-8")
    for candidate in result.candidates:
        assert candidate.candidate_id in index


def test_pack_is_reproducible(run, enrichment):
    """No wall clock, no randomness: the same run yields the same bytes."""
    result, store = run
    first = build_pack(result, store=store, engagement=ENGAGEMENT, enrichment=enrichment)
    second = build_pack(result, store=store, engagement=ENGAGEMENT, enrichment=enrichment)
    assert first.executive_summary_md == second.executive_summary_md
    assert first.executive_summary_html == second.executive_summary_html
    assert first.workbook == second.workbook
    assert [d.markdown for d in first.dossiers] == [d.markdown for d in second.dossiers]


def test_engagement_defaults_and_coercion():
    assert Engagement.coerce(None).client == "Client"
    assert Engagement.coerce({"client": "Acme", "unknown_field": 1}).client == "Acme"
    assert Engagement.coerce(Engagement(client="Acme")).title.startswith("Acme")
    with pytest.raises(TypeError):
        Engagement.coerce(42)


def test_summary_states_the_propose_only_guardrail(run, enrichment):
    """The pack must never imply the engine decided anything."""
    result, store = run
    text = render_markdown(executive_summary(result, store=store, enrichment=enrichment))
    assert "only a named reviewer" in text or "only a reviewer" in text
    assert "The engine proposes" in text
    for candidate in result.candidates:
        assert candidate.status in ("Blocked", "Exploratory", "Proposed")


# --------------------------------------------------------------------------
# R-37: communications
# --------------------------------------------------------------------------

def test_communications_are_written_per_recipient(run, tmp_path):
    result, _ = run
    notes = build_communications(result)
    assert notes["owners"] and notes["stewards"] and notes["consumers"]
    paths = write_communications(result, tmp_path / "communications")
    names = {p.name for p in paths}
    assert "README.md" in names
    for owner in notes["owners"]:
        assert f"owner-notification-{slug(owner)}.md" in names
    for steward in notes["stewards"]:
        assert f"steward-adjudication-{slug(steward)}.md" in names
    for unit in notes["consumers"]:
        assert f"consumer-confirmation-{slug(unit)}.md" in names
    assert all(p.read_text(encoding="utf-8").strip() for p in paths)


def test_owner_notification_carries_the_contest_path_and_the_right_action(run):
    result, _ = run
    notes = build_communications(result)["owners"]
    owner, text = sorted(notes.items())[0]
    assert owner in text
    assert "Contest" in text and "decision-critical" in text
    assert "Replacing candidate" in text and "Users" in text
    assert "Coverage" in text
    # a Keep report is never told it will be retired on publication
    for line in text.splitlines():
        if line.startswith("|") and "| Keep |" in line:
            assert "retire on publication" not in line


def test_steward_adjudication_ranks_by_usage_and_shows_both_expressions(run):
    result, _ = run
    notes = build_communications(result)["stewards"]
    assert notes, "no steward received an adjudication request"
    text = max(notes.values(), key=len)
    assert "Usage at stake" in text
    assert "**A." in text and "**B." in text
    assert "Stage 6 decision" in text
    assert "awaiting your acceptance" in text
    # ranked descending by usage at stake
    stakes = [float(line.split("**")[1].replace(",", ""))
              for line in text.splitlines() if line.startswith("- Usage at stake:")]
    assert stakes == sorted(stakes, reverse=True)


def test_consumer_confirmation_leaves_the_three_questions_to_the_consumer(run):
    result, _ = run
    notes = build_communications(result)["consumers"]
    text = max(notes.values(), key=len)
    assert "blocked_decision" in text
    assert "latency_tolerance" in text
    assert "consequence_of_not_deciding" in text
    assert "AI_DRAFT" in text
    assert "Persona" in text


def test_write_run_seeds_puts_communications_beside_the_candidate_seeds(run, tmp_path):
    result, _ = run
    paths = write_run_seeds(result, tmp_path)
    communications = [p for p in paths if p.parent.name == "communications"]
    assert communications
    assert all(p.parent.parent.name == result.run_id for p in communications)
    assert any(p.name.startswith("owner-notification-") for p in communications)
    assert any(p.name.startswith("steward-adjudication-") for p in communications)
    assert any(p.name.startswith("consumer-confirmation-") for p in communications)
    assert any(p.name == "stage2-charter.yaml" for p in paths)


# --------------------------------------------------------------------------
# R-36 and R-23: seed provenance and disposition-aware retirement
# --------------------------------------------------------------------------

def test_every_seed_carries_synthetic_and_generation_id(run):
    result, _ = run
    candidate = result.ranked()[0]
    seeds = build_seeds(candidate, result.canonical, result.graph)
    provenance = seeds["provenance"]
    assert provenance["synthetic"] is True
    assert provenance["generation_id"]
    assert provenance["run_id"] == result.run_id
    for key in ("decision_register", "charter", "source_inventory", "semantic_model"):
        body = seeds[key]
        assert body["provenance"]["synthetic"] is True
        assert body["provenance"]["generation_id"] == provenance["generation_id"]


def test_catalog_payload_blocks_import_of_a_synthetic_run(run):
    result, _ = run
    payload = build_seeds(result.ranked()[0], result.canonical, result.graph)["catalog_payload"]
    assert payload["synthetic"] is True
    assert payload["generation_id"]
    assert SYNTHETIC_IMPORT_BLOCK in payload["import_blocked_by"]


def test_seed_files_show_the_banner_in_their_header(run, tmp_path):
    result, _ = run
    paths = write_run_seeds(result, tmp_path)
    charter = next(p for p in paths if p.name == "stage2-charter.yaml")
    head = charter.read_text(encoding="utf-8").splitlines()[0]
    assert "SYNTHETIC" in head.upper()
    payload = next(p for p in paths if p.name.endswith("-payload.json"))
    assert SYNTHETIC_IMPORT_BLOCK in json.loads(payload.read_text())["import_blocked_by"]


def test_stage12_action_follows_the_disposition(run, tmp_path):
    """R-23: Retire retires, Merge merges, Migrate rebuilds, Keep is re-pointed."""
    result, _ = run
    paths = write_run_seeds(result, tmp_path)
    expected = {
        "Retire": "retire on publication",
        "Merge": "merge and retire",
        "Migrate": "rebuild on the product",
        "Keep": "re-point source, report retained",
    }
    seen: set[str] = set()
    for path in (p for p in paths if p.name == "stage12-retirement-list.csv"):
        for row in csv.DictReader(path.open(encoding="utf-8")):
            disposition = row["disposition"] or "Keep"
            if float(row["coverage"]) >= 1.0:
                seen.add(disposition)
            if float(row["coverage"]) < 1.0:
                assert row["action"] == "partially covered - review before retirement"
                assert row["retirable"] == "N"
            elif row["hold_reason"]:
                assert row["action"].startswith("hold")
                assert row["retirable"] == "N"
            else:
                assert row["action"] == expected[disposition]
                assert (row["retirable"] == "Y") == (disposition != "Keep")
            assert row["synthetic"] == "TRUE"
            assert row["generation_id"]
    assert {"Keep", "Retire"} <= seen, "the estate did not exercise both dispositions"


def test_a_decision_critical_report_is_held_whatever_its_disposition(run):
    result, _ = run
    report = next((r for c in result.candidates for r in c.reports if r.coverage > 0), None)
    assert report is not None
    record = result.graph.reports[report.report_id]
    was = record.decision_critical
    try:
        record.decision_critical = True
        hold = is_hold(record, report.report_name)
        assert hold is True
        assert retirement_action(report, hold).startswith("hold")
        assert is_retirable(report, hold) is False
    finally:
        record.decision_critical = was


# --------------------------------------------------------------------------
# R-58: narration that does not read as machine output
# --------------------------------------------------------------------------

def test_questions_are_de_duplicated(run):
    result, _ = run
    for candidate in result.candidates:
        for draft in candidate.decisions_drafted:
            assert len(draft.questions) == len(set(draft.questions))
    assert unique_titles(["Fraud Loss Dashboard", "fraud loss dashboard!", "Other"]) == \
        ["Fraud Loss Dashboard", "Other"]


def test_questions_are_phrased_from_kpi_labels(run):
    """'How many active cards did we have last month?', not 'Active Cards?'."""
    result, _ = run
    asked = [q for c in result.candidates for d in c.decisions_drafted for q in d.questions]
    assert asked
    assert all(q.endswith("?") for q in asked)
    assert any(q.startswith("How many ") for q in asked)
    assert any(q.startswith("What was our total ") or q.startswith("What was our ") for q in asked)

    class _Metric:
        labels = ["Active Cards"]
        canonical_name = "active_cards"
        aggregation = "COUNT"
        usage_weight = 10.0

    assert question_for_metric(_Metric(), "monthly") == \
        "How many active cards did we have last month?"
    assert plain_label("Days Past DUE") == "days past DUE".replace("DUE", "DUE")


def test_persona_is_left_blank_when_no_keyword_matched(run):
    assert persona_for("Collections") == ("Collections manager (Collections)", 0.6)
    assert persona_for("Credit") == ("Credit manager (Credit)", 0.75)
    assert persona_for("Widget Division Alpha") == ("", 0.0)
    assert persona_for("") == ("", 0.0)
    result, _ = run
    for candidate in result.candidates:
        for draft in candidate.decisions_drafted:
            confidence = getattr(draft, "persona_confidence", None)
            assert confidence is not None
            assert (confidence > 0) == bool(draft.persona)
            assert "Analyst (Unassigned)" != draft.persona


def test_inferred_decision_varies_or_says_unclear(run):
    result, _ = run
    inferred = [d.inferred_decision for c in result.candidates for d in c.decisions_drafted]
    assert inferred
    assert len(set(inferred)) > 1, "every business unit got the same templated decision"
    assert all(i.endswith(".") for i in inferred)
    assert all("A reviewer must confirm" in i or i.startswith("unclear") for i in inferred)


def test_draft_questions_prefers_metrics_then_titles():
    class _Metric:
        def __init__(self, label, aggregation, weight):
            self.labels = [label]
            self.canonical_name = label.lower().replace(" ", "_")
            self.aggregation = aggregation
            self.usage_weight = weight

    questions = draft_questions(["Fraud Loss Dashboard", "Fraud Loss Dashboard"],
                                [_Metric("Active Cards", "COUNT", 9.0),
                                 _Metric("Interchange Revenue", "SUM", 5.0)], "monthly")
    assert questions[0] == "How many active cards did we have last month?"
    assert questions[1] == "What was our total interchange revenue last month?"
    assert len(questions) == len(set(questions))
    assert sum(1 for q in questions if "Fraud Loss" in q) == 1


# --------------------------------------------------------------------------
# R-42: AI provenance
# --------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_ai_seam():
    """Each AI test starts with no provider and an empty ledger."""
    ai.reset()
    yield
    ai.reset()


def test_ledger_records_template_calls():
    """With no provider configured every draft is a template call, and it is logged."""
    first = ai.complete("system", "prompt one", "fallback one", purpose="purpose",
                        candidate_id="CAND-1")
    ai.complete("system", "prompt two", "fallback two", purpose="inferred_decision",
                candidate_id="CAND-2", redactions=3)
    assert first.text == "fallback one"
    assert first.source == ai.TEMPLATE
    assert first.status == "AI_DRAFT"
    rows = ai.drain_ledger()
    assert len(rows) == 2
    assert [r.purpose for r in rows] == ["purpose", "inferred_decision"]
    assert all(r.model == ai.TEMPLATE and r.source == ai.TEMPLATE for r in rows)
    assert all(len(r.prompt_sha256) == 64 and len(r.response_sha256) == 64 for r in rows)
    assert rows[0].prompt_sha256 != rows[1].prompt_sha256
    assert all(r.fallback_reason == "no provider configured" for r in rows)
    assert rows[1].redactions == 3
    assert rows[1].candidate_id == "CAND-2"
    assert ai.drain_ledger() == [], "draining twice must not return the same rows"


def test_ledger_records_a_provider_failure_as_a_fallback_reason():
    def _broken(system: str, user: str) -> str:
        raise RuntimeError("provider unreachable")

    ai.register_provider(_broken, model="test-model-1")
    result = ai.complete("system", "prompt", "fallback", purpose="purpose")
    assert result.text == "fallback"
    assert result.source == ai.TEMPLATE
    summary = ai.provenance_summary()
    assert summary["ai_enabled"] is True
    assert summary["model"] == "test-model-1"
    assert summary["model_calls"] == 0
    assert summary["fallbacks"], "a provider failure must be visible"
    assert "provider unreachable" in summary["fallbacks"][0]["reason"]
    assert summary["warnings"], "a silent provider failure is the finding R-42 raised"


def test_a_working_provider_is_recorded_as_a_model_call():
    ai.register_provider(lambda system, user: "  a drafted name  ", model="test-model-2")
    result = ai.complete("system", "prompt", "fallback", purpose="purpose",
                         candidate_id="CAND-9")
    assert result.text == "a drafted name"
    assert result.source == "model" and result.model == "test-model-2"
    assert result.provenance["prompt_version"] == ai.PROMPT_VERSION
    assert result.provenance["fallback_reason"] == ""
    summary = ai.provenance_summary()
    assert summary["model_calls"] == 1
    assert summary["by_purpose"]["purpose"]["model"] == 1
    assert "prompts were sent to" in summary["egress"]


def test_prompts_carry_only_allow_listed_fields():
    prompt, redactions = ai.build_prompt(
        "Name this.",
        {"proposed_name": "Card Spend", "customer_email": "a@b.example",
         "consumers": [{"business_unit": "Retail", "secret_note": "do not send"}]},
        pii_names=())
    assert "Card Spend" in prompt
    assert "customer_email" not in prompt and "a@b.example" not in prompt
    assert "secret_note" not in prompt and "do not send" not in prompt
    assert "Retail" in prompt
    assert redactions == 0


def test_pii_column_names_are_redacted_before_prompting():
    prompt, redactions = ai.build_prompt(
        "Name this.", {"metrics": ["count of customer_ssn", "CRM.DB.SCH.CUST.customer_ssn"]},
        pii_names={"CRM.DB.SCH.CUST.customer_ssn"})
    assert "customer_ssn" not in prompt
    assert ai.PII_PLACEHOLDER in prompt
    assert redactions >= 2
    text, count = ai.redact("email is customer_email", ["customer_email"])
    assert count == 1 and "customer_email" not in text


def test_narration_leaves_a_provenance_trail_on_every_candidate(run):
    result, _ = run
    for candidate in result.candidates:
        provenance = candidate.narrative.get("provenance") or {}
        purpose = provenance.get("purpose") or {}
        assert purpose.get("prompt_version") == ai.PROMPT_VERSION
        assert len(purpose.get("prompt_sha256", "")) == 64
        assert candidate.narrative["purpose_status"] == "AI_DRAFT"
        assert candidate.name_status == "AI_DRAFT"


def test_ledger_persists_to_its_own_table(run):
    result, store = run
    connection = store.connection
    ai.ensure_schema(connection)
    ai.ensure_schema(connection)                      # idempotent
    ai.complete("system", "prompt", "fallback", purpose="purpose", candidate_id="CAND-1")
    rows = ai.drain_ledger()
    assert ai.save_ledger(connection, result.run_id, rows) == 1
    assert ai.save_ledger(connection, result.run_id, rows) == 1   # replaces, never duplicates
    loaded = ai.load_ledger(connection, result.run_id)
    assert len(loaded) == 1
    assert loaded[0]["purpose"] == "purpose"
    assert loaded[0]["model"] == ai.TEMPLATE
    assert loaded[0]["prompt_sha256"] == rows[0].prompt_sha256


# --------------------------------------------------------------------------
# R-41: one display dictionary, covering every code the engine emits
# --------------------------------------------------------------------------

def _assert_covered(category: str, keys) -> None:
    missing = sorted(k for k in keys if k not in labels.LABELS.get(category, {}))
    assert not missing, f"{category} has no label for: {missing}"


def test_labels_cover_every_gate_and_quality_gate(run):
    result, _ = run
    _assert_covered("gate", {g.gate for c in result.candidates
                             for g in (c.score.gates if c.score else [])})
    _assert_covered("gate", {"G1", "G2", "G3", "G4"})
    _assert_covered("quality_gate", {g["gate"] for g in result.manifest.quality_gates})


def test_labels_cover_every_score_dimension_and_feature(run):
    result, _ = run
    _assert_covered("dimension", DIMENSION_WEIGHTS)
    for dimension, features in FEATURE_WEIGHTS.items():
        _assert_covered("feature", features)
    _assert_covered("feature", {f.feature for c in result.candidates
                                for f in (c.score.features if c.score else [])})


def test_labels_cover_every_status_origin_archetype_and_tier(run):
    result, _ = run
    _assert_covered("status", STATUS_ORDER)
    _assert_covered("origin", {c.origin for c in result.candidates})
    _assert_covered("archetype", {c.archetype for c in result.candidates})
    _assert_covered("archetype", {c.archetype_runner_up for c in result.candidates
                                  if c.archetype_runner_up})
    _assert_covered("tier", {c.tier for c in result.candidates})
    _assert_covered("name_status", {c.name_status for c in result.candidates})


def test_labels_cover_every_conflict_pattern_and_quarantine_code(run):
    result, _ = run
    _assert_covered("conflict_pattern", PATTERN_DECISIONS)
    _assert_covered("conflict_pattern", {c.pattern for c in result.canonical.conflicts
                                         if c.pattern})
    _assert_covered("quarantine_reason", QUARANTINE_REASONS)
    _assert_covered("quarantine_reason", {q.reason_code for q in result.graph.quarantine})
    _assert_covered("steward_source", {m.steward_source
                                       for m in result.canonical.metrics.values()})


def test_labels_cover_every_critique_criterion_and_severity(run):
    result, _ = run
    _assert_covered("critique_criterion", [key for key, _ in STAGE_CRITERIA])
    _assert_covered("critique_criterion", [STANDING_CRITERION])
    _assert_covered("critique_criterion", {f.criterion for c in result.candidates
                                           for f in c.critique})
    _assert_covered("severity", {f.severity for c in result.candidates for f in c.critique})
    _assert_covered("severity", {"high", "medium", "low"})       # the RAID scale


def test_labels_cover_the_reviewer_decision_vocabulary():
    reasons = pytest.importorskip("dpre.governance.reasons")
    _assert_covered("decision", reasons.DECISIONS)
    codes = {code for group in reasons.REASON_CODES.values() for code in group}
    _assert_covered("reason_code", codes)


def test_labels_cover_every_workbook_tab():
    _assert_covered("workbook_tab", TAB_NAMES)


def test_every_label_entry_is_well_formed():
    for category, entries in labels.LABELS.items():
        assert entries, f"{category} is empty"
        for key, entry in entries.items():
            assert set(entry) == {"label", "explanation"}, f"{category}.{key}"
            assert entry["label"] and not entry["label"].endswith("."), f"{category}.{key}"
            assert entry["explanation"].endswith("."), f"{category}.{key}"
            assert "_" not in entry["label"], f"{category}.{key} label is still a code"


def test_labels_for_api_is_json_serialisable_and_complete():
    payload = labels.labels_for_api()
    assert json.loads(json.dumps(payload)) == payload
    assert set(payload["categories"]) == set(labels.LABELS)
    assert payload["composite_bands"][0]["band"] == "Strong"
    assert "archetype_confidence" in payload["notes"]
    rows = labels.glossary_rows()
    assert len(rows) == sum(len(v) for v in labels.LABELS.values())
    assert all({"category", "key", "label", "explanation"} == set(r) for r in rows)


def test_unknown_codes_fall_back_to_the_code_itself():
    assert labels.label("gate", "G9") == "G9"
    assert labels.explanation("gate", "G9") == ""
    assert labels.describe("status", "Proposed")["label"] == "Proposed"
    assert labels.describe("nope", "nope") == {"key": "nope", "label": "nope", "explanation": ""}


def test_composite_bands_and_margin_note_speak_plainly():
    assert labels.composite_band(90.0)["band"] == "Strong"
    assert labels.composite_band(61.0)["band"] == "Good"
    assert labels.composite_band(50.0)["band"] == "Moderate"
    assert labels.composite_band(0.0)["band"] == "Weak"
    assert "classification margin" in labels.classification_margin_note(0.28)
    assert "confidence" not in labels.classification_margin_note(0.28).split("margin")[0]
