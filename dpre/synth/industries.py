"""Per-industry content packs for the synthetic data generator (section 17.1).

Structure is identical across industries: five source systems (the last one
sunset), a five-entity conformed backbone, eight fact tables, nine dimensions
and three reference tables. Only the *names* differ, which is what keeps the
engine free of industry-specific logic.
"""
from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Measure:
    column: str
    label: str
    aggregation: str
    kind: str = "amount"          # amount | count | rate | duration | status


@dataclass(frozen=True)
class SystemSpec:
    code: str
    name: str
    kind: str                     # core | transactional | servicing | finance | legacy
    sor: bool
    lifecycle: str = "active"


@dataclass(frozen=True)
class FactSpec:
    table: str
    schema: str
    system_index: int
    grain: str
    measures: tuple[Measure, ...]
    date_columns: tuple[str, ...]


@dataclass(frozen=True)
class DimSpec:
    table: str
    schema: str
    system_index: int
    entity: str
    attributes: tuple[str, ...]


@dataclass(frozen=True)
class RatioSpec:
    """A derived (ratio) metric, and the denominator a competing report uses."""

    label: str
    num_fact: int
    num_measure: int
    den_fact: int
    den_measure: int
    alt_den_fact: int
    alt_den_measure: int
    multiplier: int = 100
    denominator_swap: bool = True


@dataclass(frozen=True)
class IndustryPack:
    key: str
    label: str
    systems: tuple[SystemSpec, ...]
    domains: tuple[str, ...]
    backbone: tuple[str, ...]
    business_units: tuple[str, ...]
    facts: tuple[FactSpec, ...]
    dims: tuple[DimSpec, ...]
    reference_tables: tuple[str, ...]
    report_themes: tuple[str, ...]
    threshold_metric: str         # measure that carries the planted threshold drift
    threshold_column: str
    aging_unit: str = "days"
    ratios: tuple["RatioSpec", ...] = ()

    def system(self, index: int) -> SystemSpec:
        return self.systems[index % len(self.systems)]


# --------------------------------------------------------------------------
# Shared structural template
# --------------------------------------------------------------------------

# (schema slot, system slot, grain slot) for the eight fact tables. Grain slot is
# an index into the backbone, or the literal "Event"/"Transaction".
FACT_LAYOUT = (
    (0, 1),   # system 0, backbone[1]
    (1, 1),
    (1, 2),
    (0, 3),
    (2, 0),
    (3, 1),
    (0, "Event"),
    (4, 1),   # the sunset system
)

DIM_LAYOUT = (0, 1, 2, 3, 4)      # one dimension per backbone entity

STANDARD_ATTRIBUTES = (
    "status_code", "segment_code", "region_code", "start_date", "end_date",
    "created_by", "updated_at", "source_system_code",
)


def _facts(rows, backbone, schemas) -> tuple[FactSpec, ...]:
    out = []
    for i, (table, measures, dates) in enumerate(rows):
        system_index, grain_slot = FACT_LAYOUT[i]
        grain = backbone[grain_slot] if isinstance(grain_slot, int) else grain_slot
        out.append(FactSpec(
            table=table,
            schema=schemas[system_index],
            system_index=system_index,
            grain=grain,
            measures=tuple(Measure(*m) for m in measures),
            date_columns=tuple(dates),
        ))
    return tuple(out)


def _dims(rows, schemas) -> tuple[DimSpec, ...]:
    return tuple(
        DimSpec(table=table, schema=schemas[sysidx], system_index=sysidx,
                entity=entity, attributes=tuple(attrs) + STANDARD_ATTRIBUTES)
        for table, sysidx, entity, attrs in rows
    )


# --------------------------------------------------------------------------
# Industry packs
# --------------------------------------------------------------------------

def _utility() -> IndustryPack:
    backbone = ("Customer", "Account", "Premise", "Service Point", "Meter")
    systems = (
        SystemSpec("CIS", "Customer Information System", "core", True),
        SystemSpec("BILLING", "Billing and Payments", "transactional", True),
        SystemSpec("CRM", "Customer Relationship Management", "servicing", True),
        SystemSpec("ERP", "Enterprise Finance", "finance", True),
        SystemSpec("LEGACY_MDM", "Legacy Meter Data Mart", "legacy", False, "sunset"),
    )
    schemas = ("CIS_CORE", "BILLING", "CRM", "GL", "MDM_ARCHIVE")
    facts = _facts([
        ("FCT_ACCOUNT_BALANCE", [
            ("arrears_amount", "Arrears Balance", "SUM", "amount"),
            ("current_balance", "Current Balance", "SUM", "amount"),
            ("days_past_due", "Days Past Due", "AVG", "duration"),
            ("account_key", "Accounts in Arrears", "COUNT DISTINCT", "count"),
        ], ("bill_date", "read_date", "fiscal_period_end")),
        ("FCT_PAYMENT", [
            ("payment_amount", "Payment Amount", "SUM", "amount"),
            ("payment_count", "Payment Count", "COUNT", "count"),
            ("days_to_pay", "Days To Pay", "AVG", "duration"),
            ("failed_payment_amount", "Failed Payment Amount", "SUM", "amount"),
        ], ("payment_date", "posting_date", "fiscal_period_end")),
        ("FCT_DISCONNECT_NOTICE", [
            ("notice_count", "Disconnect Notices Issued", "COUNT", "count"),
            ("reconnect_count", "Reconnections", "COUNT", "count"),
            ("field_visit_cost", "Field Visit Cost", "SUM", "amount"),
            ("notice_to_disconnect_days", "Notice To Disconnect Days", "AVG", "duration"),
        ], ("notice_date", "scheduled_date", "fiscal_period_end")),
        ("FCT_METER_READ", [
            ("consumption_kwh", "Consumption kWh", "SUM", "amount"),
            ("estimated_read_flag", "Estimated Reads", "COUNT", "count"),
            ("read_exception_count", "Read Exceptions", "COUNT", "count"),
            ("peak_demand_kw", "Peak Demand kW", "MAX", "amount"),
        ], ("read_date", "schedule_date", "fiscal_period_end")),
        ("FCT_CUSTOMER_CONTACT", [
            ("contact_count", "Customer Contacts", "COUNT", "count"),
            ("handle_time_seconds", "Average Handle Time", "AVG", "duration"),
            ("first_contact_resolution_flag", "First Contact Resolution", "AVG", "rate"),
            ("complaint_count", "Complaints", "COUNT", "count"),
        ], ("contact_date", "resolution_date", "fiscal_period_end")),
        ("FCT_REVENUE_LEDGER", [
            ("billed_revenue", "Billed Revenue", "SUM", "amount"),
            ("total_revenue", "Total Revenue", "SUM", "amount"),
            ("write_off_amount", "Write Off Amount", "SUM", "amount"),
            ("unbilled_revenue", "Unbilled Revenue", "SUM", "amount"),
        ], ("posting_date", "bill_date", "fiscal_period_end")),
        ("FCT_OUTAGE_EVENT", [
            ("outage_minutes", "Outage Minutes", "SUM", "duration"),
            ("customers_affected", "Customers Affected", "SUM", "count"),
            ("restoration_minutes", "Restoration Minutes", "AVG", "duration"),
            ("event_count", "Outage Events", "COUNT", "count"),
        ], ("event_start_ts", "restored_ts", "fiscal_period_end")),
        ("FCT_LEGACY_ARREARS", [
            ("legacy_arrears_amount", "Legacy Arrears Balance", "SUM", "amount"),
            ("legacy_account_count", "Legacy Accounts", "COUNT DISTINCT", "count"),
            ("legacy_dso", "Legacy Days Sales Outstanding", "AVG", "duration"),
            ("legacy_write_off", "Legacy Write Off", "SUM", "amount"),
        ], ("snapshot_date", "bill_date", "fiscal_period_end")),
    ], backbone, schemas)
    dims = _dims([
        ("DIM_CUSTOMER", 0, "Customer", ("customer_name", "customer_segment", "credit_score", "email_address")),
        ("DIM_ACCOUNT", 0, "Account", ("account_number", "rate_class", "budget_billing_flag", "open_date")),
        ("DIM_PREMISE", 0, "Premise", ("premise_address", "service_territory", "dwelling_type")),
        ("DIM_SERVICE_POINT", 0, "Service Point", ("service_point_number", "voltage_class", "connection_status")),
        ("DIM_METER", 0, "Meter", ("meter_number", "meter_type", "install_date", "ami_flag")),
        ("DIM_RATE_PLAN", 1, "Rate Plan", ("rate_code", "tariff_name", "effective_date")),
        ("DIM_PAYMENT_ARRANGEMENT", 1, "Payment Arrangement", ("arrangement_type", "instalments", "default_flag")),
        ("DIM_OPERATING_COMPANY", 3, "Operating Company", ("opco_code", "opco_name", "regulator")),
        ("DIM_CALENDAR", 3, "Calendar", ("calendar_date", "fiscal_period", "fiscal_year", "calendar_month")),
    ], schemas)
    return IndustryPack(
        key="utility", label="Utility / Energy", systems=systems,
        domains=("Customer", "Billing & Collections", "Metering", "Network Operations",
                 "Finance", "Regulatory"),
        backbone=backbone,
        business_units=("Credit & Collections", "Finance", "Customer Operations",
                        "Metering Services", "Network Operations", "Regulatory Affairs",
                        "Executive"),
        facts=facts, dims=dims,
        reference_tables=("REF_AGING_BUCKET", "REF_DISCONNECT_REASON", "REF_RATE_CLASS"),
        report_themes=("Arrears Aging", "Collections Performance", "Payment Arrangement",
                       "Disconnect Notice", "Revenue Assurance", "Meter Read Exception",
                       "Consumption Analysis", "Outage Performance", "Customer Contact",
                       "Write Off", "Regulatory Filing", "Executive Scorecard"),
        threshold_metric="Days Past Due", threshold_column="days_past_due",
    )


def _banking() -> IndustryPack:
    backbone = ("Customer", "Account", "Product Holding", "Transaction", "Card")
    systems = (
        SystemSpec("CORE", "Core Banking Platform", "core", True),
        SystemSpec("PAYMENTS", "Payments Hub", "transactional", True),
        SystemSpec("CRM", "Relationship Management", "servicing", True),
        SystemSpec("FINRISK", "Finance and Risk Datamart", "finance", True),
        SystemSpec("LEGACY_LOANS", "Legacy Loan Servicing", "legacy", False, "sunset"),
    )
    schemas = ("CORE_BANK", "PAYMENTS", "CRM", "FINRISK", "LOANS_ARCHIVE")
    facts = _facts([
        ("FCT_LOAN_BALANCE", [
            ("outstanding_balance", "Outstanding Balance", "SUM", "amount"),
            ("delinquent_amount", "Delinquent Balance", "SUM", "amount"),
            ("days_past_due", "Days Past Due", "AVG", "duration"),
            ("account_key", "Delinquent Accounts", "COUNT DISTINCT", "count"),
        ], ("statement_date", "value_date", "fiscal_period_end")),
        ("FCT_PAYMENT_TXN", [
            ("payment_amount", "Payment Amount", "SUM", "amount"),
            ("txn_count", "Payment Transactions", "COUNT", "count"),
            ("failed_txn_amount", "Failed Payment Amount", "SUM", "amount"),
            ("settlement_lag_days", "Settlement Lag Days", "AVG", "duration"),
        ], ("txn_date", "settlement_date", "fiscal_period_end")),
        ("FCT_PRODUCT_HOLDING", [
            ("holding_balance", "Holding Balance", "SUM", "amount"),
            ("product_count", "Products Per Customer", "COUNT", "count"),
            ("cross_sell_flag", "Cross Sell Rate", "AVG", "rate"),
            ("attrition_flag", "Attrition Rate", "AVG", "rate"),
        ], ("open_date", "close_date", "fiscal_period_end")),
        ("FCT_CARD_ACTIVITY", [
            ("spend_amount", "Card Spend", "SUM", "amount"),
            ("interchange_revenue", "Interchange Revenue", "SUM", "amount"),
            ("dispute_count", "Card Disputes", "COUNT", "count"),
            ("active_card_count", "Active Cards", "COUNT DISTINCT", "count"),
        ], ("posting_date", "authorization_date", "fiscal_period_end")),
        ("FCT_CUSTOMER_INTERACTION", [
            ("interaction_count", "Customer Interactions", "COUNT", "count"),
            ("handle_time_seconds", "Average Handle Time", "AVG", "duration"),
            ("complaint_count", "Complaints", "COUNT", "count"),
            ("nps_score", "Net Promoter Score", "AVG", "rate"),
        ], ("interaction_date", "resolution_date", "fiscal_period_end")),
        ("FCT_GL_POSTING", [
            ("net_interest_income", "Net Interest Income", "SUM", "amount"),
            ("total_revenue", "Total Revenue", "SUM", "amount"),
            ("charge_off_amount", "Charge Off Amount", "SUM", "amount"),
            ("provision_amount", "Loan Loss Provision", "SUM", "amount"),
        ], ("posting_date", "value_date", "fiscal_period_end")),
        ("FCT_FRAUD_EVENT", [
            ("fraud_loss_amount", "Fraud Loss", "SUM", "amount"),
            ("alert_count", "Fraud Alerts", "COUNT", "count"),
            ("investigation_minutes", "Investigation Minutes", "AVG", "duration"),
            ("confirmed_fraud_count", "Confirmed Fraud Cases", "COUNT", "count"),
        ], ("event_ts", "closed_ts", "fiscal_period_end")),
        ("FCT_LEGACY_DELINQUENCY", [
            ("legacy_delinquent_amount", "Legacy Delinquent Balance", "SUM", "amount"),
            ("legacy_account_count", "Legacy Delinquent Accounts", "COUNT DISTINCT", "count"),
            ("legacy_roll_rate", "Legacy Roll Rate", "AVG", "rate"),
            ("legacy_charge_off", "Legacy Charge Off", "SUM", "amount"),
        ], ("snapshot_date", "statement_date", "fiscal_period_end")),
    ], backbone, schemas)
    dims = _dims([
        ("DIM_CUSTOMER", 0, "Customer", ("customer_name", "risk_segment", "kyc_status", "tax_identifier")),
        ("DIM_ACCOUNT", 0, "Account", ("account_number", "product_code", "open_date", "branch_code")),
        ("DIM_PRODUCT_HOLDING", 0, "Product Holding", ("holding_number", "product_family", "term_months")),
        ("DIM_TRANSACTION_TYPE", 1, "Transaction", ("txn_type_code", "channel", "scheme")),
        ("DIM_CARD", 1, "Card", ("card_number_masked", "card_type", "issue_date")),
        ("DIM_BRANCH", 2, "Branch", ("branch_code", "branch_name", "region")),
        ("DIM_RISK_GRADE", 3, "Risk Grade", ("grade_code", "pd_band", "basel_class")),
        ("DIM_LEGAL_ENTITY", 3, "Legal Entity", ("entity_code", "entity_name", "regulator")),
        ("DIM_CALENDAR", 3, "Calendar", ("calendar_date", "fiscal_period", "fiscal_year", "calendar_month")),
    ], schemas)
    return IndustryPack(
        key="banking", label="Banking", systems=systems,
        domains=("Customer", "Lending & Collections", "Payments", "Risk", "Finance", "Compliance"),
        backbone=backbone,
        business_units=("Collections", "Finance", "Retail Banking", "Risk Management",
                        "Payments Operations", "Compliance", "Executive"),
        facts=facts, dims=dims,
        reference_tables=("REF_DELINQUENCY_BUCKET", "REF_CHANNEL", "REF_PRODUCT_CODE"),
        report_themes=("Delinquency Aging", "Collections Performance", "Payment Volume",
                       "Card Spend", "Net Interest Margin", "Charge Off", "Fraud Loss",
                       "Customer Attrition", "Branch Performance", "Regulatory Return",
                       "Cross Sell", "Executive Scorecard"),
        threshold_metric="Days Past Due", threshold_column="days_past_due",
    )


def _insurance() -> IndustryPack:
    backbone = ("Customer", "Policy", "Coverage", "Claim", "Payment")
    systems = (
        SystemSpec("POLICY", "Policy Administration", "core", True),
        SystemSpec("CLAIMS", "Claims Management", "transactional", True),
        SystemSpec("CRM", "Distribution and Servicing", "servicing", True),
        SystemSpec("ACTUARIAL", "Actuarial and Finance Mart", "finance", True),
        SystemSpec("LEGACY_POL", "Legacy Policy Archive", "legacy", False, "sunset"),
    )
    schemas = ("POLICY_ADMIN", "CLAIMS", "DISTRIBUTION", "ACTUARIAL", "POLICY_ARCHIVE")
    facts = _facts([
        ("FCT_POLICY_PREMIUM", [
            ("written_premium", "Written Premium", "SUM", "amount"),
            ("earned_premium", "Earned Premium", "SUM", "amount"),
            ("policy_count", "In Force Policies", "COUNT DISTINCT", "count"),
            ("days_past_due", "Premium Days Past Due", "AVG", "duration"),
        ], ("effective_date", "accounting_date", "fiscal_period_end")),
        ("FCT_CLAIM_TRANSACTION", [
            ("paid_loss", "Paid Loss", "SUM", "amount"),
            ("case_reserve", "Case Reserve", "SUM", "amount"),
            ("claim_count", "Claim Count", "COUNT DISTINCT", "count"),
            ("cycle_time_days", "Claim Cycle Time", "AVG", "duration"),
        ], ("loss_date", "report_date", "fiscal_period_end")),
        ("FCT_COVERAGE_EXPOSURE", [
            ("sum_insured", "Sum Insured", "SUM", "amount"),
            ("exposure_units", "Exposure Units", "SUM", "count"),
            ("coverage_count", "Coverages", "COUNT", "count"),
            ("deductible_amount", "Average Deductible", "AVG", "amount"),
        ], ("effective_date", "expiry_date", "fiscal_period_end")),
        ("FCT_CLAIM_PAYMENT", [
            ("payment_amount", "Claim Payment Amount", "SUM", "amount"),
            ("recovery_amount", "Recovery Amount", "SUM", "amount"),
            ("payment_count", "Claim Payments", "COUNT", "count"),
            ("days_to_pay", "Days To Pay Claim", "AVG", "duration"),
        ], ("payment_date", "approval_date", "fiscal_period_end")),
        ("FCT_CUSTOMER_SERVICE", [
            ("contact_count", "Policyholder Contacts", "COUNT", "count"),
            ("handle_time_seconds", "Average Handle Time", "AVG", "duration"),
            ("complaint_count", "Complaints", "COUNT", "count"),
            ("retention_flag", "Retention Rate", "AVG", "rate"),
        ], ("contact_date", "resolution_date", "fiscal_period_end")),
        ("FCT_FINANCIAL_RESULT", [
            ("total_revenue", "Total Revenue", "SUM", "amount"),
            ("incurred_loss", "Incurred Loss", "SUM", "amount"),
            ("expense_amount", "Underwriting Expense", "SUM", "amount"),
            ("commission_amount", "Commission Amount", "SUM", "amount"),
        ], ("posting_date", "accounting_date", "fiscal_period_end")),
        ("FCT_FNOL_EVENT", [
            ("fnol_count", "First Notice Of Loss Events", "COUNT", "count"),
            ("triage_minutes", "Triage Minutes", "AVG", "duration"),
            ("fraud_flag_count", "Suspected Fraud Claims", "COUNT", "count"),
            ("straight_through_flag", "Straight Through Rate", "AVG", "rate"),
        ], ("notice_ts", "assigned_ts", "fiscal_period_end")),
        ("FCT_LEGACY_PREMIUM", [
            ("legacy_written_premium", "Legacy Written Premium", "SUM", "amount"),
            ("legacy_policy_count", "Legacy Policies", "COUNT DISTINCT", "count"),
            ("legacy_lapse_rate", "Legacy Lapse Rate", "AVG", "rate"),
            ("legacy_loss_ratio", "Legacy Loss Ratio", "AVG", "rate"),
        ], ("snapshot_date", "effective_date", "fiscal_period_end")),
    ], backbone, schemas)
    dims = _dims([
        ("DIM_CUSTOMER", 0, "Customer", ("policyholder_name", "segment", "date_of_birth", "national_id")),
        ("DIM_POLICY", 0, "Policy", ("policy_number", "product_line", "inception_date", "channel")),
        ("DIM_COVERAGE", 0, "Coverage", ("coverage_code", "peril", "limit_amount")),
        ("DIM_CLAIM", 1, "Claim", ("claim_number", "claim_status", "cause_of_loss")),
        ("DIM_PAYMENT", 1, "Payment", ("payment_reference", "payment_method", "payee_type")),
        ("DIM_AGENT", 2, "Agent", ("agent_code", "agency_name", "channel")),
        ("DIM_LINE_OF_BUSINESS", 3, "Line Of Business", ("lob_code", "lob_name", "regulator")),
        ("DIM_LEGAL_ENTITY", 3, "Legal Entity", ("entity_code", "entity_name", "territory")),
        ("DIM_CALENDAR", 3, "Calendar", ("calendar_date", "fiscal_period", "fiscal_year", "calendar_month")),
    ], schemas)
    return IndustryPack(
        key="insurance", label="Insurance", systems=systems,
        domains=("Customer", "Policy & Underwriting", "Claims", "Actuarial",
                 "Finance", "Regulatory"),
        backbone=backbone,
        business_units=("Claims Operations", "Finance", "Underwriting", "Actuarial",
                        "Distribution", "Compliance", "Executive"),
        facts=facts, dims=dims,
        reference_tables=("REF_CLAIM_STATUS", "REF_PERIL", "REF_AGING_BUCKET"),
        report_themes=("Claims Performance", "Loss Ratio", "Premium Production",
                       "Reserve Adequacy", "Policy Lapse", "Commission", "Fraud Detection",
                       "Complaint Handling", "Regulatory Return", "Agent Scorecard",
                       "Exposure Management", "Executive Scorecard"),
        threshold_metric="Claim Cycle Time", threshold_column="cycle_time_days",
    )


def _retail() -> IndustryPack:
    backbone = ("Customer", "Order", "Store", "Product", "Shipment")
    systems = (
        SystemSpec("ECOM", "Ecommerce Platform", "core", True),
        SystemSpec("POS", "Point Of Sale", "transactional", True),
        SystemSpec("CRM", "Loyalty and Marketing", "servicing", True),
        SystemSpec("ERP", "Merchandising and Finance", "finance", True),
        SystemSpec("LEGACY_DW", "Legacy Sales Warehouse", "legacy", False, "sunset"),
    )
    schemas = ("ECOM", "POS", "LOYALTY", "MERCH", "SALES_ARCHIVE")
    facts = _facts([
        ("FCT_ORDER_LINE", [
            ("net_sales_amount", "Net Sales", "SUM", "amount"),
            ("gross_sales_amount", "Gross Sales", "SUM", "amount"),
            ("units_sold", "Units Sold", "SUM", "count"),
            ("discount_amount", "Discount Amount", "SUM", "amount"),
        ], ("order_date", "ship_date", "fiscal_period_end")),
        ("FCT_POS_TRANSACTION", [
            ("transaction_amount", "Store Sales", "SUM", "amount"),
            ("basket_size", "Average Basket Size", "AVG", "amount"),
            ("transaction_count", "Transactions", "COUNT", "count"),
            ("return_amount", "Returns Amount", "SUM", "amount"),
        ], ("transaction_date", "posting_date", "fiscal_period_end")),
        ("FCT_STORE_INVENTORY", [
            ("on_hand_units", "On Hand Units", "SUM", "count"),
            ("stockout_flag", "Stockout Rate", "AVG", "rate"),
            ("shrink_amount", "Shrink Amount", "SUM", "amount"),
            ("days_of_supply", "Days Of Supply", "AVG", "duration"),
        ], ("snapshot_date", "receipt_date", "fiscal_period_end")),
        ("FCT_PRODUCT_MARGIN", [
            ("gross_margin_amount", "Gross Margin", "SUM", "amount"),
            ("cost_of_goods", "Cost Of Goods Sold", "SUM", "amount"),
            ("markdown_amount", "Markdown Amount", "SUM", "amount"),
            ("sku_count", "Active SKUs", "COUNT DISTINCT", "count"),
        ], ("effective_date", "end_date", "fiscal_period_end")),
        ("FCT_CUSTOMER_LOYALTY", [
            ("loyalty_points", "Loyalty Points Issued", "SUM", "count"),
            ("redemption_amount", "Redemption Amount", "SUM", "amount"),
            ("active_member_count", "Active Members", "COUNT DISTINCT", "count"),
            ("churn_flag", "Churn Rate", "AVG", "rate"),
        ], ("enrolment_date", "last_activity_date", "fiscal_period_end")),
        ("FCT_FINANCE_LEDGER", [
            ("total_revenue", "Total Revenue", "SUM", "amount"),
            ("net_revenue", "Net Revenue", "SUM", "amount"),
            ("operating_expense", "Operating Expense", "SUM", "amount"),
            ("ebitda_amount", "EBITDA", "SUM", "amount"),
        ], ("posting_date", "close_date", "fiscal_period_end")),
        ("FCT_CLICKSTREAM_EVENT", [
            ("session_count", "Sessions", "COUNT", "count"),
            ("conversion_flag", "Conversion Rate", "AVG", "rate"),
            ("dwell_seconds", "Average Dwell Time", "AVG", "duration"),
            ("cart_abandon_count", "Cart Abandonments", "COUNT", "count"),
        ], ("event_ts", "session_end_ts", "fiscal_period_end")),
        ("FCT_LEGACY_SALES", [
            ("legacy_sales_amount", "Legacy Sales", "SUM", "amount"),
            ("legacy_units", "Legacy Units", "SUM", "count"),
            ("legacy_margin_rate", "Legacy Margin Rate", "AVG", "rate"),
            ("legacy_return_amount", "Legacy Returns", "SUM", "amount"),
        ], ("snapshot_date", "order_date", "fiscal_period_end")),
    ], backbone, schemas)
    dims = _dims([
        ("DIM_CUSTOMER", 0, "Customer", ("customer_name", "loyalty_tier", "email_address", "birth_date")),
        ("DIM_ORDER", 0, "Order", ("order_number", "channel", "order_status")),
        ("DIM_STORE", 1, "Store", ("store_number", "store_format", "region", "open_date")),
        ("DIM_PRODUCT", 3, "Product", ("sku", "product_name", "category", "brand")),
        ("DIM_SHIPMENT", 0, "Shipment", ("shipment_number", "carrier", "service_level")),
        ("DIM_PROMOTION", 2, "Promotion", ("promotion_code", "promotion_type", "start_date")),
        ("DIM_SUPPLIER", 3, "Supplier", ("supplier_code", "supplier_name", "country")),
        ("DIM_LEGAL_ENTITY", 3, "Legal Entity", ("entity_code", "entity_name", "currency")),
        ("DIM_CALENDAR", 3, "Calendar", ("calendar_date", "fiscal_period", "fiscal_year", "calendar_month")),
    ], schemas)
    return IndustryPack(
        key="retail", label="Retail / CPG", systems=systems,
        domains=("Customer", "Sales & Orders", "Merchandising", "Supply Chain",
                 "Finance", "Marketing"),
        backbone=backbone,
        business_units=("Merchandising", "Finance", "Store Operations", "Ecommerce",
                        "Supply Chain", "Marketing", "Executive"),
        reference_tables=("REF_CATEGORY", "REF_CHANNEL", "REF_RETURN_REASON"),
        facts=facts, dims=dims,
        report_themes=("Sales Performance", "Margin Analysis", "Inventory Position",
                       "Markdown Effectiveness", "Loyalty Engagement", "Returns",
                       "Basket Analysis", "Supplier Scorecard", "Ecommerce Conversion",
                       "Store Ranking", "Promotion Lift", "Executive Scorecard"),
        threshold_metric="Days Of Supply", threshold_column="days_of_supply",
    )


def _healthcare() -> IndustryPack:
    backbone = ("Patient", "Encounter", "Claim", "Provider", "Facility")
    systems = (
        SystemSpec("EHR", "Electronic Health Record", "core", True),
        SystemSpec("BILLING", "Revenue Cycle Billing", "transactional", True),
        SystemSpec("CRM", "Patient Engagement", "servicing", True),
        SystemSpec("ERP", "Finance and Supply", "finance", True),
        SystemSpec("LEGACY_HIS", "Legacy Hospital Information System", "legacy", False, "sunset"),
    )
    schemas = ("CLINICAL", "REVENUE_CYCLE", "ENGAGEMENT", "FINANCE", "HIS_ARCHIVE")
    facts = _facts([
        ("FCT_ENCOUNTER", [
            ("length_of_stay_days", "Length Of Stay", "AVG", "duration"),
            ("encounter_count", "Encounters", "COUNT DISTINCT", "count"),
            ("readmission_flag", "Readmission Rate", "AVG", "rate"),
            ("case_mix_index", "Case Mix Index", "AVG", "rate"),
        ], ("admit_date", "discharge_date", "fiscal_period_end")),
        ("FCT_CLAIM_BILLING", [
            ("charge_amount", "Gross Charges", "SUM", "amount"),
            ("allowed_amount", "Allowed Amount", "SUM", "amount"),
            ("denied_amount", "Denied Amount", "SUM", "amount"),
            ("days_in_ar", "Days In Accounts Receivable", "AVG", "duration"),
        ], ("service_date", "bill_date", "fiscal_period_end")),
        ("FCT_CLAIM_ADJUDICATION", [
            ("paid_amount", "Payer Paid Amount", "SUM", "amount"),
            ("patient_responsibility", "Patient Responsibility", "SUM", "amount"),
            ("denial_count", "Denials", "COUNT", "count"),
            ("appeal_success_flag", "Appeal Success Rate", "AVG", "rate"),
        ], ("adjudication_date", "remit_date", "fiscal_period_end")),
        ("FCT_PROVIDER_ACTIVITY", [
            ("rvu_total", "Work RVUs", "SUM", "count"),
            ("procedure_count", "Procedures", "COUNT", "count"),
            ("panel_size", "Panel Size", "COUNT DISTINCT", "count"),
            ("documentation_lag_days", "Documentation Lag", "AVG", "duration"),
        ], ("service_date", "signed_date", "fiscal_period_end")),
        ("FCT_PATIENT_OUTREACH", [
            ("outreach_count", "Outreach Contacts", "COUNT", "count"),
            ("no_show_flag", "No Show Rate", "AVG", "rate"),
            ("satisfaction_score", "Patient Satisfaction", "AVG", "rate"),
            ("appointment_count", "Appointments", "COUNT", "count"),
        ], ("contact_date", "appointment_date", "fiscal_period_end")),
        ("FCT_FINANCIAL_LEDGER", [
            ("net_patient_revenue", "Net Patient Revenue", "SUM", "amount"),
            ("total_revenue", "Total Revenue", "SUM", "amount"),
            ("supply_expense", "Supply Expense", "SUM", "amount"),
            ("bad_debt_amount", "Bad Debt", "SUM", "amount"),
        ], ("posting_date", "close_date", "fiscal_period_end")),
        ("FCT_CLINICAL_EVENT", [
            ("event_count", "Clinical Events", "COUNT", "count"),
            ("response_minutes", "Response Minutes", "AVG", "duration"),
            ("adverse_event_count", "Adverse Events", "COUNT", "count"),
            ("protocol_adherence_flag", "Protocol Adherence", "AVG", "rate"),
        ], ("event_ts", "resolved_ts", "fiscal_period_end")),
        ("FCT_LEGACY_AR", [
            ("legacy_ar_balance", "Legacy AR Balance", "SUM", "amount"),
            ("legacy_claim_count", "Legacy Claims", "COUNT DISTINCT", "count"),
            ("legacy_denial_rate", "Legacy Denial Rate", "AVG", "rate"),
            ("legacy_days_in_ar", "Legacy Days In AR", "AVG", "duration"),
        ], ("snapshot_date", "service_date", "fiscal_period_end")),
    ], backbone, schemas)
    dims = _dims([
        ("DIM_PATIENT", 0, "Patient", ("patient_name", "date_of_birth", "medical_record_number", "insurance_id")),
        ("DIM_ENCOUNTER", 0, "Encounter", ("encounter_number", "encounter_type", "admit_source")),
        ("DIM_CLAIM", 1, "Claim", ("claim_number", "claim_status", "payer_code")),
        ("DIM_PROVIDER", 0, "Provider", ("provider_npi", "provider_name", "specialty")),
        ("DIM_FACILITY", 0, "Facility", ("facility_code", "facility_name", "bed_count")),
        ("DIM_PAYER", 1, "Payer", ("payer_code", "payer_name", "contract_type")),
        ("DIM_DIAGNOSIS", 3, "Diagnosis", ("icd_code", "diagnosis_name", "drg_code")),
        ("DIM_LEGAL_ENTITY", 3, "Legal Entity", ("entity_code", "entity_name", "region")),
        ("DIM_CALENDAR", 3, "Calendar", ("calendar_date", "fiscal_period", "fiscal_year", "calendar_month")),
    ], schemas)
    return IndustryPack(
        key="healthcare", label="Healthcare", systems=systems,
        domains=("Patient", "Revenue Cycle", "Clinical Operations", "Provider",
                 "Finance", "Quality & Compliance"),
        backbone=backbone,
        business_units=("Revenue Cycle", "Finance", "Clinical Operations", "Quality",
                        "Patient Access", "Compliance", "Executive"),
        facts=facts, dims=dims,
        reference_tables=("REF_DENIAL_REASON", "REF_AGING_BUCKET", "REF_SERVICE_LINE"),
        report_themes=("Accounts Receivable Aging", "Denial Management", "Length Of Stay",
                       "Readmission", "Provider Productivity", "Patient Access",
                       "Revenue Cycle Performance", "Quality Measure", "Supply Cost",
                       "Regulatory Submission", "Patient Satisfaction", "Executive Scorecard"),
        threshold_metric="Days In Accounts Receivable", threshold_column="days_in_ar",
    )


def _manufacturing() -> IndustryPack:
    backbone = ("Customer", "Order", "Plant", "Work Order", "Asset")
    systems = (
        SystemSpec("ERP", "Enterprise Resource Planning", "core", True),
        SystemSpec("MES", "Manufacturing Execution System", "transactional", True),
        SystemSpec("CRM", "Sales and Service", "servicing", True),
        SystemSpec("FINANCE", "Corporate Finance", "finance", True),
        SystemSpec("LEGACY_QMS", "Legacy Quality Mart", "legacy", False, "sunset"),
    )
    schemas = ("ERP_CORE", "MES", "SALES", "FINANCE", "QMS_ARCHIVE")
    facts = _facts([
        ("FCT_SALES_ORDER", [
            ("order_amount", "Order Value", "SUM", "amount"),
            ("order_quantity", "Order Quantity", "SUM", "count"),
            ("on_time_flag", "On Time Delivery Rate", "AVG", "rate"),
            ("lead_time_days", "Order Lead Time", "AVG", "duration"),
        ], ("order_date", "promised_date", "fiscal_period_end")),
        ("FCT_PRODUCTION_RUN", [
            ("produced_quantity", "Units Produced", "SUM", "count"),
            ("scrap_quantity", "Scrap Quantity", "SUM", "count"),
            ("cycle_time_minutes", "Cycle Time", "AVG", "duration"),
            ("yield_rate", "First Pass Yield", "AVG", "rate"),
        ], ("run_start_date", "run_end_date", "fiscal_period_end")),
        ("FCT_PLANT_UTILIZATION", [
            ("run_minutes", "Run Minutes", "SUM", "duration"),
            ("downtime_minutes", "Downtime Minutes", "SUM", "duration"),
            ("oee_rate", "Overall Equipment Effectiveness", "AVG", "rate"),
            ("shift_count", "Shifts Operated", "COUNT", "count"),
        ], ("shift_date", "snapshot_date", "fiscal_period_end")),
        ("FCT_WORK_ORDER", [
            ("labour_hours", "Labour Hours", "SUM", "duration"),
            ("maintenance_cost", "Maintenance Cost", "SUM", "amount"),
            ("work_order_count", "Work Orders", "COUNT DISTINCT", "count"),
            ("mean_time_to_repair", "Mean Time To Repair", "AVG", "duration"),
        ], ("open_date", "close_date", "fiscal_period_end")),
        ("FCT_CUSTOMER_SERVICE", [
            ("case_count", "Service Cases", "COUNT", "count"),
            ("warranty_cost", "Warranty Cost", "SUM", "amount"),
            ("resolution_hours", "Resolution Hours", "AVG", "duration"),
            ("nps_score", "Net Promoter Score", "AVG", "rate"),
        ], ("case_open_date", "case_close_date", "fiscal_period_end")),
        ("FCT_COST_LEDGER", [
            ("total_revenue", "Total Revenue", "SUM", "amount"),
            ("cost_of_goods", "Cost Of Goods Sold", "SUM", "amount"),
            ("material_cost", "Material Cost", "SUM", "amount"),
            ("gross_margin_amount", "Gross Margin", "SUM", "amount"),
        ], ("posting_date", "close_date", "fiscal_period_end")),
        ("FCT_SENSOR_EVENT", [
            ("alarm_count", "Machine Alarms", "COUNT", "count"),
            ("temperature_reading", "Average Temperature", "AVG", "rate"),
            ("vibration_exceedance_count", "Vibration Exceedances", "COUNT", "count"),
            ("uptime_seconds", "Uptime Seconds", "SUM", "duration"),
        ], ("event_ts", "cleared_ts", "fiscal_period_end")),
        ("FCT_LEGACY_QUALITY", [
            ("legacy_defect_count", "Legacy Defect Count", "COUNT", "count"),
            ("legacy_scrap_cost", "Legacy Scrap Cost", "SUM", "amount"),
            ("legacy_yield_rate", "Legacy Yield Rate", "AVG", "rate"),
            ("legacy_rework_hours", "Legacy Rework Hours", "SUM", "duration"),
        ], ("snapshot_date", "run_start_date", "fiscal_period_end")),
    ], backbone, schemas)
    dims = _dims([
        ("DIM_CUSTOMER", 2, "Customer", ("customer_name", "industry_segment", "credit_terms", "contact_email")),
        ("DIM_ORDER", 0, "Order", ("order_number", "order_type", "incoterms")),
        ("DIM_PLANT", 0, "Plant", ("plant_code", "plant_name", "country")),
        ("DIM_WORK_ORDER", 1, "Work Order", ("work_order_number", "work_order_type", "priority")),
        ("DIM_ASSET", 1, "Asset", ("asset_number", "asset_class", "install_date")),
        ("DIM_MATERIAL", 0, "Material", ("material_number", "material_group", "unit_of_measure")),
        ("DIM_SUPPLIER", 0, "Supplier", ("supplier_code", "supplier_name", "country")),
        ("DIM_LEGAL_ENTITY", 3, "Legal Entity", ("entity_code", "entity_name", "currency")),
        ("DIM_CALENDAR", 3, "Calendar", ("calendar_date", "fiscal_period", "fiscal_year", "calendar_month")),
    ], schemas)
    return IndustryPack(
        key="manufacturing", label="Manufacturing", systems=systems,
        domains=("Customer", "Sales & Orders", "Production", "Maintenance",
                 "Finance", "Quality"),
        backbone=backbone,
        business_units=("Plant Operations", "Finance", "Sales", "Maintenance",
                        "Quality", "Supply Chain", "Executive"),
        facts=facts, dims=dims,
        reference_tables=("REF_DOWNTIME_REASON", "REF_DEFECT_CODE", "REF_SHIFT"),
        report_themes=("Production Performance", "Yield And Scrap", "Downtime Analysis",
                       "On Time Delivery", "Maintenance Cost", "Warranty",
                       "Order Backlog", "Plant Scorecard", "Material Cost",
                       "Quality Exception", "Supplier Performance", "Executive Scorecard"),
        threshold_metric="Mean Time To Repair", threshold_column="mean_time_to_repair",
    )


def _telecom() -> IndustryPack:
    backbone = ("Customer", "Account", "Subscription", "Device", "Network Element")
    systems = (
        SystemSpec("BSS", "Business Support System", "core", True),
        SystemSpec("BILLING", "Convergent Billing", "transactional", True),
        SystemSpec("CRM", "Care and Retention", "servicing", True),
        SystemSpec("ERP", "Corporate Finance", "finance", True),
        SystemSpec("LEGACY_OSS", "Legacy Operations Support Mart", "legacy", False, "sunset"),
    )
    schemas = ("BSS_CORE", "BILLING", "CARE", "FINANCE", "OSS_ARCHIVE")
    facts = _facts([
        ("FCT_SUBSCRIPTION_BALANCE", [
            ("overdue_amount", "Overdue Balance", "SUM", "amount"),
            ("current_balance", "Current Balance", "SUM", "amount"),
            ("days_past_due", "Days Past Due", "AVG", "duration"),
            ("account_key", "Accounts Overdue", "COUNT DISTINCT", "count"),
        ], ("bill_date", "cycle_date", "fiscal_period_end")),
        ("FCT_BILLING_EVENT", [
            ("billed_amount", "Billed Amount", "SUM", "amount"),
            ("adjustment_amount", "Billing Adjustments", "SUM", "amount"),
            ("invoice_count", "Invoices", "COUNT", "count"),
            ("dispute_count", "Billing Disputes", "COUNT", "count"),
        ], ("bill_date", "posting_date", "fiscal_period_end")),
        ("FCT_SUBSCRIPTION_LIFECYCLE", [
            ("activation_count", "Activations", "COUNT", "count"),
            ("churn_flag", "Churn Rate", "AVG", "rate"),
            ("arpu_amount", "Average Revenue Per User", "AVG", "amount"),
            ("tenure_days", "Subscription Tenure", "AVG", "duration"),
        ], ("activation_date", "deactivation_date", "fiscal_period_end")),
        ("FCT_DEVICE_USAGE", [
            ("data_volume_gb", "Data Volume GB", "SUM", "amount"),
            ("voice_minutes", "Voice Minutes", "SUM", "duration"),
            ("sms_count", "SMS Count", "COUNT", "count"),
            ("roaming_amount", "Roaming Revenue", "SUM", "amount"),
        ], ("usage_date", "rating_date", "fiscal_period_end")),
        ("FCT_CARE_INTERACTION", [
            ("contact_count", "Care Contacts", "COUNT", "count"),
            ("handle_time_seconds", "Average Handle Time", "AVG", "duration"),
            ("first_contact_resolution_flag", "First Contact Resolution", "AVG", "rate"),
            ("complaint_count", "Complaints", "COUNT", "count"),
        ], ("contact_date", "resolution_date", "fiscal_period_end")),
        ("FCT_REVENUE_LEDGER", [
            ("service_revenue", "Service Revenue", "SUM", "amount"),
            ("total_revenue", "Total Revenue", "SUM", "amount"),
            ("bad_debt_amount", "Bad Debt", "SUM", "amount"),
            ("subscriber_acquisition_cost", "Subscriber Acquisition Cost", "SUM", "amount"),
        ], ("posting_date", "close_date", "fiscal_period_end")),
        ("FCT_NETWORK_EVENT", [
            ("outage_minutes", "Network Outage Minutes", "SUM", "duration"),
            ("dropped_call_count", "Dropped Calls", "COUNT", "count"),
            ("customers_affected", "Customers Affected", "SUM", "count"),
            ("latency_ms", "Average Latency", "AVG", "rate"),
        ], ("event_ts", "restored_ts", "fiscal_period_end")),
        ("FCT_LEGACY_OVERDUE", [
            ("legacy_overdue_amount", "Legacy Overdue Balance", "SUM", "amount"),
            ("legacy_account_count", "Legacy Overdue Accounts", "COUNT DISTINCT", "count"),
            ("legacy_churn_rate", "Legacy Churn Rate", "AVG", "rate"),
            ("legacy_write_off", "Legacy Write Off", "SUM", "amount"),
        ], ("snapshot_date", "bill_date", "fiscal_period_end")),
    ], backbone, schemas)
    dims = _dims([
        ("DIM_CUSTOMER", 0, "Customer", ("customer_name", "segment", "national_id", "email_address")),
        ("DIM_ACCOUNT", 0, "Account", ("account_number", "billing_cycle", "credit_class")),
        ("DIM_SUBSCRIPTION", 0, "Subscription", ("msisdn", "plan_code", "contract_end_date")),
        ("DIM_DEVICE", 0, "Device", ("imei", "device_model", "device_type")),
        ("DIM_NETWORK_ELEMENT", 0, "Network Element", ("cell_id", "technology", "region")),
        ("DIM_PLAN", 1, "Plan", ("plan_code", "plan_name", "data_allowance")),
        ("DIM_CHANNEL", 2, "Channel", ("channel_code", "channel_name", "partner")),
        ("DIM_LEGAL_ENTITY", 3, "Legal Entity", ("entity_code", "entity_name", "market")),
        ("DIM_CALENDAR", 3, "Calendar", ("calendar_date", "fiscal_period", "fiscal_year", "calendar_month")),
    ], schemas)
    return IndustryPack(
        key="telecom", label="Telecom", systems=systems,
        domains=("Customer", "Billing & Collections", "Network", "Care",
                 "Finance", "Regulatory"),
        backbone=backbone,
        business_units=("Credit & Collections", "Finance", "Customer Care",
                        "Network Operations", "Marketing", "Regulatory Affairs", "Executive"),
        facts=facts, dims=dims,
        reference_tables=("REF_AGING_BUCKET", "REF_CHURN_REASON", "REF_TECHNOLOGY"),
        report_themes=("Overdue Aging", "Collections Performance", "Churn Analysis",
                       "ARPU Trend", "Network Quality", "Care Performance",
                       "Billing Dispute", "Revenue Assurance", "Device Upgrade",
                       "Regulatory Filing", "Roaming Revenue", "Executive Scorecard"),
        threshold_metric="Days Past Due", threshold_column="days_past_due",
    )


def _public_sector() -> IndustryPack:
    backbone = ("Citizen", "Case", "Programme", "Payment", "Facility")
    systems = (
        SystemSpec("CMS", "Case Management System", "core", True),
        SystemSpec("PAYMENTS", "Benefit Payments", "transactional", True),
        SystemSpec("CRM", "Citizen Contact", "servicing", True),
        SystemSpec("ERP", "Public Finance", "finance", True),
        SystemSpec("LEGACY_GRANTS", "Legacy Grants Mart", "legacy", False, "sunset"),
    )
    schemas = ("CASEWORK", "PAYMENTS", "CONTACT", "FINANCE", "GRANTS_ARCHIVE")
    facts = _facts([
        ("FCT_CASE_STATUS", [
            ("open_case_count", "Open Cases", "COUNT DISTINCT", "count"),
            ("backlog_amount", "Backlog Value", "SUM", "amount"),
            ("days_open", "Days Case Open", "AVG", "duration"),
            ("overdue_case_count", "Overdue Cases", "COUNT", "count"),
        ], ("case_open_date", "target_date", "fiscal_period_end")),
        ("FCT_BENEFIT_PAYMENT", [
            ("payment_amount", "Benefit Payment Amount", "SUM", "amount"),
            ("payment_count", "Benefit Payments", "COUNT", "count"),
            ("overpayment_amount", "Overpayment Amount", "SUM", "amount"),
            ("days_to_pay", "Days To Payment", "AVG", "duration"),
        ], ("payment_date", "approval_date", "fiscal_period_end")),
        ("FCT_PROGRAMME_DELIVERY", [
            ("participant_count", "Programme Participants", "COUNT DISTINCT", "count"),
            ("completion_flag", "Completion Rate", "AVG", "rate"),
            ("programme_cost", "Programme Cost", "SUM", "amount"),
            ("waiting_days", "Waiting Days", "AVG", "duration"),
        ], ("enrolment_date", "exit_date", "fiscal_period_end")),
        ("FCT_INSPECTION", [
            ("inspection_count", "Inspections Completed", "COUNT", "count"),
            ("violation_count", "Violations Found", "COUNT", "count"),
            ("penalty_amount", "Penalty Amount", "SUM", "amount"),
            ("inspection_hours", "Inspection Hours", "AVG", "duration"),
        ], ("inspection_date", "report_date", "fiscal_period_end")),
        ("FCT_CITIZEN_CONTACT", [
            ("contact_count", "Citizen Contacts", "COUNT", "count"),
            ("handle_time_seconds", "Average Handle Time", "AVG", "duration"),
            ("complaint_count", "Complaints", "COUNT", "count"),
            ("satisfaction_score", "Citizen Satisfaction", "AVG", "rate"),
        ], ("contact_date", "resolution_date", "fiscal_period_end")),
        ("FCT_BUDGET_LEDGER", [
            ("budget_amount", "Budget Amount", "SUM", "amount"),
            ("total_revenue", "Total Appropriation", "SUM", "amount"),
            ("expenditure_amount", "Expenditure", "SUM", "amount"),
            ("grant_amount", "Grant Amount", "SUM", "amount"),
        ], ("posting_date", "close_date", "fiscal_period_end")),
        ("FCT_SERVICE_EVENT", [
            ("service_request_count", "Service Requests", "COUNT", "count"),
            ("response_minutes", "Response Minutes", "AVG", "duration"),
            ("escalation_count", "Escalations", "COUNT", "count"),
            ("sla_met_flag", "SLA Met Rate", "AVG", "rate"),
        ], ("request_ts", "closed_ts", "fiscal_period_end")),
        ("FCT_LEGACY_GRANT", [
            ("legacy_grant_amount", "Legacy Grant Amount", "SUM", "amount"),
            ("legacy_recipient_count", "Legacy Recipients", "COUNT DISTINCT", "count"),
            ("legacy_overpayment_rate", "Legacy Overpayment Rate", "AVG", "rate"),
            ("legacy_days_to_pay", "Legacy Days To Payment", "AVG", "duration"),
        ], ("snapshot_date", "payment_date", "fiscal_period_end")),
    ], backbone, schemas)
    dims = _dims([
        ("DIM_CITIZEN", 0, "Citizen", ("citizen_name", "national_insurance_number", "date_of_birth", "postcode")),
        ("DIM_CASE", 0, "Case", ("case_reference", "case_type", "priority")),
        ("DIM_PROGRAMME", 0, "Programme", ("programme_code", "programme_name", "funding_stream")),
        ("DIM_PAYMENT", 1, "Payment", ("payment_reference", "payment_method", "payee_type")),
        ("DIM_FACILITY", 0, "Facility", ("facility_code", "facility_name", "local_authority")),
        ("DIM_CASEWORKER", 2, "Caseworker", ("staff_number", "team", "grade")),
        ("DIM_LOCAL_AUTHORITY", 3, "Local Authority", ("authority_code", "authority_name", "region")),
        ("DIM_LEGAL_ENTITY", 3, "Legal Entity", ("entity_code", "entity_name", "department")),
        ("DIM_CALENDAR", 3, "Calendar", ("calendar_date", "fiscal_period", "fiscal_year", "calendar_month")),
    ], schemas)
    return IndustryPack(
        key="public_sector", label="Public Sector", systems=systems,
        domains=("Citizen", "Casework", "Benefits & Payments", "Programmes",
                 "Finance", "Regulatory"),
        backbone=backbone,
        business_units=("Casework Operations", "Finance", "Benefits Delivery",
                        "Programme Management", "Contact Centre", "Audit & Assurance",
                        "Executive"),
        facts=facts, dims=dims,
        reference_tables=("REF_CASE_STATUS", "REF_AGING_BUCKET", "REF_PAYMENT_REASON"),
        report_themes=("Case Backlog", "Payment Timeliness", "Overpayment Recovery",
                       "Programme Outcomes", "Inspection Compliance", "Contact Centre",
                       "Budget Execution", "Grant Administration", "Service Level",
                       "Statutory Return", "Waiting Times", "Executive Scorecard"),
        threshold_metric="Days Case Open", threshold_column="days_open",
    )


def _generic() -> IndustryPack:
    backbone = ("Customer", "Account", "Order", "Product", "Location")
    systems = (
        SystemSpec("CORE", "Core Operations Platform", "core", True),
        SystemSpec("TRANSACT", "Transaction Processing", "transactional", True),
        SystemSpec("CRM", "Customer Management", "servicing", True),
        SystemSpec("ERP", "Enterprise Finance", "finance", True),
        SystemSpec("LEGACY_DM", "Legacy Data Mart", "legacy", False, "sunset"),
    )
    schemas = ("CORE_OPS", "TRANSACT", "CRM", "FINANCE", "LEGACY")
    facts = _facts([
        ("FCT_ACCOUNT_BALANCE", [
            ("overdue_amount", "Overdue Balance", "SUM", "amount"),
            ("current_balance", "Current Balance", "SUM", "amount"),
            ("days_past_due", "Days Past Due", "AVG", "duration"),
            ("account_key", "Overdue Accounts", "COUNT DISTINCT", "count"),
        ], ("statement_date", "value_date", "fiscal_period_end")),
        ("FCT_TRANSACTION", [
            ("transaction_amount", "Transaction Amount", "SUM", "amount"),
            ("transaction_count", "Transaction Count", "COUNT", "count"),
            ("failed_amount", "Failed Transaction Amount", "SUM", "amount"),
            ("processing_days", "Processing Days", "AVG", "duration"),
        ], ("transaction_date", "posting_date", "fiscal_period_end")),
        ("FCT_ORDER", [
            ("order_amount", "Order Value", "SUM", "amount"),
            ("order_count", "Orders", "COUNT DISTINCT", "count"),
            ("fulfilment_days", "Fulfilment Days", "AVG", "duration"),
            ("cancellation_flag", "Cancellation Rate", "AVG", "rate"),
        ], ("order_date", "delivery_date", "fiscal_period_end")),
        ("FCT_PRODUCT_PERFORMANCE", [
            ("units_sold", "Units Sold", "SUM", "count"),
            ("product_revenue", "Product Revenue", "SUM", "amount"),
            ("return_amount", "Returns Amount", "SUM", "amount"),
            ("active_product_count", "Active Products", "COUNT DISTINCT", "count"),
        ], ("effective_date", "end_date", "fiscal_period_end")),
        ("FCT_CUSTOMER_CONTACT", [
            ("contact_count", "Customer Contacts", "COUNT", "count"),
            ("handle_time_seconds", "Average Handle Time", "AVG", "duration"),
            ("complaint_count", "Complaints", "COUNT", "count"),
            ("satisfaction_score", "Customer Satisfaction", "AVG", "rate"),
        ], ("contact_date", "resolution_date", "fiscal_period_end")),
        ("FCT_GENERAL_LEDGER", [
            ("billed_revenue", "Billed Revenue", "SUM", "amount"),
            ("total_revenue", "Total Revenue", "SUM", "amount"),
            ("write_off_amount", "Write Off Amount", "SUM", "amount"),
            ("operating_expense", "Operating Expense", "SUM", "amount"),
        ], ("posting_date", "close_date", "fiscal_period_end")),
        ("FCT_SYSTEM_EVENT", [
            ("event_count", "System Events", "COUNT", "count"),
            ("response_minutes", "Response Minutes", "AVG", "duration"),
            ("incident_count", "Incidents", "COUNT", "count"),
            ("uptime_seconds", "Uptime Seconds", "SUM", "duration"),
        ], ("event_ts", "resolved_ts", "fiscal_period_end")),
        ("FCT_LEGACY_BALANCE", [
            ("legacy_overdue_amount", "Legacy Overdue Balance", "SUM", "amount"),
            ("legacy_account_count", "Legacy Overdue Accounts", "COUNT DISTINCT", "count"),
            ("legacy_dso", "Legacy Days Sales Outstanding", "AVG", "duration"),
            ("legacy_write_off", "Legacy Write Off", "SUM", "amount"),
        ], ("snapshot_date", "statement_date", "fiscal_period_end")),
    ], backbone, schemas)
    dims = _dims([
        ("DIM_CUSTOMER", 0, "Customer", ("customer_name", "customer_segment", "email_address", "tax_identifier")),
        ("DIM_ACCOUNT", 0, "Account", ("account_number", "account_type", "open_date")),
        ("DIM_ORDER", 1, "Order", ("order_number", "channel", "order_status")),
        ("DIM_PRODUCT", 3, "Product", ("product_code", "product_name", "category")),
        ("DIM_LOCATION", 0, "Location", ("location_code", "location_name", "region")),
        ("DIM_CHANNEL", 2, "Channel", ("channel_code", "channel_name", "partner")),
        ("DIM_EMPLOYEE", 2, "Employee", ("employee_number", "team", "role")),
        ("DIM_LEGAL_ENTITY", 3, "Legal Entity", ("entity_code", "entity_name", "currency")),
        ("DIM_CALENDAR", 3, "Calendar", ("calendar_date", "fiscal_period", "fiscal_year", "calendar_month")),
    ], schemas)
    return IndustryPack(
        key="generic", label="Generic / Cross-industry", systems=systems,
        domains=("Customer", "Receivables & Collections", "Sales & Orders",
                 "Operations", "Finance", "Compliance"),
        backbone=backbone,
        business_units=("Collections", "Finance", "Customer Operations", "Sales",
                        "Operations", "Compliance", "Executive"),
        facts=facts, dims=dims,
        reference_tables=("REF_AGING_BUCKET", "REF_CHANNEL", "REF_STATUS"),
        report_themes=("Receivables Aging", "Collections Performance", "Order Performance",
                       "Revenue Assurance", "Product Performance", "Customer Contact",
                       "Write Off", "Operational Incident", "Channel Analysis",
                       "Compliance Return", "Returns Analysis", "Executive Scorecard"),
        threshold_metric="Days Past Due", threshold_column="days_past_due",
    )


# Derived ratio metrics per industry. Ratios give the "denominator swap" defect
# somewhere honest to live: two reports computing the same labelled ratio over a
# different denominator column (specification section 5.4).
RATIOS: dict[str, tuple[RatioSpec, ...]] = {
    "generic": (
        RatioSpec("Days Sales Outstanding", 0, 0, 5, 0, 5, 1, 365),
        RatioSpec("Write Off Rate", 5, 2, 5, 0, 5, 1, 100),
        RatioSpec("Complaint Rate per Contact", 4, 2, 4, 0, 0, 0, 100, False),
    ),
    "utility": (
        RatioSpec("Days Sales Outstanding", 0, 0, 5, 0, 5, 1, 365),
        RatioSpec("Write Off Rate", 5, 2, 5, 0, 5, 1, 100),
        RatioSpec("Complaint Rate per Contact", 4, 3, 4, 0, 0, 0, 100, False),
    ),
    "banking": (
        RatioSpec("Receivable Days", 0, 0, 5, 0, 5, 1, 365),
        RatioSpec("Charge Off Rate", 5, 2, 5, 0, 5, 1, 100),
        RatioSpec("Complaint Rate per Interaction", 4, 2, 4, 0, 0, 0, 100, False),
    ),
    "insurance": (
        RatioSpec("Loss Ratio", 5, 1, 5, 0, 0, 1, 100),
        RatioSpec("Underwriting Expense Ratio", 5, 2, 5, 0, 0, 1, 100),
        RatioSpec("Complaint Rate per Contact", 4, 2, 4, 0, 0, 0, 100, False),
    ),
    "retail": (
        RatioSpec("Discount Rate", 0, 3, 0, 1, 5, 1, 100),
        RatioSpec("Operating Expense Ratio", 5, 2, 5, 0, 5, 1, 100),
        RatioSpec("Loyalty Redemption Rate", 4, 1, 4, 0, 0, 0, 100, False),
    ),
    "healthcare": (
        RatioSpec("Denial Rate", 1, 2, 1, 0, 1, 1, 100),
        RatioSpec("Supply Expense Ratio", 5, 2, 5, 0, 5, 1, 100),
        RatioSpec("Outreach per Appointment", 4, 0, 4, 3, 0, 0, 100, False),
    ),
    "manufacturing": (
        RatioSpec("Gross Margin Rate", 5, 3, 5, 0, 5, 1, 100),
        RatioSpec("Material Cost Ratio", 5, 2, 5, 0, 5, 1, 100),
        RatioSpec("Warranty Cost per Case", 4, 1, 4, 0, 0, 0, 1, False),
    ),
    "telecom": (
        RatioSpec("Days Sales Outstanding", 0, 0, 5, 0, 5, 1, 365),
        RatioSpec("Bad Debt Rate", 5, 2, 5, 0, 5, 1, 100),
        RatioSpec("Complaint Rate per Contact", 4, 3, 4, 0, 0, 0, 100, False),
    ),
    "public_sector": (
        RatioSpec("Budget Execution Rate", 5, 2, 5, 0, 5, 1, 100),
        RatioSpec("Grant Share of Budget", 5, 3, 5, 0, 5, 1, 100),
        RatioSpec("Complaint Rate per Contact", 4, 2, 4, 0, 0, 0, 100, False),
    ),
}


_BUILDERS = {
    "generic": _generic,
    "utility": _utility,
    "banking": _banking,
    "insurance": _insurance,
    "retail": _retail,
    "healthcare": _healthcare,
    "manufacturing": _manufacturing,
    "telecom": _telecom,
    "public_sector": _public_sector,
}

INDUSTRY_KEYS = tuple(_BUILDERS)


def get_pack(key: str) -> IndustryPack:
    key = (key or "generic").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "energy": "utility", "utilities": "utility", "utility_energy": "utility",
        "cpg": "retail", "retail_cpg": "retail", "consumer": "retail",
        "bank": "banking", "financial_services": "banking",
        "health": "healthcare", "provider": "healthcare",
        "gov": "public_sector", "government": "public_sector", "public": "public_sector",
        "telco": "telecom", "communications": "telecom",
        "mfg": "manufacturing", "industrial": "manufacturing",
        "insurer": "insurance",
    }
    key = aliases.get(key, key)
    if key not in _BUILDERS:
        raise KeyError(f"unknown industry '{key}'; known: {', '.join(INDUSTRY_KEYS)}")
    pack = _BUILDERS[key]()
    return replace(pack, ratios=RATIOS[key])


def list_industries() -> list[dict]:
    out = []
    for key in INDUSTRY_KEYS:
        pack = get_pack(key)
        out.append({
            "key": key,
            "label": pack.label,
            "domains": list(pack.domains),
            "backbone": list(pack.backbone),
            "systems": [s.name for s in pack.systems],
            "business_units": list(pack.business_units),
        })
    return out
