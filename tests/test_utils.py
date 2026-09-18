"""Utilities: text similarity, the xlsx round trip, tabular coercion, YAML."""
from __future__ import annotations

import datetime as _dt

import pytest

from dpre.util import tabular
from dpre.util.text import (
    embedding_similarity, jaro_winkler, name_similarity, snake_case, token_key,
)
from dpre.util.xlsx import read_sheet_records, read_workbook, write_workbook
from dpre.util.yamlio import dumps as yaml_dumps


def test_abbreviation_expansion_matches_the_specification_example():
    # ER-4's own example: cust_acct_no ~ customer_account_number
    assert name_similarity("cust_acct_no", "customer_account_number") >= 0.92


def test_similarity_separates_unrelated_names():
    assert name_similarity("arrears_balance", "meter_read_exception") < 0.6


def test_jaro_winkler_bounds():
    assert jaro_winkler("abc", "abc") == 1.0
    assert 0.0 <= jaro_winkler("abc", "xyz") < 0.5


def test_embedding_similarity_is_symmetric_and_bounded():
    a, b = "Days Sales Outstanding", "DSO days sales outstanding"
    assert 0.0 <= embedding_similarity(a, b) <= 1.0
    assert embedding_similarity(a, b) == embedding_similarity(b, a)
    assert embedding_similarity(a, b) > embedding_similarity(a, "meter read exception")


def test_token_key_is_order_insensitive():
    assert token_key("Arrears Balance") == token_key("balance arrears")


def test_snake_case():
    assert snake_case("Arrears Balance 60+") == "arrears_balance_60"


def test_xlsx_round_trip(tmp_path):
    path = tmp_path / "book.xlsx"
    sheets = {
        "Data": [["id", "name", "amount", "flag"],
                 [1, "hé & <b>", 2.5, True],
                 [2, "", None, False]],
        "Other": [["k"], ["v"]],
    }
    write_workbook(path, sheets)
    back = read_workbook(path)
    assert list(back) == ["Data", "Other"]
    assert back["Data"][1][1] == "hé & <b>"
    assert back["Data"][1][2] == 2.5
    records = read_sheet_records(path, "Data")
    assert records[0]["id"] == 1 and records[1]["name"] in ("", None)


def test_tabular_coercion():
    assert tabular.as_int("1,234") == 1234
    assert tabular.as_float("12.5%") == 12.5
    assert tabular.as_bool("Y") is True and tabular.as_bool("N") is False
    assert tabular.as_date("2026-09-17") == _dt.date(2026, 9, 17)
    assert tabular.as_date("17/09/2026") == _dt.date(2026, 9, 17)
    assert tabular.as_date("not a date", None) is None


def test_tabular_pick_is_case_and_underscore_insensitive():
    record = {"Run Count 12m": 5}
    assert tabular.pick(record, "run_count_12m") == 5


def test_yaml_emitter_round_trips_through_pyyaml():
    yaml = pytest.importorskip("yaml")
    payload = {"a": 1, "b": ["x", {"c": "hello: world", "d": None}],
               "e": {"f": True}, "g": "multi\nline", "h": []}
    assert yaml.safe_load(yaml_dumps(payload)) == payload
