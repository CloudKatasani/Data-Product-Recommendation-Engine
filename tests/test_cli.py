"""The command line, exercised the way a batch schedule and a reviewer use it.

Everything the browser can do has to be reachable here, because a review board
runs monthly and a pipeline runs nightly. These tests drive ``dpre.cli.main``
directly, with stdout captured, so a broken column name or a renamed table shows
up as a failing assertion rather than a traceback in someone's terminal.
"""
from __future__ import annotations

import datetime as _dt

import pytest

from dpre.cli import main
from dpre.pipeline import run_pipeline
from dpre.store import Store

AS_OF = _dt.date(2026, 9, 17)


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    """One retail run on disk, shared by every test in this file."""
    from dpre.ingest import ingest_automated
    path = tmp_path_factory.mktemp("cli") / "engine.db"
    store = Store(path)
    run_pipeline(ingest_automated("retail", as_of=AS_OF), store=store, label="cli")
    store.close()
    return str(path)


def run_cli(db: str, *argv: str) -> int:
    return main(["--db", db, *argv])


def test_status_prints_the_success_measures(db, capsys):
    assert run_cli(db, "status") == 0
    out = capsys.readouterr().out
    assert "Success measures" in out
    # Every 14.2 measure carries a RAG, including the ones that cannot be
    # computed from the engine alone: silence would read as a pass.
    assert "Usage-weighted KPI consumption" in out
    assert "Conflicts adjudicated" in out
    assert "Queues" in out


def test_waves_name_what_they_ship(db, capsys):
    assert run_cli(db, "waves") == 0
    out = capsys.readouterr().out
    assert "Wave 1" in out
    # A wave plan that prints candidate ids is unreadable in a steering meeting.
    assert "CAND-" not in out.split("Unscheduled")[0]


def test_value_separates_gross_claims_from_attributed_benefit(db, capsys):
    assert run_cli(db, "value") == 0
    out = capsys.readouterr().out
    assert "assumptions" in out
    gross = float(out.split("gross claims ")[1].split(";")[0].replace(",", ""))
    attributed = float(out.split("after attribution ")[1].split(".")[0].replace(",", ""))
    # A report retired once is a saving once: double counting is the single
    # easiest way to lose a business case in the room.
    assert attributed < gross


def test_raid_filters_and_exports(db, capsys, tmp_path):
    assert run_cli(db, "raid", "--severity", "high", "--limit", "5") == 0
    out = capsys.readouterr().out
    assert "RAID log" in out and "low" not in out.split("title")[1]

    target = tmp_path / "raid.csv"
    assert run_cli(db, "raid", "--csv", str(target)) == 0
    header, first = target.read_text(encoding="utf-8").splitlines()[:2]
    assert header.startswith("raid_id,type,title,severity")
    # Id lists belong in one cell, joined, not as a Python repr.
    assert "[" not in first and "]" not in first


def test_audit_reports_the_chain_and_the_unapproved_weights(db, capsys):
    # A fresh estate has no council approval on file, so the audit is not clean
    # and the exit code says so.
    assert run_cli(db, "audit") == 1
    out = capsys.readouterr().out
    assert "hash chain          intact" in out
    assert "pending council approval" in out
    assert run_cli(db, "audit", "--report-only") == 0


def test_weights_list_then_approve(db, capsys):
    assert run_cli(db, "weights") == 0
    assert "(pending)" in capsys.readouterr().out
    assert run_cli(db, "weights", "--approve", "v1.0-initial",
                   "--approver", "council.chair") == 0
    assert run_cli(db, "weights") == 0
    assert "council.chair" in capsys.readouterr().out


def test_reasons_prints_the_closed_vocabulary(capsys):
    assert main(["reasons"]) == 0
    out = capsys.readouterr().out
    assert "AcceptWithException" in out and "gate_waived" in out
    assert "no_named_consumer" in out


def _first(db: str, status: str) -> str:
    store = Store(db)
    rows = store.candidates(store.latest_run_id(), status)
    store.close()
    return rows[0]["candidate_id"] if rows else ""


def test_a_gated_candidate_is_refused_in_words_not_a_traceback(db, capsys):
    candidate_id = _first(db, "Blocked")
    assert candidate_id, "the retail pack should block at least one candidate"
    assert run_cli(db, "review", candidate_id, "Accept",
                   "--reviewer", "priya.silva", "--reason", "value_clear") == 1
    assert "refused:" in capsys.readouterr().err


def test_confirming_a_consumer_is_recorded_against_the_lineage(db, capsys):
    candidate_id = _first(db, "Exploratory")
    assert candidate_id
    assert run_cli(db, "confirm", candidate_id,
                   "--business-unit", "Ecommerce",
                   "--blocked-decision", "weekly promotion stop/go",
                   "--latency", "daily by 07:00",
                   "--consequence", "the promotion runs on last week's numbers",
                   "--confirmed-by", "dana.okafor") == 0
    assert "consumer confirmed" in capsys.readouterr().out
    store = Store(db)
    confirmations = store.consumer_confirmations()
    store.close()
    # The confirmation is keyed by lineage, not candidate id, so it survives
    # into the next run even when the clusterer renumbers the candidate.
    assert confirmations and confirmations[0]["lineage_id"].startswith("LIN-")
    assert confirmations[0]["confirmed_by"] == "dana.okafor"


def test_an_accepted_candidate_gets_a_benefit_plan_to_be_held_to(db, capsys):
    candidate_id = _first(db, "Proposed")
    assert candidate_id
    assert run_cli(db, "review", candidate_id, "Accept",
                   "--reviewer", "priya.silva", "--reason", "value_clear") == 0
    capsys.readouterr()
    assert run_cli(db, "benefits") == 0
    out = capsys.readouterr().out
    assert "Benefit realisation" in out
    assert "/" in out          # realised over planned, both present


def test_run_writes_an_executive_pack_a_partner_could_table(tmp_path, capsys):
    """The pack is cut in the same process as the run, so the cover and the
    store cannot disagree about what the engine found."""
    out = tmp_path / "packs"
    assert main(["--db", str(tmp_path / "pack.db"), "run", "automated",
                 "--industry", "retail", "--as-of", "2026-09-17",
                 "--workspace", str(tmp_path / "ws"),
                 "--pack", str(out), "--client", "Northwind Retail",
                 "--partner", "A. Partner"]) == 0
    assert "executive pack written to" in capsys.readouterr().out
    folder = next(out.iterdir())
    names = {p.name for p in folder.iterdir()}
    assert {"executive-summary.md", "executive-summary.html",
            "backlog-workbook.xlsx", "dossiers", "README.md"} <= names
    summary = (folder / "executive-summary.md").read_text(encoding="utf-8")
    assert summary.startswith("# Northwind Retail")
    # A synthetic run must never read as a client finding, on any page.
    assert "synthetic" in summary.lower()
    assert (folder / "backlog-workbook.xlsx").read_bytes()[:4] == b"PK\x03\x04"


def test_assess_reports_what_the_engine_was_fed_and_what_it_missed(db, capsys):
    assert run_cli(db, "assess", "--limit", "5") == 0
    out = capsys.readouterr().out
    assert "Detection against what was planted" in out
    assert "Remediation plan" in out
    # The units are named, not identified: a metric id is not something anyone
    # can look up in a review meeting.
    assert "how it is recognised" in out


def test_assess_exports_the_remediation_plan(db, tmp_path, capsys):
    target = tmp_path / "plan.csv"
    assert run_cli(db, "assess", "--csv", str(target)) == 0
    assert "remediation units written to" in capsys.readouterr().out
    header = target.read_text(encoding="utf-8").splitlines()[0]
    assert "owner_role" in header and "action" in header


def test_the_registers_name_the_code_that_implements_them(capsys):
    assert main(["registers", "assumptions", "--limit", "3"]) == 0
    out = capsys.readouterr().out
    assert "dpre/config.py" in out and "decision" in out

    assert main(["registers", "traceability", "--limit", "3"]) == 0
    out = capsys.readouterr().out
    assert "falsifier" in out.lower()


def test_every_control_in_the_matrix_is_present_in_the_code(capsys):
    """A controls matrix that names a control the code does not have is worse
    than no matrix: it is an assurance claim nobody can support."""
    assert main(["registers", "controls", "--limit", "50"]) == 0
    out = capsys.readouterr().out
    assert " NO " not in out, out
    assert "controls verified against the code" in out


def test_the_open_decisions_say_what_the_engine_assumes_meanwhile(capsys):
    assert main(["--db", ":memory:", "registers", "decisions"]) == 0
    out = capsys.readouterr().out
    for ref in ("D-01", "D-04", "D-08"):
        assert ref in out
    # An open decision is not a blank: the engine has taken a position and says
    # which one, so a client can disagree with something specific.
    assert "the engine currently assumes" in out


def test_an_engagement_owns_its_runs(db, tmp_path, capsys):
    path = str(tmp_path / "eng.db")
    assert run_cli(path, "engagement", "create", "--client", "Acme Utilities",
                   "--code", "ACM-2026-07", "--cut-date", "2026-09-17",
                   "--domain", "Customer", "--domain", "Finance",
                   "--reviewer", "priya.silva:reviewer:Finance", "--actor", "setup") == 0
    created = capsys.readouterr().out
    assert "created for Acme Utilities" in created
    engagement_id = created.split()[1]

    assert run_cli(path, "engagement", "list") == 0
    listing = capsys.readouterr().out
    assert "Acme Utilities" in listing and "active" in listing
    # A run that belongs to nobody is named, because a run nobody owns cannot
    # be governed.
    assert "belong to no engagement" not in listing

    assert run_cli(path, "engagement", "close", "--engagement", engagement_id) == 0
    assert "closed" in capsys.readouterr().out


def test_scope_drift_is_reported_against_the_statement_of_work(tmp_path, capsys):
    path = str(tmp_path / "scope.db")
    assert run_cli(path, "engagement", "create", "--client", "Acme Utilities",
                   "--cut-date", "2026-09-17", "--domain", "Customer",
                   "--domain", "Finance") == 0
    engagement_id = capsys.readouterr().out.split()[1]
    run_cli(path, "scope", "--engagement", engagement_id,
            "--industry", "utility", "--as-of", "2026-09-17")
    out = capsys.readouterr().out
    # The catalog export reaches past what the client agreed. That is normal and
    # it belongs on the run summary rather than in a committee three weeks on.
    assert "DOMAINS_OUT_OF_SCOPE" in out
    assert "Metering" in out
