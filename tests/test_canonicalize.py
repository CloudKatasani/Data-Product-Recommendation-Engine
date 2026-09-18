"""Parsing, fingerprinting and the match tiers of specification section 5."""
from __future__ import annotations

from dpre.canonicalize.conflicts import classify_pattern
from dpre.canonicalize.expr import parse_expression
from dpre.canonicalize.fingerprint import fingerprint_kpi
from dpre.models import CanonicalMetric, KpiNode


def kpi(expression, *, tool="cognos", operands=(), filters=(), label="metric",
        aggregation="", filter_expression="", time_modifier=""):
    parsed = parse_expression(expression, "dax" if tool == "powerbi" else "cognos",
                              aggregation_hint=aggregation)
    return KpiNode(
        kpi_id="K", report_id="R", label=label, tool=tool,
        aggregation=parsed.aggregation or aggregation, expression=expression,
        expression_ast=parsed.ast, fingerprint="", filter_fp="",
        parse_status=parsed.status, filter_expression=filter_expression,
        operand_columns=list(operands), filter_columns=list(filters),
        time_modifier=time_modifier or parsed.time_modifier)


ACCOUNT = "CIS.CISDB.CIS_CORE.FCT_ACCOUNT_BALANCE.arrears_amount"
REVENUE = "ERP.ERPDB.GL.FCT_REVENUE_LEDGER.billed_revenue"
TOTAL = "ERP.ERPDB.GL.FCT_REVENUE_LEDGER.total_revenue"
KEY = "CIS.CISDB.CIS_CORE.DIM_ACCOUNT.account_key"
DAYS = "CIS.CISDB.CIS_CORE.FCT_ACCOUNT_BALANCE.days_past_due"


def test_cognos_and_dax_agree_on_the_same_calculation():
    """Section 16.2: fingerprinting rules are unchanged across tools."""
    cognos = kpi("total([CIS_CORE].[FCT_ACCOUNT_BALANCE].[arrears_amount])", operands=[ACCOUNT])
    dax = kpi("SUM('FCT_ACCOUNT_BALANCE'[arrears_amount])", tool="powerbi", operands=[ACCOUNT])
    assert fingerprint_kpi(cognos)[0] == fingerprint_kpi(dax)[0]


def test_calculate_wrapper_does_not_change_the_fingerprint():
    plain = kpi("SUM('F'[arrears_amount])", tool="powerbi", operands=[ACCOUNT])
    wrapped = kpi("CALCULATE(SUM('F'[arrears_amount]))", tool="powerbi", operands=[ACCOUNT])
    assert fingerprint_kpi(plain)[0] == fingerprint_kpi(wrapped)[0]


def test_formatting_functions_are_stripped():
    plain = kpi("total([S].[T].[arrears_amount])", operands=[ACCOUNT])
    rounded = kpi("round(total([S].[T].[arrears_amount]), 2)", operands=[ACCOUNT])
    assert fingerprint_kpi(plain)[0] == fingerprint_kpi(rounded)[0]


def test_distinct_count_spellings_agree():
    cognos = kpi("count(distinct [S].[DIM_ACCOUNT].[account_key])", operands=[KEY])
    dax = kpi("DISTINCTCOUNT('DIM_ACCOUNT'[account_key])", tool="powerbi", operands=[KEY])
    assert fingerprint_kpi(cognos)[0] == fingerprint_kpi(dax)[0]
    assert cognos.aggregation == "COUNT DISTINCT"


def test_threshold_drift_produces_different_fingerprints():
    """A hard-coded threshold is part of the calculation, so > 60 and > 59 differ."""
    over_60 = kpi("count(distinct case when [S].[T].[days_past_due] > 60 then "
                  "[S].[D].[account_key] end)", operands=[DAYS, KEY])
    over_59 = kpi("count(distinct case when [S].[T].[days_past_due] > 59 then "
                  "[S].[D].[account_key] end)", operands=[DAYS, KEY])
    at_least_61 = kpi("count(distinct case when [S].[T].[days_past_due] >= 61 then "
                      "[S].[D].[account_key] end)", operands=[DAYS, KEY])
    prints = {fingerprint_kpi(k)[0] for k in (over_60, over_59, at_least_61)}
    assert len(prints) == 3


def test_exclusion_drift_is_a_variant_not_a_conflict():
    """Same fingerprint, different filter_fp: one metric, each filter a variant."""
    plain = kpi("total([S].[T].[arrears_amount])", operands=[ACCOUNT])
    excluded = kpi("total([S].[T].[arrears_amount])", operands=[ACCOUNT],
                   filter_expression="[S].[D].[budget_billing_flag] <> 'Y'")
    assert fingerprint_kpi(plain)[0] == fingerprint_kpi(excluded)[0]
    assert fingerprint_kpi(plain)[1] != fingerprint_kpi(excluded)[1]


def test_denominator_swap_changes_the_fingerprint():
    billed = kpi("total([S].[T].[arrears_amount]) / total([S].[G].[billed_revenue])",
                 operands=[ACCOUNT, REVENUE])
    total = kpi("total([S].[T].[arrears_amount]) / total([S].[G].[total_revenue])",
                operands=[ACCOUNT, TOTAL])
    assert fingerprint_kpi(billed)[0] != fingerprint_kpi(total)[0]


def test_commutative_operands_are_ordered():
    left = kpi("total([S].[T].[a]) + total([S].[T].[b])", operands=[ACCOUNT, REVENUE])
    right = kpi("total([S].[T].[b]) + total([S].[T].[a])", operands=[ACCOUNT, REVENUE])
    assert fingerprint_kpi(left)[0] == fingerprint_kpi(right)[0]


def test_time_intelligence_becomes_a_modifier_not_a_new_metric():
    """Year-over-year variants group with their base measure (section 16.2)."""
    base = kpi("SUM('F'[arrears_amount])", tool="powerbi", operands=[ACCOUNT])
    prior = kpi("CALCULATE(SUM('F'[arrears_amount]), SAMEPERIODLASTYEAR('DIM_CALENDAR'"
                "[calendar_date]))", tool="powerbi", operands=[ACCOUNT])
    assert prior.time_modifier == "PY"
    assert fingerprint_kpi(base)[0] == fingerprint_kpi(prior)[0]
    assert fingerprint_kpi(base)[1] != fingerprint_kpi(prior)[1]


def test_prompts_and_macros_are_opaque():
    opaque = parse_expression("#prompt('Period','date')# total([S].[T].[x])")
    assert opaque.status == "PARSE_FAIL"
    embedded = parse_expression("total([S].[T].[x]) * ?Multiplier?")
    assert embedded.status == "PARSE_FAIL"


def test_opaque_metrics_never_merge_with_parsed_ones():
    parsed = kpi("total([S].[T].[arrears_amount])", operands=[ACCOUNT], label="Arrears")
    opaque = kpi("#prompt('x','date')# total([S].[T].[arrears_amount])", operands=[ACCOUNT],
                 label="Arrears")
    assert fingerprint_kpi(parsed)[0] != fingerprint_kpi(opaque)[0]


def test_unparseable_expression_is_flagged_not_guessed():
    assert parse_expression("total([S].[T].[x]").status == "PARSE_FAIL"
    assert parse_expression("").status == "PARSE_FAIL"


def _metric(metric_id, operands, aggregation="SUM", filters=()):
    return CanonicalMetric(metric_id=metric_id, canonical_name=metric_id, definition="",
                           fingerprint=metric_id, grain="Account", aggregation=aggregation,
                           operand_columns=list(operands), filter_columns=list(filters))


def test_conflict_patterns_are_classified():
    a = _metric("a", [DAYS, KEY])
    b = _metric("b", [DAYS, KEY])
    assert classify_pattern(a, b, "case(>(col(x),lit(60)))", "case(>(col(x),lit(59)))") == "THRESHOLD"
    assert classify_pattern(_metric("a", [ACCOUNT]), _metric("b", [ACCOUNT], "AVG")) == "AGGREGATION"
    ratio_a = _metric("a", [ACCOUNT, REVENUE], "RATIO")
    ratio_b = _metric("b", [ACCOUNT, TOTAL], "RATIO")
    assert classify_pattern(ratio_a, ratio_b) == "DENOMINATOR"
    time_a = _metric("a", [ACCOUNT, "S.D.T.DIM_CALENDAR.calendar_month"])
    time_b = _metric("b", [ACCOUNT, "S.D.T.DIM_CALENDAR.fiscal_period"])
    assert classify_pattern(time_a, time_b) == "TIME_BASIS"
