"""Parameterized synthetic extract generator (specification section 17).

One generation model, nine industry packs. The pack is deliberately imperfect:
it carries the duplication, conflicting definitions, broken lineage and missing
stewards the engine is built to find, and every injected defect is recorded in
``Planted_Defects`` so detection can be measured rather than claimed.
"""
from __future__ import annotations

import datetime as _dt
import random
from dataclasses import dataclass

from ..models import (
    CatalogColumnRecord, CatalogLineageRecord, ExtractBundle, GlossaryTermRecord,
    KpiRecord, ReportRecord,
)
from ..util.ids import stable_id
from .defects import DefectLog
from .estate import Estate, Table, build_estate
from .industries import IndustryPack, RatioSpec, get_pack

AGG_COGNOS = {
    "SUM": "total", "AVG": "average", "COUNT": "count",
    "COUNT DISTINCT": "count", "MAX": "maximum", "MIN": "minimum",
}
AGG_DAX = {
    "SUM": "SUM", "AVG": "AVERAGE", "COUNT": "COUNT",
    "COUNT DISTINCT": "DISTINCTCOUNT", "MAX": "MAX", "MIN": "MIN",
}

# report theme -> fact index, business-unit index (12 themes per pack)
THEME_FACT = (0, 1, 1, 2, 5, 3, 3, 6, 4, 5, 7, 5)
THEME_BU = (0, 0, 0, 2, 1, 3, 3, 4, 2, 1, 5, 6)
QUALIFIERS = (
    "Weekly", "Monthly", "Daily", "Summary", "Detail", "by Region", "by Business Unit",
    "YTD", "Trend", "Dashboard", "Exception", "Operational", "Executive View",
    "Quarter End", "Prior Year Comparison",
)
CADENCES = ("Daily", "Weekly", "Monthly", "Quarterly")
DISPOSITIONS = (("Retire", 0.30), ("Keep", 0.34), ("Merge", 0.21), ("Migrate", 0.15))


@dataclass
class GenerationParams:
    """Volumes, rates and structure - identical across industries by design."""

    cognos_reports: int = 150
    powerbi_reports: int = 90
    powerbi_model_measures: int = 22
    powerbi_report_measures: int = 42
    kpis_per_report: tuple[int, int] = (3, 4)
    kpi_row_target: int = 650
    secondary_fact_share: float = 0.30
    ratio_share: float = 0.25
    glossary_terms: int = 140
    dim_filler_columns: int = 8
    fact_filler_columns: int = 6
    staging_tables: int = 31
    staging_columns: int = 14
    reporting_view_count: int = 7
    reporting_view_share: float = 0.18
    broken_lineage_rate: float = 0.10
    missing_definition_rate: float = 0.25
    unassigned_steward_rate: float = 0.15
    opaque_rate: float = 0.05
    alias_rate: float = 0.06
    er4_rate: float = 0.04
    er6_rate: float = 0.015
    divergent_term_rate: float = 0.12
    term_reference_rate: float = 0.05
    zombie_reports: int = 3
    regulatory_reports: int = 2
    identical_kpi_groups: int = 3
    threshold_drift_groups: int = 3
    exclusion_drift_groups: int = 3
    denominator_swap_groups: int = 2
    time_basis_groups: int = 2
    cross_tool_groups: int = 3
    grain_mixing_groups: int = 2
    cousin_groups: int = 2
    jitter: float = 0.13

    def jittered(self, rng: random.Random) -> "GenerationParams":
        def jit(value: int) -> int:
            delta = int(value * self.jitter)
            return max(1, value + rng.randint(-delta, delta))
        return GenerationParams(
            cognos_reports=jit(self.cognos_reports),
            powerbi_reports=jit(self.powerbi_reports),
            powerbi_model_measures=jit(self.powerbi_model_measures),
            powerbi_report_measures=jit(self.powerbi_report_measures),
            kpis_per_report=self.kpis_per_report,
            kpi_row_target=self.kpi_row_target,
            secondary_fact_share=self.secondary_fact_share,
            ratio_share=self.ratio_share,
            glossary_terms=jit(self.glossary_terms),
            dim_filler_columns=self.dim_filler_columns,
            fact_filler_columns=self.fact_filler_columns,
            staging_tables=self.staging_tables,
            staging_columns=self.staging_columns,
            reporting_view_count=self.reporting_view_count,
            reporting_view_share=self.reporting_view_share,
            broken_lineage_rate=self.broken_lineage_rate,
            missing_definition_rate=self.missing_definition_rate,
            unassigned_steward_rate=self.unassigned_steward_rate,
            opaque_rate=self.opaque_rate,
            alias_rate=self.alias_rate,
            er4_rate=self.er4_rate,
            er6_rate=self.er6_rate,
            divergent_term_rate=self.divergent_term_rate,
            term_reference_rate=self.term_reference_rate,
            zombie_reports=self.zombie_reports,
            regulatory_reports=self.regulatory_reports,
            identical_kpi_groups=self.identical_kpi_groups,
            threshold_drift_groups=self.threshold_drift_groups,
            exclusion_drift_groups=self.exclusion_drift_groups,
            denominator_swap_groups=self.denominator_swap_groups,
            time_basis_groups=self.time_basis_groups,
            cross_tool_groups=self.cross_tool_groups,
            grain_mixing_groups=self.grain_mixing_groups,
            cousin_groups=self.cousin_groups,
        )


@dataclass
class MetricSpec:
    """One way of computing a business measure, before any report uses it."""

    key: str
    label: str
    aggregation: str
    operands: list[str]                  # column FQNs
    grain: str
    domain: str
    kind: str = "base"                   # base | ratio | threshold | timebasis | mixed_grain
    expression_cognos: str = ""
    expression_dax: str = ""
    filter_expression: str = ""
    primary_table: str = ""
    note: str = ""


@dataclass
class SyntheticPack:
    bundle: ExtractBundle
    tabs: dict[str, list[dict]]
    estate: Estate
    defects: DefectLog
    params: GenerationParams
    seed: int
    industry: str


# --------------------------------------------------------------------------
# Expression rendering
# --------------------------------------------------------------------------

def _cognos_ref(fqn: str) -> str:
    _system, _db, schema, table, column = fqn.split(".", 4)
    return f"[{schema}].[{table}].[{column}]"


def _dax_ref(fqn: str) -> str:
    _system, _db, _schema, table, column = fqn.split(".", 4)
    return f"'{table}'[{column}]"


def _cognos_agg(aggregation: str, ref: str) -> str:
    fn = AGG_COGNOS.get(aggregation.upper(), "total")
    inner = f"distinct {ref}" if aggregation.upper() == "COUNT DISTINCT" else ref
    return f"{fn}({inner})"


def _dax_agg(aggregation: str, ref: str) -> str:
    fn = AGG_DAX.get(aggregation.upper(), "SUM")
    return f"{fn}({ref})"


# --------------------------------------------------------------------------
# Generator
# --------------------------------------------------------------------------

class SyntheticGenerator:
    def __init__(self, industry: str = "generic", seed: int | None = None,
                 as_of: _dt.date | None = None, params: GenerationParams | None = None) -> None:
        self.pack: IndustryPack = get_pack(industry)
        self.industry = self.pack.key
        self.seed = seed if seed is not None else _default_seed(self.pack.key)
        self.rng = random.Random(self.seed)
        self.as_of = as_of or _dt.date.today()
        self.base_params = params or GenerationParams()
        self.params = self.base_params.jittered(random.Random(self.seed))
        self.defects = DefectLog()
        self.generation_id = stable_id("GEN", self.pack.key, self.seed, self.as_of.isoformat())
        self.estate: Estate | None = None
        self.metrics: dict[str, MetricSpec] = {}
        self.reports: list[ReportRecord] = []
        self.kpis: list[KpiRecord] = []
        self._kpi_rows: list[dict] = []
        self._pbi_reports: list[dict] = []
        self._pbi_measure_rows: list[dict] = []
        self._report_by_id: dict[str, ReportRecord] = {}

    # -- public ---------------------------------------------------------
    def generate(self) -> SyntheticPack:
        self.estate = build_estate(self.pack, self.rng, self.params)
        self._build_metric_catalogue()
        self._build_reports()
        self._plant_structural_defects()
        self._assign_kpis()
        self._plant_row_defects()
        self._build_power_bi()
        self._plant_lineage_defects()
        bundle = self._assemble_bundle()
        tabs = self._assemble_tabs(bundle)
        return SyntheticPack(bundle=bundle, tabs=tabs, estate=self.estate,
                             defects=self.defects, params=self.params,
                             seed=self.seed, industry=self.industry)

    # -- metric catalogue ------------------------------------------------
    def _fact_table(self, index: int) -> Table:
        return self.estate.fact_tables[index % len(self.estate.fact_tables)]

    def _measure_column(self, fact_index: int, measure_index: int) -> str:
        return self._measure_operands(fact_index, measure_index)[0]

    def _measure_operands(self, fact_index: int, measure_index: int) -> list[str]:
        """Columns one measure reads.

        A distinct count of an entity reads the conformed dimension key *and* the
        fact rows being counted, which is what makes a shared dimension visible to
        the clusterer as a hub rather than as a private column of one community.
        """
        fact_spec = self.pack.facts[fact_index % len(self.pack.facts)]
        measure = fact_spec.measures[measure_index % len(fact_spec.measures)]
        table = self._fact_table(fact_index)
        if measure.aggregation == "COUNT DISTINCT":
            dim = self._dim_for_entity(table.entity)
            if dim is not None:
                key = f"{table.entity.lower().replace(' ', '_')}_key"
                fqn = f"{dim.fqn}.{key}"
                fact_key = next((c.fqn for c in table.columns if c.primary_key), "")
                if fqn in self.estate.columns and fact_key:
                    return [fqn, fact_key]
        return [f"{table.fqn}.{measure.column}"]

    def _dim_for_entity(self, entity: str) -> Table | None:
        for table in self.estate.dim_tables:
            if table.entity == entity and table.kind == "dim":
                return table
        return None

    def _calendar_column(self, name: str) -> str | None:
        for table in self.estate.dim_tables:
            if table.entity == "Calendar":
                for col in table.columns:
                    if col.column == name:
                        return col.fqn
        return None

    def _build_metric_catalogue(self) -> None:
        pack = self.pack
        for f_i, fact_spec in enumerate(pack.facts):
            table = self._fact_table(f_i)
            for m_i, measure in enumerate(fact_spec.measures):
                operands = self._measure_operands(f_i, m_i)
                operand = operands[0]
                spec = MetricSpec(
                    key=f"m{f_i:02d}_{m_i}",
                    label=measure.label,
                    aggregation=measure.aggregation,
                    operands=operands,
                    grain=table.entity,
                    domain=table.domain,
                    primary_table=table.fqn,
                    expression_cognos=_cognos_agg(measure.aggregation, _cognos_ref(operand)),
                    expression_dax=_dax_agg(measure.aggregation, _dax_ref(operand)),
                )
                self.metrics[spec.key] = spec
        # Every subject area counts its entities, which is what makes a conformed
        # dimension a genuine hub across communities rather than one team's column.
        for f_i, fact_spec in enumerate(pack.facts):
            table = self._fact_table(f_i)
            if table.entity not in pack.backbone:
                continue
            dim = self._dim_for_entity(table.entity)
            fact_key = next((c.fqn for c in table.columns if c.primary_key), "")
            if dim is None or not fact_key:
                continue
            key_column = f"{dim.fqn}.{table.entity.lower().replace(' ', '_')}_key"
            if key_column not in self.estate.columns:
                continue
            plural = "Active " + table.entity + ("es" if table.entity.endswith("s") else "s")
            spec = MetricSpec(
                key=f"m{f_i:02d}_4",
                label=f"{plural} with {_subject_label(fact_spec.table)}",
                aggregation="COUNT DISTINCT",
                operands=[key_column, fact_key],
                grain=table.entity,
                domain=table.domain,
                primary_table=table.fqn,
                expression_cognos=_cognos_agg("COUNT DISTINCT", _cognos_ref(key_column)),
                expression_dax=_dax_agg("COUNT DISTINCT", _dax_ref(key_column)),
            )
            self.metrics[spec.key] = spec

        for r_i, ratio in enumerate(pack.ratios):
            self.metrics[f"r{r_i}"] = self._ratio_metric(f"r{r_i}", ratio, alternate=False)
            if ratio.denominator_swap:
                self.metrics[f"r{r_i}_alt"] = self._ratio_metric(f"r{r_i}_alt", ratio, alternate=True)

    def _ratio_metric(self, key: str, ratio: RatioSpec, alternate: bool) -> MetricSpec:
        num = self._measure_column(ratio.num_fact, ratio.num_measure)
        if alternate:
            den = self._measure_column(ratio.alt_den_fact, ratio.alt_den_measure)
        else:
            den = self._measure_column(ratio.den_fact, ratio.den_measure)
        num_table = self._fact_table(ratio.num_fact)
        mult = f" * {ratio.multiplier}" if ratio.multiplier != 1 else ""
        return MetricSpec(
            key=key,
            label=ratio.label,
            aggregation="RATIO",
            operands=[num, den],
            grain=num_table.entity,
            domain=num_table.domain,
            kind="ratio",
            primary_table=num_table.fqn,
            expression_cognos=(
                f"total({_cognos_ref(num)}) / total({_cognos_ref(den)}){mult}"),
            expression_dax=(
                f"DIVIDE(SUM({_dax_ref(num)}), SUM({_dax_ref(den)})){mult}"),
            note="alternate denominator" if alternate else "",
        )

    # -- reports ---------------------------------------------------------
    def _build_reports(self) -> None:
        pack, rng = self.pack, self.rng
        n = self.params.cognos_reports
        ranks = list(range(1, n + 1))
        rng.shuffle(ranks)
        owners = {bu: [f"{bu.split()[0].lower()}.owner{i}" for i in range(1, 4)]
                  for bu in pack.business_units}
        for i in range(n):
            theme_idx = i % len(pack.report_themes)
            theme = pack.report_themes[theme_idx]
            bu = pack.business_units[THEME_BU[theme_idx] % len(pack.business_units)]
            fact_index = THEME_FACT[theme_idx]
            table = self._fact_table(fact_index)
            rank = ranks[i]
            runs_12m = max(4, int(46000 / (rank ** 1.35)) + rng.randint(-5, 25))
            runs_90d = max(0, int(runs_12m * rng.uniform(0.15, 0.32)))
            users = max(1, min(180, int((runs_12m ** 0.42) * rng.uniform(0.7, 1.6))))
            days_ago = rng.choice([rng.randint(1, 45), rng.randint(1, 45),
                                   rng.randint(46, 150), rng.randint(151, 320)])
            scheduled = rng.random() < 0.45
            disposition = _weighted_choice(rng, DISPOSITIONS)
            qualifier = QUALIFIERS[(i * 7 + theme_idx) % len(QUALIFIERS)]
            report = ReportRecord(
                report_id=f"COG-{i+1:05d}",
                report_name=f"{theme} {qualifier}",
                folder_path=f"/Shared Reports/{table.domain}/{theme}",
                tool="cognos",
                semantic_container=f"{table.domain} Analytics Package",
                owner=rng.choice(owners[bu]),
                business_unit=bu,
                run_count_90d=runs_90d,
                run_count_12m=runs_12m,
                distinct_users_12m=users,
                last_run_date=self.as_of - _dt.timedelta(days=days_ago),
                schedule_flag=scheduled,
                schedule_frequency=rng.choice(CADENCES) if scheduled else "",
                disposition=disposition,
                redundancy_cluster_id=f"RC-{theme_idx:02d}" if rng.random() < 0.4 else "",
                complexity_score=round(rng.uniform(0.1, 0.95), 2),
                synthetic=True,
                generation_id=self.generation_id,
            )
            self.reports.append(report)
            self._report_by_id[report.report_id] = report

    def _report_fact_index(self, report: ReportRecord) -> int:
        idx = int(report.report_id.split("-")[1]) - 1
        return THEME_FACT[idx % len(self.pack.report_themes)]

    # -- KPI rows --------------------------------------------------------
    def _emit_kpi(self, report: ReportRecord, spec: MetricSpec, *,
                  label: str | None = None, filter_expression: str = "",
                  expression: str | None = None, operands: list[str] | None = None,
                  filter_columns: list[str] | None = None, suffix: str = "") -> str:
        kpi_id = f"KPI-{len(self._kpi_rows)+1:06d}"
        operands = operands or spec.operands
        label = label or spec.label
        expression = expression or spec.expression_cognos
        table = self.estate.tables.get(spec.primary_table)
        query_subject = table.table if table else spec.primary_table.split(".")[-1]
        for operand in operands:
            self._kpi_rows.append({
                "kpi_id": kpi_id,
                "kpi_label": label,
                "report_id": report.report_id,
                "fm_package": report.semantic_container,
                "query_subject": query_subject,
                "query_item": _query_item_for(operand),
                "calculation_expression": expression,
                "aggregation_type": spec.aggregation,
                "filter_expression": filter_expression,
                "column_role": "operand",
                "column_fqn": operand,
                "usage_rank": 0,
                "cross_report_count": 0,
                "metric_key": spec.key + suffix,
                "expression_language": "cognos",
            })
        for filter_column in (filter_columns or []):
            self._kpi_rows.append({
                "kpi_id": kpi_id,
                "kpi_label": label,
                "report_id": report.report_id,
                "fm_package": report.semantic_container,
                "query_subject": query_subject,
                "query_item": _query_item_for(filter_column),
                "calculation_expression": expression,
                "aggregation_type": spec.aggregation,
                "filter_expression": filter_expression,
                "column_role": "filter",
                "column_fqn": filter_column,
                "usage_rank": 0,
                "cross_report_count": 0,
                "metric_key": spec.key + suffix,
                "expression_language": "cognos",
            })
        return kpi_id

    def _assign_kpis(self) -> None:
        rng = self.rng
        # The structural defects have already claimed their rows; the base
        # allocation fills what is left of the section 17.1 band.
        remaining = max(120, self.params.kpi_row_target - len(self._kpi_rows))
        # A KPI emits one lineage row per operand column, so the row budget has to
        # be divided by the average operand count, not by the KPI count.
        base_specs = [m for k, m in self.metrics.items() if k.startswith("m")]
        operands_per_kpi = (sum(len(m.operands) for m in base_specs) / len(base_specs)
                            if base_specs else 1.0)
        per_report = max(1.2, remaining / (max(1, len(self.reports)) * max(1.0, operands_per_kpi)))
        # Round probabilistically rather than in whole steps, so the mean tracks
        # the budget instead of jumping a whole KPI per report.
        floor_count, fraction = int(per_report), per_report - int(per_report)
        for report in self.reports:
            fact_index = self._report_fact_index(report)
            count = floor_count + (1 if rng.random() < fraction else 0)
            picks: list[MetricSpec] = []
            fact_metrics = [m for k, m in self.metrics.items()
                            if k.startswith(f"m{fact_index:02d}_")]
            rng.shuffle(fact_metrics)
            picks.extend(fact_metrics[:max(1, count - 1)])
            if rng.random() < self.params.secondary_fact_share:
                other = (fact_index + rng.choice([1, 2, 5])) % len(self.pack.facts)
                other_metrics = [m for k, m in self.metrics.items()
                                 if k.startswith(f"m{other:02d}_")]
                if other_metrics:
                    picks.append(rng.choice(other_metrics))
            if rng.random() < self.params.ratio_share and self.pack.ratios:
                r_i = rng.randrange(len(self.pack.ratios))
                picks.append(self.metrics[f"r{r_i}"])
            for spec in picks[:count]:
                filter_expression = ""
                if rng.random() < 0.35:
                    filter_expression = self._standard_filter(spec)
                self._emit_kpi(report, spec, filter_expression=filter_expression)

    def _standard_filter(self, spec: MetricSpec) -> str:
        rng = self.rng
        table = self.estate.tables.get(spec.primary_table)
        if table is None:
            return ""
        candidates = [c for c in table.columns if c.column.endswith("_date")]
        cal_fiscal = self._calendar_column("fiscal_period")
        if candidates and rng.random() < 0.6:
            col = rng.choice(candidates)
            return f"{_cognos_ref(col.fqn)} between ?StartDate? and ?EndDate?"
        if cal_fiscal:
            return f"{_cognos_ref(cal_fiscal)} = ?FiscalPeriod?"
        return ""

    # -- planted defects on the Cognos side ------------------------------
    def _plant_structural_defects(self) -> None:
        """Defects that add KPI rows. Planted before the base allocation, so the
        pack's total row count stays inside the band whatever the report count."""
        rng, params = self.rng, self.params
        base_keys = [k for k in self.metrics if k.startswith("m")]

        # 1. Identical KPI repeated across many reports.
        for group in range(params.identical_kpi_groups):
            spec = self.metrics[base_keys[(group * 7 + 3) % len(base_keys)]]
            targets = self._reports_for_metric(spec, rng.randint(5, 11))
            for report in targets:
                self._emit_kpi(report, spec)
            self.defects.plant("IDENTICAL_KPI", [r.report_id for r in targets],
                               count=len(targets),
                               detail=f"'{spec.label}' computed identically in {len(targets)} reports")

        # 2. Threshold drift: the same labelled measure with different hard-coded
        #    thresholds inside the expression, so the fingerprints differ.
        threshold_column = self._threshold_column()
        if threshold_column:
            entity_key = self._entity_key_column(threshold_column)
            for group in range(params.threshold_drift_groups):
                label = f"{self.pack.threshold_metric} 60 Plus" if group == 0 else \
                    f"{self.pack.threshold_metric} Breach Group {group + 1}"
                variants = [("> 60", 60, ">"), (">= 61", 61, ">="), ("> 59", 59, ">")]
                touched: list[str] = []
                for v_i, (text, value, operator) in enumerate(variants):
                    spec = MetricSpec(
                        key=f"thr{group}_{v_i}",
                        label=label,
                        aggregation="COUNT DISTINCT",
                        operands=[entity_key, threshold_column],
                        grain=self._grain_of(threshold_column),
                        domain=self._domain_of(threshold_column),
                        kind="threshold",
                        primary_table=threshold_column.rsplit(".", 1)[0],
                        expression_cognos=(
                            f"count(distinct case when {_cognos_ref(threshold_column)} {operator} "
                            f"{value} then {_cognos_ref(entity_key)} end)"),
                        expression_dax=(
                            f"CALCULATE(DISTINCTCOUNT({_dax_ref(entity_key)}), "
                            f"{_dax_ref(threshold_column)} {operator} {value})"),
                        note=f"threshold {text}",
                    )
                    self.metrics[spec.key] = spec
                    for report in self._reports_for_metric(spec, rng.randint(2, 6)):
                        self._emit_kpi(report, spec)
                        touched.append(report.report_id)
                self.defects.plant("THRESHOLD_DRIFT", touched, count=len(touched),
                                   detail=f"'{label}' uses > 60, >= 61 and > 59 across reports")

        # 3. Exclusion drift: same fingerprint, one variant carries an extra filter.
        exclusion_columns = self._exclusion_columns()
        for group in range(params.exclusion_drift_groups):
            spec = self.metrics[base_keys[(group * 11 + 5) % len(base_keys)]]
            if not exclusion_columns:
                break
            column = exclusion_columns[group % len(exclusion_columns)]
            filter_expression = f"{_cognos_ref(column)} <> 'Y'"
            plain = self._reports_for_metric(spec, 3)
            excluded = self._reports_for_metric(spec, 2)
            for report in plain:
                self._emit_kpi(report, spec)
            for report in excluded:
                self._emit_kpi(report, spec, filter_expression=filter_expression,
                               filter_columns=[column], suffix="_excl")
            self.defects.plant(
                "EXCLUSION_DRIFT", [r.report_id for r in plain + excluded],
                count=len(plain) + len(excluded),
                detail=f"'{spec.label}' excludes {column.rsplit('.', 1)[-1]} in {len(excluded)} reports only")

        # 4. Denominator swap on a labelled ratio.
        for r_i, ratio in enumerate(self.pack.ratios[:params.denominator_swap_groups]):
            if not ratio.denominator_swap:
                continue
            primary = self.metrics[f"r{r_i}"]
            alternate = self.metrics.get(f"r{r_i}_alt")
            if alternate is None:
                continue
            touched = []
            for report in self._reports_for_metric(primary, 4):
                self._emit_kpi(report, primary)
                touched.append(report.report_id)
            for report in self._reports_for_metric(alternate, 3):
                self._emit_kpi(report, alternate)
                touched.append(report.report_id)
            self.defects.plant(
                "DENOMINATOR_SWAP", touched, count=len(touched),
                detail=(f"'{ratio.label}' divides by {primary.operands[1].rsplit('.', 1)[-1]} "
                        f"in some reports and {alternate.operands[1].rsplit('.', 1)[-1]} in others"))

        # 5. Time-basis drift: calendar month vs fiscal period as the operand.
        calendar = self._calendar_column("calendar_month")
        fiscal = self._calendar_column("fiscal_period")
        if calendar and fiscal:
            for group in range(params.time_basis_groups):
                spec = self.metrics[base_keys[(group * 13 + 2) % len(base_keys)]]
                label = f"{spec.label} by Period"
                touched = []
                for basis_name, basis_col in (("calendar", calendar), ("fiscal", fiscal)):
                    variant = MetricSpec(
                        key=f"tb{group}_{basis_name}",
                        label=label,
                        aggregation=spec.aggregation,
                        operands=list(spec.operands) + [basis_col],
                        grain=spec.grain, domain=spec.domain, kind="timebasis",
                        primary_table=spec.primary_table,
                        expression_cognos=(
                            f"{_cognos_agg(spec.aggregation, _cognos_ref(spec.operands[0]))} "
                            f"for {_cognos_ref(basis_col)}"),
                        expression_dax=(
                            f"CALCULATE({_dax_agg(spec.aggregation, _dax_ref(spec.operands[0]))}, "
                            f"VALUES({_dax_ref(basis_col)}))"),
                        note=f"{basis_name} basis",
                    )
                    self.metrics[variant.key] = variant
                    for report in self._reports_for_metric(variant, rng.randint(2, 4)):
                        self._emit_kpi(report, variant)
                        touched.append(report.report_id)
                self.defects.plant("TIME_BASIS_DRIFT", touched, count=len(touched),
                                   detail=f"'{label}' is computed on calendar month and on fiscal period")

        # 6. Grain mixing: a ratio whose operands sit at two different grains.
        for group in range(params.grain_mixing_groups):
            fine_index = (3 + group) % len(self.pack.facts)
            coarse_index = (0 + group) % len(self.pack.facts)
            coarse = self._measure_column(coarse_index, 0)
            fine = self._measure_column(fine_index, 0)
            coarse_table = self._fact_table(coarse_index)
            fine_table = self._fact_table(fine_index)
            if coarse_table.entity == fine_table.entity:
                continue
            spec = MetricSpec(
                key=f"gm{group}",
                label=f"{coarse_table.entity} {self.pack.facts[coarse_index].measures[0].label} "
                      f"per {fine_table.entity}",
                aggregation="RATIO",
                operands=[coarse, fine],
                grain=coarse_table.entity,
                domain=coarse_table.domain,
                kind="mixed_grain",
                primary_table=coarse_table.fqn,
                expression_cognos=f"total({_cognos_ref(coarse)}) / total({_cognos_ref(fine)})",
                expression_dax=f"DIVIDE(SUM({_dax_ref(coarse)}), SUM({_dax_ref(fine)}))",
                note="mixes grains",
            )
            self.metrics[spec.key] = spec
            touched = []
            for report in self._reports_for_metric(spec, rng.randint(3, 5)):
                self._emit_kpi(report, spec)
                touched.append(report.report_id)
            self.defects.plant(
                "GRAIN_MIXING", touched, count=len(touched),
                detail=f"community mixes {coarse_table.entity} and {fine_table.entity} grain measures")

        # 7. Structural cousins: same operand column, different aggregation.
        for group in range(params.cousin_groups):
            spec = self.metrics[base_keys[(group * 17 + 4) % len(base_keys)]]
            if spec.aggregation not in ("SUM", "AVG"):
                continue
            other = "AVG" if spec.aggregation == "SUM" else "SUM"
            cousin = MetricSpec(
                key=f"cz{group}",
                label=f"Average {spec.label}" if other == "AVG" else f"Total {spec.label}",
                aggregation=other,
                operands=list(spec.operands),
                grain=spec.grain, domain=spec.domain, kind="base",
                primary_table=spec.primary_table,
                expression_cognos=_cognos_agg(other, _cognos_ref(spec.operands[0])),
                expression_dax=_dax_agg(other, _dax_ref(spec.operands[0])),
            )
            self.metrics[cousin.key] = cousin
            touched = []
            for report in self._reports_for_metric(cousin, 3):
                self._emit_kpi(report, cousin)
                touched.append(report.report_id)
            self.defects.plant("STRUCTURAL_COUSIN", touched, count=len(touched),
                               detail=f"'{spec.label}' appears as both {spec.aggregation} and {other}")

    def _plant_row_defects(self) -> None:
        """Defects that mark rows already emitted, or that live in the catalog."""
        rng, params = self.rng, self.params

        # 8. Opaque expressions: prompts and macros the parser cannot read.
        opaque_targets = rng.sample(self._kpi_rows, k=max(1, int(len(self._kpi_rows) * params.opaque_rate)))
        opaque_ids = set()
        for row in opaque_targets:
            opaque_ids.add(row["kpi_id"])
        for row in self._kpi_rows:
            if row["kpi_id"] in opaque_ids:
                row["calculation_expression"] = (
                    "#prompt('ReportingPeriod','date','2026-01-01')# " + row["calculation_expression"]
                    + " /* macro: <#sq($Parameter)#> */")
        self.defects.plant("OPAQUE_EXPRESSION", sorted(opaque_ids)[:12], count=len(opaque_ids),
                           detail=f"{len(opaque_ids)} KPI expressions carry a prompt or macro")

        # 9. Zombie reports and regulatory low-usage reports.
        busy = sorted(self.reports, key=lambda r: -r.run_count_12m)
        for report in busy[3:3 + params.zombie_reports]:
            report.last_run_date = self.as_of - _dt.timedelta(days=rng.randint(430, 520))
            report.run_count_90d = 0
            self.defects.plant("ZOMBIE_REPORT", [report.report_id],
                               detail=f"{report.report_name} last ran {report.last_run_date}")
        regulatory = [r for r in self.reports if "Regulatory" in r.report_name
                      or "Statutory" in r.report_name or "Filing" in r.report_name
                      or "Return" in r.report_name or "Submission" in r.report_name]
        for report in (regulatory or busy[-6:])[:params.regulatory_reports]:
            report.run_count_12m = rng.randint(4, 12)
            report.run_count_90d = rng.randint(0, 3)
            report.distinct_users_12m = rng.randint(2, 5)
            report.disposition = "Keep"
            report.schedule_flag = True
            report.schedule_frequency = "Quarterly"
            self.defects.plant("REGULATORY_LOW_USAGE", [report.report_id],
                               detail=f"{report.report_name} runs {report.run_count_12m} times a year "
                                      "but carries statutory consequence")

        # 10. The sunset system.
        sunset = [t for t in self.estate.tables.values() if t.lifecycle == "sunset"]
        if sunset:
            self.defects.plant("SUNSET_SOURCE", sorted({t.system for t in sunset}),
                               count=len(sunset),
                               detail=f"{sunset[0].system} is marked sunset with no successor mapped")

        # 11. Metadata gaps already applied to the estate.
        missing = [c for c in self.estate.columns.values() if not c.business_term]
        self.defects.plant("MISSING_DEFINITION", [c.fqn for c in missing[:10]], count=len(missing),
                           detail=f"{len(missing)} of {len(self.estate.columns)} columns have no business term")
        unassigned = [t for t in self.estate.glossary if not t.get("steward")]
        self.defects.plant("UNASSIGNED_STEWARD", [t["term"] for t in unassigned[:10]],
                           count=len(unassigned),
                           detail=f"{len(unassigned)} of {len(self.estate.glossary)} terms have no steward")

    def _reports_for_metric(self, spec: MetricSpec, count: int) -> list[ReportRecord]:
        """Pick reports whose theme already sits near this metric's fact table."""
        table_fqn = spec.primary_table
        near = [r for r in self.reports
                if self._fact_table(self._report_fact_index(r)).fqn == table_fqn]
        pool = near or self.reports
        count = min(count, len(pool))
        return self.rng.sample(pool, k=count)

    def _threshold_column(self) -> str | None:
        target = self.pack.threshold_column
        for table in self.estate.fact_tables:
            for col in table.columns:
                if col.column == target:
                    return col.fqn
        return None

    def _entity_key_column(self, column_fqn: str) -> str:
        table = self.estate.tables.get(column_fqn.rsplit(".", 1)[0])
        if table:
            dim = self._dim_for_entity(table.entity)
            if dim:
                key = f"{table.entity.lower().replace(' ', '_')}_key"
                fqn = f"{dim.fqn}.{key}"
                if fqn in self.estate.columns:
                    return fqn
        return column_fqn

    def _exclusion_columns(self) -> list[str]:
        out = []
        for table in self.estate.dim_tables:
            for col in table.columns:
                if col.column.endswith("_flag") and "attribute" not in col.column:
                    out.append(col.fqn)
        return out

    def _grain_of(self, column_fqn: str) -> str:
        table = self.estate.tables.get(column_fqn.rsplit(".", 1)[0])
        return table.entity if table else "unknown"

    def _domain_of(self, column_fqn: str) -> str:
        table = self.estate.tables.get(column_fqn.rsplit(".", 1)[0])
        return table.domain if table else ""

    # -- Power BI --------------------------------------------------------
    def _build_power_bi(self) -> None:
        rng, pack, params = self.rng, self.pack, self.params
        workspaces = [f"{domain} Workspace" for domain in pack.domains]
        models: list[dict] = []
        for i, fact_table in enumerate(self.estate.fact_tables):
            models.append({
                "semantic_model": f"{fact_table.table.replace('FCT_', '').title().replace('_', ' ')} Model",
                "workspace": workspaces[i % len(workspaces)],
                "fact_index": i,
                "storage_mode": rng.choice(["Import", "DirectQuery", "Import"]),
            })
        # Two models over the same source tables: the Power BI duplication signal.
        duplicate = dict(models[0])
        duplicate["semantic_model"] = duplicate["semantic_model"] + " (Finance Copy)"
        duplicate["workspace"] = workspaces[(1) % len(workspaces)]
        models.append(duplicate)

        for i in range(params.powerbi_reports):
            model = models[i % len(models)]
            theme = pack.report_themes[i % len(pack.report_themes)]
            bu = pack.business_units[THEME_BU[i % len(THEME_BU)] % len(pack.business_units)]
            rank = i + 1
            views = max(3, int(9000 / (rank ** 1.2)) + rng.randint(-4, 20))
            self._pbi_reports.append({
                "report_id": f"PBI-{i+1:05d}",
                "report_name": f"{theme} ({model['workspace'].split()[0]})",
                "workspace": model["workspace"],
                "app_name": f"{model['workspace'].split()[0]} App",
                "semantic_model": model["semantic_model"],
                "storage_mode": model["storage_mode"],
                "owner": f"{bu.split()[0].lower()}.pbi{(i % 3) + 1}",
                "business_unit": bu,
                "view_count_90d": max(0, int(views * rng.uniform(0.2, 0.35))),
                "view_count_12m": views,
                "distinct_users_12m": max(1, int((views ** 0.45) * rng.uniform(0.6, 1.4))),
                "last_viewed_date": (self.as_of - _dt.timedelta(
                    days=rng.choice([rng.randint(1, 40), rng.randint(41, 160), rng.randint(200, 400)]))).isoformat(),
                "refresh_schedule": rng.choice(["Daily 06:00", "Weekly Mon 07:00", "", "Hourly"]),
                "disposition": _weighted_choice(rng, DISPOSITIONS),
                "certified": rng.choice(["Certified", "Promoted", "", ""]),
            })

        reports_by_model: dict[str, list[dict]] = {}
        for report in self._pbi_reports:
            reports_by_model.setdefault(report["semantic_model"], []).append(report)

        base_keys = [k for k in self.metrics if k.startswith("m")]
        measure_seq = 0

        # Model-scope measures: defined once, used by many reports (section 16.2).
        for i in range(params.powerbi_model_measures):
            model = models[i % len(models)]
            fact_index = model["fact_index"]
            candidates = [k for k in base_keys if k.startswith(f"m{fact_index:02d}_")]
            if not candidates:
                continue
            spec = self.metrics[candidates[i % len(candidates)]]
            measure_seq += 1
            measure_id = f"PBM-{measure_seq:05d}"
            consumers = reports_by_model.get(model["semantic_model"], [])
            consumers = consumers[: rng.randint(2, max(2, min(8, len(consumers) or 2)))]
            time_intelligence = rng.random() < 0.25
            expression = spec.expression_dax
            label = spec.label
            if time_intelligence:
                calendar_date = self._calendar_column("calendar_date")
                if calendar_date:
                    expression = (f"CALCULATE({spec.expression_dax}, "
                                  f"SAMEPERIODLASTYEAR({_dax_ref(calendar_date)}))")
                    label = f"{spec.label} PY"
            for report in consumers:
                self._emit_pbi_rows(measure_id, label, "model", model, report, spec, expression)

        # Report-scope measures: the Power BI duplication signal in its own right.
        for i in range(params.powerbi_report_measures):
            report = self._pbi_reports[i % len(self._pbi_reports)]
            model = next(m for m in models if m["semantic_model"] == report["semantic_model"])
            candidates = [k for k in base_keys if k.startswith(f"m{model['fact_index']:02d}_")]
            if not candidates:
                continue
            spec = self.metrics[candidates[(i * 3) % len(candidates)]]
            measure_seq += 1
            measure_id = f"PBM-{measure_seq:05d}"
            self._emit_pbi_rows(measure_id, spec.label, "report", model, report, spec,
                                spec.expression_dax)

        # Cross-tool duplication: the same KPI in Cognos and as a DAX measure.
        touched: list[str] = []
        for group in range(params.cross_tool_groups):
            spec = self.metrics[base_keys[(group * 5 + 1) % len(base_keys)]]
            model = models[spec_fact_index(spec) % len(models)]
            consumers = reports_by_model.get(model["semantic_model"], self._pbi_reports)[:3]
            measure_seq += 1
            measure_id = f"PBM-{measure_seq:05d}"
            for report in consumers:
                self._emit_pbi_rows(measure_id, spec.label, "model", model, report, spec,
                                    spec.expression_dax)
                touched.append(report["report_id"])
            cognos_reports = self._reports_for_metric(spec, 3)
            for report in cognos_reports:
                self._emit_kpi(report, spec)
                touched.append(report.report_id)
        if touched:
            self.defects.plant("CROSS_TOOL_DUPLICATION", touched, count=len(touched),
                               detail="the same calculation exists as a Cognos KPI and a DAX measure")

    def _emit_pbi_rows(self, measure_id: str, label: str, scope: str, model: dict,
                       report: dict, spec: MetricSpec, expression: str) -> None:
        for operand in spec.operands:
            _system, _db, _schema, table, column = operand.split(".", 4)
            self._pbi_measure_rows.append({
                "measure_id": measure_id,
                "measure_name": label,
                "measure_scope": scope,
                "semantic_model": model["semantic_model"],
                "workspace": model["workspace"],
                "report_id": report["report_id"],
                "model_table": table,
                "model_column": column,
                "dax_expression": expression,
                "filter_expression": "",
                "aggregation_type": spec.aggregation,
                "source_system": operand.split(".")[0],
                "source_database": operand.split(".")[1],
                "source_schema": operand.split(".")[2],
                "source_table": table,
                "source_column": column,
                "column_role": "operand",
                "storage_mode": model["storage_mode"],
                "column_fqn": operand,
                "metric_key": spec.key,
            })

    # -- lineage-level defects -------------------------------------------
    def _plant_lineage_defects(self) -> None:
        """Make the lineage rows as imperfect as a real extract.

        Each rule below lands on a disjoint sample of rows, so one row never
        carries two defects and the expected resolution rule is unambiguous.
        """
        rng, params = self.rng, self.params

        # Point a share of Cognos rows at the reporting view rather than the fact
        # table, so the resolver has to walk catalog lineage (ER-3).
        for row in self._kpi_rows:
            fqn = row["column_fqn"]
            table_fqn = fqn.rsplit(".", 1)[0]
            view = self.estate.reporting_tables.get(table_fqn)
            if view and rng.random() < params.reporting_view_share:
                column = fqn.rsplit(".", 1)[-1]
                candidate = f"{view.fqn}.{column}"
                if candidate in self.estate.columns:
                    row["column_fqn"] = candidate

        rows = [r for r in self._kpi_rows + self._pbi_measure_rows
                if r["column_fqn"] in self.estate.columns]
        rng.shuffle(rows)
        cursor = 0

        def take(rate: float) -> list[dict]:
            nonlocal cursor
            count = int(len(rows) * rate)
            chunk = rows[cursor:cursor + count]
            cursor += count
            return chunk

        # ER-2: a query-item alias that only resolves once abbreviations expand.
        for row in take(params.alias_rate):
            column = row["column_fqn"].rsplit(".", 1)[-1]
            row["alias_column"] = _abbreviate(column)
            row["alias_query_item"] = _title(_abbreviate(column))

        # ER-4: a near-miss column name that survives on similarity alone.
        for i, row in enumerate(take(params.er4_rate)):
            column = row["column_fqn"].rsplit(".", 1)[-1]
            suffix = ("_usd", "_local", "_reporting")[i % 3]
            row["alias_column"] = f"{column}{suffix}"
            row["alias_query_item"] = _title(f"{column}{suffix}")

        # ER-5: the lineage row names the business term, not the column. Only
        # terms that read differently from the column name exercise this rule.
        from ..util.text import token_key as _tk
        for row in take(params.term_reference_rate * 2):
            column = self.estate.columns.get(row["column_fqn"])
            if column is None or not column.business_term:
                continue
            if _tk(column.business_term) == _tk(column.column):
                continue
            row["alias_column"] = column.business_term
            row["alias_query_item"] = column.business_term

        # ER-6: a synonym that only an embedding will recognise.
        for i, row in enumerate(take(params.er6_rate)):
            column = self.estate.columns.get(row["column_fqn"])
            if column is None or not column.business_term:
                continue
            row["alias_column"] = _synonym(column.column)
            row["alias_query_item"] = f"{column.business_term} reported figure"

        # Broken lineage: a share of rows point at something the catalog does not
        # hold - half a renamed table, half a column dropped in a past release.
        broken = take(params.broken_lineage_rate)
        for i, row in enumerate(broken):
            row.pop("alias_query_item", None)
            if i % 2 == 0:
                row["broken_table"] = True
                row.pop("alias_column", None)
            else:
                row["alias_column"] = f"retired_field_{i:03d}"
                row["alias_query_item"] = f"Retired Field {i:03d}"
            row["broken"] = True
        self.defects.plant("BROKEN_LINEAGE",
                           [r.get("kpi_id") or r.get("measure_id") for r in broken[:10]],
                           count=len(broken),
                           detail=f"{len(broken)} of {len(rows)} lineage rows reference a table or "
                                  "column absent from the catalog")

    # -- assembly --------------------------------------------------------
    def _assemble_bundle(self) -> ExtractBundle:
        bundle = ExtractBundle(
            mode="automated", catalog="collibra", industry=self.industry,
            as_of_date=self.as_of, synthetic=True, generation_id=self.generation_id,
        )
        bundle.reports = list(self.reports)
        for report in self._pbi_reports:
            bundle.reports.append(ReportRecord(
                report_id=report["report_id"], report_name=report["report_name"],
                folder_path=f"/{report['workspace']}", tool="powerbi",
                semantic_container=report["semantic_model"], workspace=report["workspace"],
                owner=report["owner"], business_unit=report["business_unit"],
                run_count_90d=report["view_count_90d"], run_count_12m=report["view_count_12m"],
                distinct_users_12m=report["distinct_users_12m"],
                last_run_date=_dt.date.fromisoformat(report["last_viewed_date"]),
                schedule_flag=bool(report["refresh_schedule"]),
                schedule_frequency=report["refresh_schedule"],
                disposition=report["disposition"], complexity_score=0.0,
                synthetic=True, generation_id=self.generation_id,
            ))
        for row in self._kpi_rows:
            bundle.kpis.append(self._kpi_record(row))
        for row in self._pbi_measure_rows:
            bundle.kpis.append(self._pbi_record(row))
        for column in self.estate.columns.values():
            table = self.estate.tables[column.table_fqn]
            bundle.columns.append(CatalogColumnRecord(
                system=column.system, database=column.database, schema=column.schema,
                table=column.table, column=column.column,
                business_term=column.business_term, definition=column.definition,
                data_domain=table.domain, sub_domain=table.sub_domain,
                data_owner=table.owner, data_steward=table.steward,
                classification=column.classification, pii_flag=column.pii_flag,
                data_type=column.data_type, nullable=column.nullable,
                primary_key=column.primary_key, foreign_key=column.foreign_key,
                system_of_record=table.sor, certification_status=column.certification_status,
                quality_score=column.quality_score, lifecycle_status=table.lifecycle,
                sunset_date=table.sunset_date, successor_system=table.successor_system,
                row_count=table.row_count, catalog="collibra", synthetic=True,
            ))
        for edge in self.estate.lineage:
            bundle.lineage.append(_lineage_record(edge))
        for term in self.estate.glossary:
            bundle.glossary.append(GlossaryTermRecord(
                term_id=term["term_id"], term=term["term"], definition=term["definition"],
                domain=term["domain"], sub_domain=term.get("sub_domain", ""),
                steward=term.get("steward", ""), status=term.get("status", ""),
            ))
        bundle.planted_defects = self.defects.rows()
        bundle.manifest = self.manifest()
        return bundle

    def _kpi_record(self, row: dict) -> KpiRecord:
        fqn = row["column_fqn"]
        system, database, schema, table, column = fqn.split(".", 4)
        column_name = row.get("alias_column") or column
        if row.get("broken_table"):
            table = f"{table}_ARCHIVE"
        return KpiRecord(
            kpi_id=row["kpi_id"], kpi_label=row["kpi_label"], report_id=row["report_id"],
            semantic_container=row["fm_package"], query_subject=row["query_subject"],
            query_item=row.get("alias_query_item") or row["query_item"],
            calculation_expression=row["calculation_expression"],
            expression_language="cognos", aggregation_type=row["aggregation_type"],
            filter_expression=row["filter_expression"], source_system=system,
            database=database, schema=schema, table=table, column=column_name,
            measure_scope="report", tool="cognos", synthetic=True,
            generation_id=self.generation_id,
        )

    def _pbi_record(self, row: dict) -> KpiRecord:
        column_name = row.get("alias_column") or row["source_column"]
        source_table = row["source_table"]
        if row.get("broken_table"):
            source_table = f"{source_table}_ARCHIVE"
        return KpiRecord(
            kpi_id=row["measure_id"], kpi_label=row["measure_name"], report_id=row["report_id"],
            semantic_container=row["semantic_model"], query_subject=row["model_table"],
            query_item=row["model_column"], calculation_expression=row["dax_expression"],
            expression_language="dax", aggregation_type=row["aggregation_type"],
            filter_expression=row["filter_expression"], source_system=row["source_system"],
            database=row["source_database"], schema=row["source_schema"],
            table=source_table, column=column_name,
            measure_scope=row["measure_scope"], tool="powerbi", synthetic=True,
            generation_id=self.generation_id,
        )

    def manifest(self) -> dict:
        return {
            "industry": self.industry,
            "industry_label": self.pack.label,
            "seed": self.seed,
            "generation_id": self.generation_id,
            "as_of_date": self.as_of.isoformat(),
            "cognos_reports": len(self.reports),
            "cognos_kpi_rows": len(self._kpi_rows),
            "powerbi_reports": len(self._pbi_reports),
            "powerbi_measure_rows": len(self._pbi_measure_rows),
            "catalog_columns": len(self.estate.columns),
            "catalog_tables": len(self.estate.tables),
            "catalog_lineage_edges": len(self.estate.lineage),
            "glossary_terms": len(self.estate.glossary),
            "planted_defects": len(self.defects.items),
            "defect_summary": self.defects.summary(),
            "parameters": {
                "broken_lineage_rate": self.params.broken_lineage_rate,
                "missing_definition_rate": self.params.missing_definition_rate,
                "unassigned_steward_rate": self.params.unassigned_steward_rate,
                "opaque_rate": self.params.opaque_rate,
                "alias_rate": self.params.alias_rate,
                "reporting_view_share": self.params.reporting_view_share,
            },
        }

    def _assemble_tabs(self, bundle: ExtractBundle) -> dict[str, list[dict]]:
        from .workbook import build_tabs
        return build_tabs(self, bundle)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def spec_fact_index(spec: MetricSpec) -> int:
    if spec.key.startswith("m"):
        try:
            return int(spec.key[1:3])
        except ValueError:
            return 0
    return 0


def _weighted_choice(rng: random.Random, options: tuple[tuple[str, float], ...]) -> str:
    roll = rng.random()
    cumulative = 0.0
    for value, weight in options:
        cumulative += weight
        if roll <= cumulative:
            return value
    return options[-1][0]


def _subject_label(table_name: str) -> str:
    cleaned = table_name.replace("FCT_", "").replace("_", " ").title()
    return cleaned


def _query_item_for(column_fqn: str) -> str:
    column = column_fqn.rsplit(".", 1)[-1]
    return " ".join(part.capitalize() for part in column.split("_"))


def _title(column: str) -> str:
    return " ".join(part.capitalize() for part in column.replace("_", " ").split())


SYNONYMS = {
    "amount": "value", "balance": "position", "count": "volume", "revenue": "income",
    "cost": "spend", "days": "ageing", "rate": "ratio", "total": "aggregate",
    "payment": "remittance", "arrears": "overdue", "charge": "billing",
}


def _synonym(column: str) -> str:
    parts = column.split("_")
    return "_".join(SYNONYMS.get(p, p) for p in parts)


def _abbreviate(column: str) -> str:
    replacements = {
        "account": "acct", "customer": "cust", "number": "no", "amount": "amt",
        "identifier": "id", "quantity": "qty", "date": "dt", "description": "desc",
        "payment": "pmt", "transaction": "txn", "balance": "bal", "premise": "prem",
        "revenue": "rev", "service": "svc", "average": "avg", "percent": "pct",
    }
    parts = column.split("_")
    return "_".join(replacements.get(p, p) for p in parts)


def _lineage_record(edge: dict) -> CatalogLineageRecord:
    def split(fqn: str, is_column: bool):
        parts = fqn.split(".")
        if is_column:
            return parts[0], parts[1], parts[2], parts[3], parts[4]
        return parts[0], parts[1], parts[2], parts[3], ""
    is_column = edge["level"] == "column"
    s = split(edge["src"], is_column)
    t = split(edge["tgt"], is_column)
    return CatalogLineageRecord(
        src_system=s[0], src_database=s[1], src_schema=s[2], src_table=s[3], src_column=s[4],
        tgt_system=t[0], tgt_database=t[1], tgt_schema=t[2], tgt_table=t[3], tgt_column=t[4],
        level=edge["level"], transformation=edge.get("transformation", ""),
    )


def _default_seed(industry: str) -> int:
    return 1000 + sum(ord(c) for c in industry) * 7
