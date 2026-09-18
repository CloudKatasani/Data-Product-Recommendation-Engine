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
