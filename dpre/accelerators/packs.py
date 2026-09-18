"""Industry accelerators: the reusable content behind a "banking pack" (R-38).

A practice leader who hears "banking pack" expects a starter glossary with
defensible definitions, a KPI dictionary with canonical formulas and the
conflicts each one usually hides, a conformed backbone the client can adopt,
a persona map from business unit to the role that reads the numbers, and the
regulatory-report patterns that make a low-run report decision-critical
(specification sections 4.3, 5.4, 9.2, 15.1 and 17.3).

What shipped before this module was a demo generator: ``dpre/synth`` writes
glossary rows such as "Customer Name attribute of Customer maintained in CORE"
with a random person as steward on every term. That is the right shape for a
fixture and the wrong content for a client workshop. This module separates
the two assets. ``dpre/synth`` stays the generator; the content here is the
accelerator, hand-written, and consumable by three things:

* a manual run, which can borrow the backbone for ``build_graph`` and merge
  the starter glossary (status ``Starter``, steward blank - a real steward is
  a fact the client supplies, never something we invent);
* the narrator, whose ``ROLE_BY_KEYWORD`` map can fall back to
  ``persona_for`` when the client's business-unit names are industry-shaped;
* the generator, which can draw role-shaped stewards ("Head of Collections
  Reporting") from ``steward_role_for`` instead of random names.

Every structure is frozen and every accessor is a pure function, so the
content is as reproducible as the code. Definitions are written to be
contested: each glossary entry is one sentence a steward can accept, amend or
reject, which is the Phase 1 exit act in specification section 14.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from ..models import ExtractBundle, GlossaryTermRecord

STARTER_STATUS = "Starter"


@dataclass(frozen=True)
class KpiEntry:
    """One canonical KPI: the formula the client should adopt, and its usual drift."""

    label: str
    canonical_name: str
    formula: str
    aggregation: str
    grain: str
    domain: str
    typical_conflicts: tuple[str, ...] = ()
    regulatory: bool = False


@dataclass(frozen=True)
class GlossaryEntry:
    term: str
    definition: str
    domain: str
    related_kpi: str = ""


@dataclass(frozen=True)
class RegulatoryPattern:
    """A report-name pattern that implies consequence far above its run count."""

    pattern: str
    regime: str
    why_decision_critical: str


@dataclass(frozen=True)
class IndustryAccelerator:
    key: str
    label: str
    backbone: tuple[str, ...]
    domains: tuple[str, ...]
    personas: dict[str, str] = field(default_factory=dict)         # business unit -> role
    steward_roles: dict[str, str] = field(default_factory=dict)    # domain -> role title
    kpis: tuple[KpiEntry, ...] = ()
    glossary: tuple[GlossaryEntry, ...] = ()
    regulatory: tuple[RegulatoryPattern, ...] = ()

    def to_dict(self) -> dict:
        return {
            "key": self.key, "label": self.label, "backbone": list(self.backbone),
            "domains": list(self.domains), "personas": dict(self.personas),
            "steward_roles": dict(self.steward_roles),
            "kpis": [k.__dict__ | {"typical_conflicts": list(k.typical_conflicts)}
                     for k in self.kpis],
            "glossary": [g.__dict__ for g in self.glossary],
            "regulatory": [r.__dict__ for r in self.regulatory],
        }


# --------------------------------------------------------------------------
# Content shared by several industries
# --------------------------------------------------------------------------

_FINANCE_STEWARD = "Head of Financial Reporting"
_CUSTOMER_STEWARD = "Customer Data Steward (CRM owner)"

_AGING_CONFLICTS = (
    "aging boundary written as > 60 in some reports and >= 61 in others",
    "budget-billing or payment-arrangement accounts excluded in one variant only",
    "bill date versus read date as the time basis of the balance",
)
_DSO_CONFLICTS = (
    "denominator is billed revenue in Finance reports and total revenue in Collections reports",
    "receivable balance taken at period end in one report and averaged in another",
)
_COMPLAINT_CONFLICTS = (
    "denominator per contact in operations reports and per account in executive reports",
    "reopened complaints counted again in some reports",
)

def _common_glossary(person_domain: str = "Customer") -> tuple[GlossaryEntry, ...]:
    """Terms every estate needs, filed under a domain that estate actually has.

    A hospital has Patients and a benefits agency has Citizens, not Customers.
    Filing a shared term under a domain the accelerator does not declare puts a
    term in the starter glossary that no steward in this client owns, which is
    exactly the templated filler review finding R-38 objected to.
    """
    return (
        GlossaryEntry("Business Unit", "The organisational unit that owns a report's consumers; "
                      "the grain of the demand-score consumer breadth feature.", person_domain),
        GlossaryEntry("Fiscal Period", "The accounting period a transaction is posted to, which "
                      "may differ from the calendar month of the underlying event.", "Finance"),
        GlossaryEntry("System of Record", "The application whose value for a fact is "
                      "authoritative when two systems disagree; feasibility prefers it.",
                      "Finance"),
    )


# --------------------------------------------------------------------------
# Utility / energy
# --------------------------------------------------------------------------

def _utility() -> IndustryAccelerator:
    return IndustryAccelerator(
        key="utility", label="Utility / Energy",
        backbone=("Customer", "Account", "Premise", "Service Point", "Meter"),
        domains=("Customer", "Billing & Collections", "Metering", "Network Operations",
                 "Finance", "Regulatory"),
        personas={
            "Credit & Collections": "Collections manager",
            "Finance": "Finance business partner",
            "Customer Operations": "Customer operations lead",
            "Metering Services": "Metering services lead",
            "Network Operations": "Network operations manager",
            "Regulatory Affairs": "Regulatory reporting lead",
            "Executive": "Executive sponsor",
        },
        steward_roles={
            "Customer": _CUSTOMER_STEWARD,
            "Billing & Collections": "Head of Billing Operations",
            "Metering": "Meter Data Manager",
            "Network Operations": "Network Performance Manager",
            "Finance": _FINANCE_STEWARD,
            "Regulatory": "Regulatory Compliance Manager",
        },
        kpis=(
            KpiEntry("Arrears Balance", "arrears_balance",
                     "SUM(arrears_amount) for accounts with days_past_due > 0 at period end",
                     "SUM", "Account", "Billing & Collections", _AGING_CONFLICTS),
            KpiEntry("Arrears 60+", "arrears_balance_60_plus",
                     "SUM(arrears_amount) WHERE days_past_due >= 61 at period end",
                     "SUM", "Account", "Billing & Collections", _AGING_CONFLICTS),
            KpiEntry("Accounts in Arrears", "accounts_in_arrears",
                     "COUNT DISTINCT account_key WHERE arrears_amount > 0", "COUNT DISTINCT",
                     "Account", "Billing & Collections",
                     ("closed accounts with a residual balance included in one variant only",)),
            KpiEntry("Days Sales Outstanding", "days_sales_outstanding",
                     "period-end receivable balance / billed revenue in the period x days in period",
                     "RATIO", "Account", "Finance", _DSO_CONFLICTS),
            KpiEntry("Payment Arrangement Rate", "payment_arrangement_rate",
                     "accounts with an active arrangement / accounts in arrears", "RATIO",
                     "Account", "Billing & Collections",
                     ("defaulted arrangements still counted as active in some reports",)),
            KpiEntry("Write Off Amount", "write_off_amount",
                     "SUM(write_off_amount) posted in the fiscal period", "SUM", "Account",
                     "Finance", ("posting date versus bill date as the period basis",
                                 "recoveries netted in one report and shown gross in another")),
            KpiEntry("Estimated Read Rate", "estimated_read_rate",
                     "reads flagged estimated / reads scheduled in the period", "RATIO", "Meter",
                     "Metering", ("AMI meters excluded from the denominator in some reports",)),
            KpiEntry("SAIDI", "saidi",
                     "SUM(customer outage minutes) / customers served, major event days excluded",
                     "RATIO", "Service Point", "Network Operations",
                     ("major-event-day exclusion applied in the regulatory filing only",),
                     regulatory=True),
            KpiEntry("Disconnect Notices Issued", "disconnect_notices_issued",
                     "COUNT(notice) by notice_date", "COUNT", "Premise",
                     "Billing & Collections",
                     ("counted by scheduled_date in field-operations reports",)),
            KpiEntry("Complaint Rate", "complaint_rate",
                     "complaints / customer contacts in the period x 100", "RATIO", "Customer",
                     "Customer", _COMPLAINT_CONFLICTS),
        ),
        glossary=_common_glossary("Customer") + (
            GlossaryEntry("Account", "A billing relationship between a customer and the utility "
                          "for one or more service points; the grain of arrears and payments.",
                          "Billing & Collections"),
            GlossaryEntry("Premise", "A physical location that receives service; one premise can "
                          "have several service points and several accounts over time.",
                          "Customer"),
            GlossaryEntry("Service Point", "The point at which a commodity is delivered and "
                          "metered; the grain of outage and disconnect measures.", "Metering"),
            GlossaryEntry("Arrears", "The portion of an account balance that is past its due "
                          "date at the reporting date.", "Billing & Collections", "arrears_balance"),
            GlossaryEntry("Days Past Due", "Calendar days between the oldest unpaid bill's due "
                          "date and the reporting date.", "Billing & Collections",
                          "arrears_balance_60_plus"),
            GlossaryEntry("Payment Arrangement", "An agreed instalment plan that suspends "
                          "collection activity while payments are kept.", "Billing & Collections",
                          "payment_arrangement_rate"),
            GlossaryEntry("Budget Billing", "A levelised monthly charge that spreads seasonal "
                          "consumption; often excluded from arrears reporting.",
                          "Billing & Collections"),
            GlossaryEntry("Estimated Read", "A consumption value derived from history when no "
                          "actual meter read was obtained.", "Metering", "estimated_read_rate"),
            GlossaryEntry("Major Event Day", "A day whose outage minutes exceed the statistical "
                          "threshold and are excluded from reliability indices.",
                          "Network Operations", "saidi"),
            GlossaryEntry("Write Off", "Receivable balance removed from the ledger as "
                          "uncollectable after the collection cycle is exhausted.", "Finance",
                          "write_off_amount"),
        ),
        regulatory=(
            RegulatoryPattern("Regulatory Filing", "Utility commission",
                              "A filing figure that is wrong is a compliance finding, not a "
                              "reporting error, whatever its run count."),
            RegulatoryPattern("Reliability", "Reliability indices (SAIDI/SAIFI)",
                              "Reported annually to the regulator; one run a year."),
            RegulatoryPattern("Rate Case", "Tariff review",
                              "Run a handful of times per rate cycle; drives revenue for years."),
        ),
    )


# --------------------------------------------------------------------------
# Banking
# --------------------------------------------------------------------------

def _banking() -> IndustryAccelerator:
    delinquency = (
        "delinquency bucket boundary written as > 30 in some reports and >= 31 in others",
        "charged-off accounts still in the delinquent population in one variant",
        "statement date versus value date as the time basis",
    )
    return IndustryAccelerator(
        key="banking", label="Banking",
        backbone=("Customer", "Account", "Product Holding", "Transaction", "Card"),
        domains=("Customer", "Lending & Collections", "Payments", "Risk", "Finance", "Compliance"),
        personas={
            "Collections": "Collections manager",
            "Finance": "Finance business partner",
            "Retail Banking": "Retail banking product manager",
            "Risk Management": "Risk manager",
            "Payments Operations": "Payments operations lead",
            "Compliance": "Compliance officer",
            "Executive": "Executive sponsor",
        },
        steward_roles={
            "Customer": _CUSTOMER_STEWARD,
            "Lending & Collections": "Head of Collections Reporting",
            "Payments": "Payments Operations Data Lead",
            "Risk": "Credit Risk Reporting Manager",
            "Finance": _FINANCE_STEWARD,
            "Compliance": "Regulatory Reporting Manager",
        },
        kpis=(
            KpiEntry("Delinquent Balance", "delinquent_balance",
                     "SUM(outstanding_balance) WHERE days_past_due >= 30 at statement date",
                     "SUM", "Account", "Lending & Collections", delinquency),
            KpiEntry("Delinquency Rate", "delinquency_rate",
                     "delinquent balance / total outstanding balance x 100", "RATIO", "Account",
                     "Lending & Collections", delinquency),
            KpiEntry("Charge Off Rate", "charge_off_rate",
                     "net charge-offs in the period / average outstanding balance, annualised",
                     "RATIO", "Account", "Finance",
                     ("gross versus net of recoveries", "period-end versus average balance")),
            KpiEntry("Roll Rate", "roll_rate",
                     "balance moving from bucket n to bucket n+1 / bucket n balance at prior month end",
                     "RATIO", "Account", "Risk",
                     ("balance-weighted in Risk reports and account-count in Collections reports",)),
            KpiEntry("Net Interest Income", "net_interest_income",
                     "SUM(interest income) - SUM(interest expense) posted in the fiscal period",
                     "SUM", "Account", "Finance",
                     ("fee income folded in by some product reports",), regulatory=True),
            KpiEntry("Loan Loss Provision", "loan_loss_provision",
                     "SUM(provision_amount) posted in the fiscal period", "SUM", "Account",
                     "Finance", ("stage 1/2/3 split present only in the regulatory view",),
                     regulatory=True),
            KpiEntry("Products Per Customer", "products_per_customer",
                     "COUNT(active holdings) / COUNT DISTINCT customer", "RATIO", "Customer",
                     "Customer", ("dormant holdings counted as active in some reports",)),
            KpiEntry("Attrition Rate", "attrition_rate",
                     "customers closing their last product in the period / customers at period start",
                     "RATIO", "Customer", "Customer",
                     ("account closures counted rather than customer exits",)),
            KpiEntry("Payment Failure Rate", "payment_failure_rate",
                     "failed transactions / attempted transactions x 100", "RATIO", "Transaction",
                     "Payments", ("retries counted as separate attempts in one variant",)),
            KpiEntry("Complaint Rate", "complaint_rate",
                     "complaints / customer interactions x 100", "RATIO", "Customer", "Customer",
                     _COMPLAINT_CONFLICTS, regulatory=True),
        ),
        glossary=_common_glossary("Customer") + (
            GlossaryEntry("Account", "A contractual product relationship (deposit, loan or "
                          "card) held by one or more customers.", "Lending & Collections"),
            GlossaryEntry("Product Holding", "One customer's ownership of one product instance; "
                          "the grain of cross-sell and attrition.", "Customer",
                          "products_per_customer"),
            GlossaryEntry("Days Past Due", "Calendar days since the oldest contractual payment "
                          "was missed, measured at the statement date.", "Lending & Collections",
                          "delinquent_balance"),
            GlossaryEntry("Delinquency Bucket", "The aging band (1-29, 30-59, 60-89, 90+) an "
                          "account sits in by days past due.", "Lending & Collections",
                          "roll_rate"),
            GlossaryEntry("Charge Off", "A loan balance written off the balance sheet as "
                          "uncollectable; recoveries are reported separately.", "Finance",
                          "charge_off_rate"),
            GlossaryEntry("Provision", "The expense recognised for expected credit losses on "
                          "the outstanding book.", "Finance", "loan_loss_provision"),
            GlossaryEntry("Value Date", "The date on which funds are effective for interest, "
                          "as distinct from the posting date.", "Payments"),
            GlossaryEntry("Interchange Revenue", "Fees earned by the issuer on card "
                          "transactions settled through the scheme.", "Payments"),
            GlossaryEntry("KYC Status", "Whether the customer's identity verification is "
                          "current under the applicable know-your-customer rules.", "Compliance"),
            GlossaryEntry("Risk Grade", "The internal rating band assigned to an obligor, "
                          "mapped to a probability-of-default range.", "Risk"),
        ),
        regulatory=(
            RegulatoryPattern("Regulatory Return", "Prudential regulator",
                              "Submitted quarterly; a misstatement is a reportable breach."),
            RegulatoryPattern("Capital", "Capital adequacy reporting",
                              "Feeds risk-weighted assets; low run count, board consequence."),
            RegulatoryPattern("Complaints Return", "Conduct regulator",
                              "Complaint counts are published and compared across firms."),
        ),
    )


# --------------------------------------------------------------------------
# Insurance
# --------------------------------------------------------------------------

def _insurance() -> IndustryAccelerator:
    return IndustryAccelerator(
        key="insurance", label="Insurance",
        backbone=("Customer", "Policy", "Coverage", "Claim", "Payment"),
        domains=("Customer", "Policy & Underwriting", "Claims", "Actuarial", "Finance",
                 "Regulatory"),
        personas={
            "Claims Operations": "Claims operations lead",
            "Finance": "Finance business partner",
            "Underwriting": "Underwriting manager",
            "Actuarial": "Actuary",
            "Distribution": "Distribution manager",
            "Compliance": "Compliance officer",
            "Executive": "Executive sponsor",
        },
        steward_roles={
            "Customer": _CUSTOMER_STEWARD,
            "Policy & Underwriting": "Head of Underwriting Operations",
            "Claims": "Claims Data Manager",
            "Actuarial": "Chief Actuary's Reporting Lead",
            "Finance": _FINANCE_STEWARD,
            "Regulatory": "Regulatory Reporting Manager",
        },
        kpis=(
            KpiEntry("Gross Written Premium", "gross_written_premium",
                     "SUM(written premium) by policy inception in the period", "SUM", "Policy",
                     "Policy & Underwriting",
                     ("inception date versus bound date", "endorsements netted or not")),
            KpiEntry("Earned Premium", "earned_premium",
                     "written premium recognised pro rata over the coverage period", "SUM",
                     "Policy", "Finance", ("daily versus monthly earning pattern",),
                     regulatory=True),
            KpiEntry("Loss Ratio", "loss_ratio",
                     "(paid + case reserves + IBNR movement) / earned premium x 100", "RATIO",
                     "Policy", "Actuarial",
                     ("denominator is written premium in distribution reports",
                      "IBNR excluded in claims-operations reports")),
            KpiEntry("Expense Ratio", "expense_ratio",
                     "underwriting expenses / written premium x 100", "RATIO", "Policy",
                     "Finance", ("earned premium used as denominator in some variants",)),
            KpiEntry("Combined Ratio", "combined_ratio",
                     "loss ratio + expense ratio", "RATIO", "Policy", "Finance",
                     ("inherits every conflict of its two components",), regulatory=True),
            KpiEntry("Claims Frequency", "claims_frequency",
                     "claims reported / policies in force x 100", "RATIO", "Policy", "Claims",
                     ("reopened claims counted again", "exposure-years versus policy count")),
            KpiEntry("Average Claim Severity", "average_claim_severity",
                     "incurred claim cost / number of closed claims", "RATIO", "Claim", "Claims",
                     ("nil claims excluded in one variant only",)),
            KpiEntry("Lapse Rate", "lapse_rate",
                     "policies not renewed at expiry / policies offered renewal x 100", "RATIO",
                     "Policy", "Policy & Underwriting",
                     ("cancellations mid-term folded into lapses",)),
            KpiEntry("Straight Through Rate", "straight_through_rate",
                     "claims settled without manual touch / claims settled x 100", "RATIO",
                     "Claim", "Claims", ()),
            KpiEntry("Complaint Rate", "complaint_rate",
                     "complaints / policies in force x 1000", "RATIO", "Customer", "Customer",
                     _COMPLAINT_CONFLICTS, regulatory=True),
        ),
        glossary=_common_glossary("Customer") + (
            GlossaryEntry("Policy", "A contract of insurance with a defined term, premium and "
                          "set of coverages.", "Policy & Underwriting"),
            GlossaryEntry("Coverage", "One insured peril or section within a policy, with its "
                          "own limit and deductible.", "Policy & Underwriting"),
            GlossaryEntry("Claim", "A demand under a policy arising from one insured event; "
                          "the grain of frequency and severity.", "Claims", "claims_frequency"),
            GlossaryEntry("Incurred Claims", "Paid amounts plus outstanding case reserves, plus "
                          "IBNR where the actuarial view is intended.", "Actuarial", "loss_ratio"),
            GlossaryEntry("IBNR", "Incurred but not reported: the reserve for events that have "
                          "occurred but have not yet been notified.", "Actuarial", "loss_ratio"),
            GlossaryEntry("Written Premium", "Premium contracted at inception, before earning.",
                          "Finance", "gross_written_premium"),
            GlossaryEntry("Earned Premium", "The share of written premium that relates to "
                          "coverage already provided.", "Finance", "earned_premium"),
            GlossaryEntry("Policies In Force", "Policies whose coverage period includes the "
                          "reporting date.", "Policy & Underwriting", "lapse_rate"),
            GlossaryEntry("Case Reserve", "The adjuster's estimate of the remaining cost of an "
                          "open claim.", "Claims"),
            GlossaryEntry("Lapse", "Termination of a policy at expiry because the renewal was "
                          "not taken up.", "Policy & Underwriting", "lapse_rate"),
        ),
        regulatory=(
            RegulatoryPattern("Solvency", "Prudential regulator",
                              "Capital and reserve figures reported to the supervisor."),
            RegulatoryPattern("Statutory Return", "Insurance supervisor",
                              "Annual statutory accounts and returns; one run a year."),
            RegulatoryPattern("Conduct Return", "Conduct regulator",
                              "Complaint and claims-handling times are supervised."),
        ),
    )


# --------------------------------------------------------------------------
# Retail / CPG
# --------------------------------------------------------------------------

def _retail() -> IndustryAccelerator:
    return IndustryAccelerator(
        key="retail", label="Retail / CPG",
        backbone=("Customer", "Order", "Store", "Product", "Shipment"),
        domains=("Customer", "Sales & Orders", "Merchandising", "Supply Chain", "Finance",
                 "Marketing"),
        personas={
            "Merchandising": "Merchandising planner",
            "Finance": "Finance business partner",
            "Store Operations": "Store operations manager",
            "Ecommerce": "Ecommerce trading manager",
            "Supply Chain": "Supply chain planner",
            "Marketing": "Marketing analyst",
            "Executive": "Executive sponsor",
        },
        steward_roles={
            "Customer": "Customer Data Steward (loyalty owner)",
            "Sales & Orders": "Head of Trading Analytics",
            "Merchandising": "Merchandise Planning Lead",
            "Supply Chain": "Supply Chain Data Manager",
            "Finance": _FINANCE_STEWARD,
            "Marketing": "Marketing Analytics Lead",
        },
        kpis=(
            KpiEntry("Net Sales", "net_sales",
                     "SUM(gross sales) - SUM(returns) - SUM(discounts) by transaction date",
                     "SUM", "Order", "Sales & Orders",
                     ("returns netted by return date rather than original sale date",
                      "VAT included in store reports and excluded in finance reports")),
            KpiEntry("Gross Margin Rate", "gross_margin_rate",
                     "(net sales - cost of goods sold) / net sales x 100", "RATIO", "Product",
                     "Merchandising", ("landed cost versus standard cost as COGS basis",)),
            KpiEntry("Like For Like Sales Growth", "like_for_like_sales_growth",
                     "net sales in comparable stores this period / same period prior year - 1",
                     "RATIO", "Store", "Sales & Orders",
                     ("comparable-store window is 52 weeks in some reports and 12 months in others",)),
            KpiEntry("Sell Through Rate", "sell_through_rate",
                     "units sold / (units sold + units on hand) x 100", "RATIO", "Product",
                     "Merchandising", ("in-transit stock counted as on hand in one variant",)),
            KpiEntry("Average Order Value", "average_order_value",
                     "net sales / order count", "RATIO", "Order", "Sales & Orders",
                     ("cancelled orders in the denominator in some reports",)),
            KpiEntry("Return Rate", "return_rate",
                     "returned units / sold units x 100", "RATIO", "Order", "Sales & Orders",
                     ("value-weighted in finance reports and unit-weighted in operations reports",)),
            KpiEntry("On Time In Full", "on_time_in_full",
                     "shipments delivered on the promised date with full quantity / shipments x 100",
                     "RATIO", "Shipment", "Supply Chain",
                     ("promise date versus requested date as the on-time basis",)),
            KpiEntry("Stock Availability", "stock_availability",
                     "SKU-store combinations with on-hand > 0 / ranged SKU-store combinations",
                     "RATIO", "Product", "Supply Chain", ()),
            KpiEntry("Loyalty Redemption Rate", "loyalty_redemption_rate",
                     "points redeemed / points issued x 100", "RATIO", "Customer", "Marketing",
                     ("expired points excluded from the denominator in one variant",)),
            KpiEntry("Cart Abandonment Rate", "cart_abandonment_rate",
                     "sessions with a cart and no order / sessions with a cart x 100", "RATIO",
                     "Customer", "Marketing",
                     ("sessions counted per device in one report and per visitor in another",)),
        ),
        glossary=_common_glossary("Customer") + (
            GlossaryEntry("Order", "A customer's purchase transaction across one or more lines "
                          "and channels.", "Sales & Orders", "average_order_value"),
            GlossaryEntry("SKU", "Stock keeping unit: the lowest level at which a product is "
                          "ranged, priced and counted.", "Merchandising", "sell_through_rate"),
            GlossaryEntry("Net Sales", "Sales after returns, discounts and, where stated, "
                          "sales tax.", "Finance", "net_sales"),
            GlossaryEntry("Cost of Goods Sold", "The cost basis of units sold; the accelerator "
                          "recommends landed cost.", "Finance", "gross_margin_rate"),
            GlossaryEntry("Comparable Store", "A store trading for the whole of both periods "
                          "being compared, with no material change of footprint.",
                          "Sales & Orders", "like_for_like_sales_growth"),
            GlossaryEntry("On Hand", "Units physically in the store or warehouse at the count "
                          "time, excluding in-transit.", "Supply Chain", "sell_through_rate"),
            GlossaryEntry("Promise Date", "The delivery date committed to the customer at order "
                          "confirmation.", "Supply Chain", "on_time_in_full"),
            GlossaryEntry("Markdown", "A permanent reduction in selling price; distinct from a "
                          "promotional discount.", "Merchandising"),
            GlossaryEntry("Loyalty Member", "A customer enrolled in the loyalty programme with "
                          "an identifiable card or account.", "Marketing",
                          "loyalty_redemption_rate"),
            GlossaryEntry("Session", "One visit to the ecommerce site or app by one visitor.",
                          "Marketing", "cart_abandonment_rate"),
        ),
        regulatory=(
            RegulatoryPattern("Statutory", "Financial reporting",
                              "Statutory revenue reconciliation; audited annually."),
            RegulatoryPattern("Product Recall", "Product safety",
                              "Run only during a recall; the consequence of error is public."),
        ),
    )


# --------------------------------------------------------------------------
# Healthcare
# --------------------------------------------------------------------------

def _healthcare() -> IndustryAccelerator:
    return IndustryAccelerator(
        key="healthcare", label="Healthcare",
        backbone=("Patient", "Encounter", "Claim", "Provider", "Facility"),
        domains=("Patient", "Revenue Cycle", "Clinical Operations", "Provider", "Finance",
                 "Quality & Compliance"),
        personas={
            "Revenue Cycle": "Revenue cycle director",
            "Finance": "Finance business partner",
            "Clinical Operations": "Clinical operations lead",
            "Quality": "Quality lead",
            "Patient Access": "Patient access manager",
            "Compliance": "Compliance officer",
            "Executive": "Executive sponsor",
        },
        steward_roles={
            "Patient": "Health Information Management Director",
            "Revenue Cycle": "Revenue Cycle Data Manager",
            "Clinical Operations": "Clinical Informatics Lead",
            "Provider": "Medical Staff Office Data Lead",
            "Finance": _FINANCE_STEWARD,
            "Quality & Compliance": "Quality Reporting Manager",
        },
        kpis=(
            KpiEntry("Denial Rate", "denial_rate",
                     "claims denied on first submission / claims submitted x 100", "RATIO",
                     "Claim", "Revenue Cycle",
                     ("denominator is claim count in operations and billed dollars in finance",
                      "partial denials counted as denials in one variant only")),
            KpiEntry("Days in Accounts Receivable", "days_in_ar",
                     "AR balance at period end / average daily gross charges over the last 90 days",
                     "RATIO", "Claim", "Revenue Cycle",
                     ("net versus gross charges as the daily basis",
                      "credit balances netted in some reports")),
            KpiEntry("Clean Claim Rate", "clean_claim_rate",
                     "claims accepted without edit on first pass / claims submitted x 100",
                     "RATIO", "Claim", "Revenue Cycle", ()),
            KpiEntry("Average Length of Stay", "average_length_of_stay",
                     "SUM(discharge date - admit date) / inpatient discharges", "RATIO",
                     "Encounter", "Clinical Operations",
                     ("observation stays included in one variant",
                      "midnight census versus hour-based stay")),
            KpiEntry("Readmission Rate", "readmission_rate",
                     "unplanned readmissions within 30 days / index discharges x 100", "RATIO",
                     "Encounter", "Quality & Compliance",
                     ("30 versus 31 day window", "planned readmissions not excluded"),
                     regulatory=True),
            KpiEntry("Bed Occupancy", "bed_occupancy",
                     "occupied bed days / available bed days x 100", "RATIO", "Facility",
                     "Clinical Operations", ("staffed versus licensed beds as the denominator",)),
            KpiEntry("Net Patient Revenue", "net_patient_revenue",
                     "gross charges - contractual adjustments - bad debt provision", "SUM",
                     "Claim", "Finance", ("bad debt treated as expense in one view",),
                     regulatory=True),
            KpiEntry("Cost Per Case", "cost_per_case",
                     "direct + allocated cost / discharges, case-mix adjusted", "RATIO",
                     "Encounter", "Finance", ("case-mix adjustment applied only in finance reports",)),
            KpiEntry("No Show Rate", "no_show_rate",
                     "appointments not attended without cancellation / scheduled appointments x 100",
                     "RATIO", "Encounter", "Patient",
                     ("late cancellations counted as no-shows in some reports",)),
            KpiEntry("Adverse Event Rate", "adverse_event_rate",
                     "reported adverse events / 1000 patient days", "RATIO", "Encounter",
                     "Quality & Compliance", (), regulatory=True),
        ),
        glossary=_common_glossary("Patient") + (
            GlossaryEntry("Patient", "A person receiving care, identified once across "
                          "facilities by the enterprise master patient index.", "Patient"),
            GlossaryEntry("Encounter", "One episode of contact between a patient and the "
                          "organisation; the grain of stay and readmission measures.",
                          "Clinical Operations", "average_length_of_stay"),
            GlossaryEntry("Claim", "A request for payment to a payer for services in one or "
                          "more encounters.", "Revenue Cycle", "denial_rate"),
            GlossaryEntry("Denial", "A payer's refusal to pay all or part of a claim as "
                          "submitted.", "Revenue Cycle", "denial_rate"),
            GlossaryEntry("Gross Charges", "The chargemaster value of services before "
                          "contractual adjustments.", "Finance", "days_in_ar"),
            GlossaryEntry("Contractual Adjustment", "The difference between gross charges and "
                          "the amount the payer contract allows.", "Finance",
                          "net_patient_revenue"),
            GlossaryEntry("Index Discharge", "The discharge from which a readmission window "
                          "is measured.", "Quality & Compliance", "readmission_rate"),
            GlossaryEntry("Case Mix Index", "The average relative weight of the cases treated, "
                          "used to adjust cost and stay comparisons.", "Finance", "cost_per_case"),
            GlossaryEntry("Staffed Bed", "A licensed bed with nursing cover available on the "
                          "reporting day.", "Clinical Operations", "bed_occupancy"),
            GlossaryEntry("Protected Health Information", "Any patient-identifying attribute; "
                          "carries the highest sensitivity class and a Stage 9 review.",
                          "Patient"),
        ),
        regulatory=(
            RegulatoryPattern("Quality Measure", "Quality reporting programme",
                              "Publicly reported and payment-adjusted; run per submission window."),
            RegulatoryPattern("Cost Report", "Payer cost reporting",
                              "Annual; drives reimbursement for the following year."),
            RegulatoryPattern("Adverse Event", "Patient safety reporting",
                              "Low volume, mandatory notification."),
        ),
    )


# --------------------------------------------------------------------------
# Manufacturing
# --------------------------------------------------------------------------

def _manufacturing() -> IndustryAccelerator:
    return IndustryAccelerator(
        key="manufacturing", label="Manufacturing",
        backbone=("Customer", "Order", "Plant", "Work Order", "Asset"),
        domains=("Customer", "Sales & Orders", "Production", "Maintenance", "Finance",
                 "Quality"),
        personas={
            "Plant Operations": "Plant manager",
            "Finance": "Finance business partner",
            "Sales": "Sales operations manager",
            "Maintenance": "Maintenance planner",
            "Quality": "Quality lead",
            "Supply Chain": "Supply chain planner",
            "Executive": "Executive sponsor",
        },
        steward_roles={
            "Customer": _CUSTOMER_STEWARD,
            "Sales & Orders": "Order Management Data Lead",
            "Production": "Manufacturing Systems Data Manager",
            "Maintenance": "Reliability Engineering Lead",
            "Finance": _FINANCE_STEWARD,
            "Quality": "Quality Systems Manager",
        },
        kpis=(
            KpiEntry("Overall Equipment Effectiveness", "oee",
                     "availability x performance x quality", "RATIO", "Asset", "Production",
                     ("planned downtime excluded from availability in plant reports only",
                      "ideal cycle time from the routing versus best observed")),
            KpiEntry("First Pass Yield", "first_pass_yield",
                     "units passing inspection without rework / units started x 100", "RATIO",
                     "Work Order", "Quality", ("reworked units counted as passed in one variant",)),
            KpiEntry("Scrap Rate", "scrap_rate",
                     "scrapped quantity / produced quantity x 100", "RATIO", "Work Order",
                     "Quality", ("value-weighted in finance reports, unit-weighted in plant reports",)),
            KpiEntry("Schedule Adherence", "schedule_adherence",
                     "work orders completed on the scheduled date / work orders due x 100",
                     "RATIO", "Work Order", "Production", ("original versus rescheduled due date",)),
            KpiEntry("On Time Delivery", "on_time_delivery",
                     "order lines shipped by the confirmed date / order lines due x 100", "RATIO",
                     "Order", "Sales & Orders",
                     ("customer requested date used instead of confirmed date",)),
            KpiEntry("Mean Time Between Failures", "mtbf",
                     "operating hours / number of failures", "RATIO", "Asset", "Maintenance",
                     ("planned interventions counted as failures in some reports",)),
            KpiEntry("Maintenance Cost Per Asset", "maintenance_cost_per_asset",
                     "SUM(maintenance labour + parts) / assets in service", "RATIO", "Asset",
                     "Maintenance", ("capital refurbishment included in one variant",)),
            KpiEntry("Gross Margin Rate", "gross_margin_rate",
                     "(revenue - cost of goods sold) / revenue x 100", "RATIO", "Order",
                     "Finance", ("standard versus actual cost as COGS basis",), regulatory=True),
            KpiEntry("Inventory Turns", "inventory_turns",
                     "cost of goods sold over 12 months / average inventory value", "RATIO",
                     "Plant", "Finance", ("period-end inventory used instead of average",)),
            KpiEntry("Warranty Cost Per Unit", "warranty_cost_per_unit",
                     "warranty claims cost / units shipped in the warranty cohort", "RATIO",
                     "Order", "Quality", ()),
        ),
        glossary=_common_glossary("Customer") + (
            GlossaryEntry("Work Order", "An instruction to produce a quantity of one material "
                          "on a routing; the grain of yield and scrap.", "Production",
                          "first_pass_yield"),
            GlossaryEntry("Asset", "A maintained piece of equipment with an asset number in "
                          "the maintenance system.", "Maintenance", "mtbf"),
            GlossaryEntry("Availability", "Run time / planned production time.", "Production",
                          "oee"),
            GlossaryEntry("Performance", "Actual output / theoretical output at ideal cycle "
                          "time during run time.", "Production", "oee"),
            GlossaryEntry("Quality Rate", "Good units / total units produced.", "Quality", "oee"),
            GlossaryEntry("Planned Downtime", "Time the line is scheduled not to run "
                          "(changeover, planned maintenance, no demand).", "Production", "oee"),
            GlossaryEntry("Rework", "Additional processing of a unit that failed inspection so "
                          "it can pass.", "Quality", "first_pass_yield"),
            GlossaryEntry("Confirmed Date", "The delivery date acknowledged to the customer "
                          "on the order confirmation.", "Sales & Orders", "on_time_delivery"),
            GlossaryEntry("Standard Cost", "The planned unit cost of a material for the "
                          "costing period.", "Finance", "gross_margin_rate"),
            GlossaryEntry("Failure", "An unplanned event in which an asset stops performing "
                          "its required function.", "Maintenance", "mtbf"),
        ),
        regulatory=(
            RegulatoryPattern("Safety", "Occupational safety reporting",
                              "Incident rates are reported to the regulator; low run count."),
            RegulatoryPattern("Environmental", "Emissions and effluent reporting",
                              "Permit conditions; a wrong number is a breach."),
        ),
    )


# --------------------------------------------------------------------------
# Telecom
# --------------------------------------------------------------------------

def _telecom() -> IndustryAccelerator:
    return IndustryAccelerator(
        key="telecom", label="Telecom",
        backbone=("Customer", "Account", "Subscription", "Device", "Network Element"),
        domains=("Customer", "Billing & Collections", "Network", "Care", "Finance", "Regulatory"),
        personas={
            "Credit & Collections": "Collections manager",
            "Finance": "Finance business partner",
            "Customer Care": "Care operations lead",
            "Network Operations": "Network operations manager",
            "Marketing": "Marketing analyst",
            "Regulatory Affairs": "Regulatory reporting lead",
            "Executive": "Executive sponsor",
        },
        steward_roles={
            "Customer": _CUSTOMER_STEWARD,
            "Billing & Collections": "Billing Assurance Manager",
            "Network": "Network Performance Data Lead",
            "Care": "Care Operations Data Lead",
            "Finance": _FINANCE_STEWARD,
            "Regulatory": "Regulatory Compliance Manager",
        },
        kpis=(
            KpiEntry("Churn Rate", "churn_rate",
                     "subscriptions disconnected in the period / subscriptions at period start x 100",
                     "RATIO", "Subscription", "Customer",
                     ("involuntary (non-payment) churn excluded in marketing reports",
                      "average versus opening base as the denominator")),
            KpiEntry("ARPU", "arpu",
                     "service revenue in the period / average active subscriptions", "RATIO",
                     "Subscription", "Finance",
                     ("device revenue included in one variant", "period-end versus average base")),
            KpiEntry("Arrears Balance", "arrears_balance",
                     "SUM(arrears_amount) for accounts past due at period end", "SUM", "Account",
                     "Billing & Collections", _AGING_CONFLICTS),
            KpiEntry("Bad Debt Rate", "bad_debt_rate",
                     "write-offs in the period / billed revenue x 100", "RATIO", "Account",
                     "Finance", ("recoveries netted in one report and not the other",)),
            KpiEntry("Days Sales Outstanding", "days_sales_outstanding",
                     "period-end receivable / billed revenue x days in period", "RATIO",
                     "Account", "Finance", _DSO_CONFLICTS),
            KpiEntry("Network Availability", "network_availability",
                     "(total cell hours - outage hours) / total cell hours x 100", "RATIO",
                     "Network Element", "Network",
                     ("planned maintenance windows excluded in engineering reports only",),
                     regulatory=True),
            KpiEntry("Dropped Call Rate", "dropped_call_rate",
                     "abnormally released calls / established calls x 100", "RATIO",
                     "Network Element", "Network", ()),
            KpiEntry("First Contact Resolution", "first_contact_resolution",
                     "contacts with no repeat contact within 7 days / contacts x 100", "RATIO",
                     "Customer", "Care", ("7 versus 14 day repeat window",)),
            KpiEntry("Average Handle Time", "average_handle_time",
                     "SUM(talk + hold + wrap seconds) / handled contacts", "RATIO", "Customer",
                     "Care", ("wrap time excluded in some reports",)),
            KpiEntry("Complaint Rate", "complaint_rate",
                     "complaints / 1000 subscriptions", "RATIO", "Customer", "Regulatory",
                     _COMPLAINT_CONFLICTS, regulatory=True),
        ),
        glossary=_common_glossary("Customer") + (
            GlossaryEntry("Subscription", "One billable service line (a MSISDN or line id) "
                          "under an account; the grain of churn and ARPU.", "Customer",
                          "churn_rate"),
            GlossaryEntry("Account", "The billing relationship holding one or more "
                          "subscriptions.", "Billing & Collections", "arrears_balance"),
            GlossaryEntry("Active Base", "Subscriptions with a status of active on the "
                          "reporting date, excluding suspended lines.", "Customer", "arpu"),
            GlossaryEntry("Involuntary Churn", "Disconnection initiated by the operator, "
                          "typically for non-payment.", "Billing & Collections", "churn_rate"),
            GlossaryEntry("Service Revenue", "Recurring and usage revenue excluding device "
                          "and accessory sales.", "Finance", "arpu"),
            GlossaryEntry("Network Element", "A managed node (cell, site, router) with a "
                          "performance counter feed.", "Network", "network_availability"),
            GlossaryEntry("Outage Hour", "A whole or partial hour during which a network "
                          "element carried no traffic.", "Network", "network_availability"),
            GlossaryEntry("Contact", "One inbound customer interaction through any care "
                          "channel.", "Care", "first_contact_resolution"),
            GlossaryEntry("Wrap Time", "After-call work recorded by the agent before the next "
                          "contact.", "Care", "average_handle_time"),
            GlossaryEntry("Billing Cycle", "The monthly cut-off group an account is billed in.",
                          "Billing & Collections"),
        ),
        regulatory=(
            RegulatoryPattern("Regulatory Return", "Communications regulator",
                              "Quality-of-service and complaint statistics are published."),
            RegulatoryPattern("Outage Report", "Network resilience reporting",
                              "Major outages must be notified; low run count, high consequence."),
        ),
    )


# --------------------------------------------------------------------------
# Public sector
# --------------------------------------------------------------------------

def _public_sector() -> IndustryAccelerator:
    return IndustryAccelerator(
        key="public_sector", label="Public Sector",
        backbone=("Citizen", "Case", "Programme", "Payment", "Facility"),
        domains=("Citizen", "Casework", "Benefits & Payments", "Programmes", "Finance",
                 "Regulatory"),
        personas={
            "Casework Operations": "Casework team leader",
            "Finance": "Finance business partner",
            "Benefits Delivery": "Benefits delivery lead",
            "Programme Management": "Programme manager",
            "Contact Centre": "Contact centre manager",
            "Audit & Assurance": "Assurance lead",
            "Executive": "Executive sponsor",
        },
        steward_roles={
            "Citizen": "Citizen Data Steward (identity service owner)",
            "Casework": "Head of Casework Performance",
            "Benefits & Payments": "Benefits Payment Assurance Lead",
            "Programmes": "Programme Reporting Lead",
            "Finance": _FINANCE_STEWARD,
            "Regulatory": "Statutory Reporting Manager",
        },
        kpis=(
            KpiEntry("Case Clearance Time", "case_clearance_time",
                     "AVG(decision date - receipt date) for cases decided in the period",
                     "AVG", "Case", "Casework",
                     ("working versus calendar days", "clock stopped while awaiting evidence in some reports")),
            KpiEntry("Cases Within Standard", "cases_within_standard",
                     "cases decided within the service standard / cases decided x 100", "RATIO",
                     "Case", "Casework", ("standard is 20 working days in one report and 28 in another",),
                     regulatory=True),
            KpiEntry("Backlog", "backlog",
                     "COUNT(open cases) older than the service standard at period end", "COUNT",
                     "Case", "Casework", ("suspended cases included in one variant",)),
            KpiEntry("Payment Accuracy", "payment_accuracy",
                     "payments made at the correct amount / payments made x 100", "RATIO",
                     "Payment", "Benefits & Payments",
                     ("underpayments excluded from error in some reports",), regulatory=True),
            KpiEntry("Overpayment Recovery Rate", "overpayment_recovery_rate",
                     "overpayments recovered in the period / overpayments identified x 100",
                     "RATIO", "Payment", "Benefits & Payments",
                     ("written-off overpayments removed from the denominator",)),
            KpiEntry("Budget Execution Rate", "budget_execution_rate",
                     "expenditure to date / approved budget x 100", "RATIO", "Programme",
                     "Finance", ("committed versus paid expenditure", "original versus revised budget")),
            KpiEntry("Grant Share of Budget", "grant_share_of_budget",
                     "grant expenditure / total programme expenditure x 100", "RATIO",
                     "Programme", "Finance", ()),
            KpiEntry("Appeal Success Rate", "appeal_success_rate",
                     "appeals upheld / appeals decided x 100", "RATIO", "Case", "Casework",
                     ("partially upheld counted as upheld in one variant",), regulatory=True),
            KpiEntry("Contact Resolution Rate", "contact_resolution_rate",
                     "contacts resolved at first contact / contacts x 100", "RATIO", "Citizen",
                     "Citizen", ("transfers counted as resolved in some reports",)),
            KpiEntry("Digital Take Up", "digital_take_up",
                     "applications received through the digital channel / applications x 100",
                     "RATIO", "Case", "Programmes", ("assisted digital counted as digital",)),
        ),
        glossary=_common_glossary("Citizen") + (
            GlossaryEntry("Citizen", "A person interacting with the department, identified "
                          "once through the identity service.", "Citizen"),
            GlossaryEntry("Case", "One application, claim or referral worked to a decision; "
                          "the grain of clearance and backlog.", "Casework", "case_clearance_time"),
            GlossaryEntry("Service Standard", "The published target time within which a case "
                          "should be decided.", "Casework", "cases_within_standard"),
            GlossaryEntry("Benefit Award", "A decision entitling a citizen to a payment "
                          "stream at a stated rate and period.", "Benefits & Payments",
                          "payment_accuracy"),
            GlossaryEntry("Overpayment", "A payment above entitlement, whether from official "
                          "error, claimant error or fraud.", "Benefits & Payments",
                          "overpayment_recovery_rate"),
            GlossaryEntry("Programme", "A budgeted activity with an accounting officer, a "
                          "business case and outcome measures.", "Programmes",
                          "budget_execution_rate"),
            GlossaryEntry("Committed Expenditure", "Spend contractually obligated but not yet "
                          "paid.", "Finance", "budget_execution_rate"),
            GlossaryEntry("Appeal", "A formal challenge to a case decision heard by an "
                          "independent body.", "Casework", "appeal_success_rate"),
            GlossaryEntry("Working Day", "A weekday that is not a public holiday, used for "
                          "service standards.", "Casework", "case_clearance_time"),
            GlossaryEntry("Assisted Digital", "An application completed through the digital "
                          "channel with staff support.", "Programmes", "digital_take_up"),
        ),
        regulatory=(
            RegulatoryPattern("Statutory", "Parliamentary or statutory return",
                              "Laid before the legislature; one run per return."),
            RegulatoryPattern("Audit", "External audit",
                              "Payment accuracy feeds the audited accounts qualification."),
            RegulatoryPattern("Freedom of Information", "Information rights",
                              "Figures released on request are quoted publicly."),
        ),
    )


# --------------------------------------------------------------------------
# Generic / cross-industry
# --------------------------------------------------------------------------

def _generic() -> IndustryAccelerator:
    return IndustryAccelerator(
        key="generic", label="Generic / Cross-industry",
        backbone=("Customer", "Account", "Order", "Product", "Location"),
        domains=("Customer", "Receivables & Collections", "Sales & Orders", "Operations",
                 "Finance", "Compliance"),
        personas={
            "Collections": "Collections manager",
            "Finance": "Finance business partner",
            "Customer Operations": "Customer operations lead",
            "Sales": "Sales operations manager",
            "Operations": "Operations manager",
            "Compliance": "Compliance officer",
            "Executive": "Executive sponsor",
        },
        steward_roles={
            "Customer": _CUSTOMER_STEWARD,
            "Receivables & Collections": "Head of Receivables",
            "Sales & Orders": "Order Management Data Lead",
            "Operations": "Operations Reporting Lead",
            "Finance": _FINANCE_STEWARD,
            "Compliance": "Compliance Reporting Manager",
        },
        kpis=(
            KpiEntry("Days Sales Outstanding", "days_sales_outstanding",
                     "period-end receivable / billed revenue x days in period", "RATIO",
                     "Account", "Finance", _DSO_CONFLICTS),
            KpiEntry("Overdue Balance", "overdue_balance",
                     "SUM(balance) past due date at period end", "SUM", "Account",
                     "Receivables & Collections", _AGING_CONFLICTS),
            KpiEntry("Write Off Rate", "write_off_rate",
                     "write-offs in the period / billed revenue x 100", "RATIO", "Account",
                     "Finance", ("gross versus net of recoveries",)),
            KpiEntry("Net Revenue", "net_revenue",
                     "SUM(billed revenue) - SUM(credits) in the fiscal period", "SUM", "Account",
                     "Finance", ("posting date versus invoice date",), regulatory=True),
            KpiEntry("Order Fill Rate", "order_fill_rate",
                     "order lines shipped complete / order lines x 100", "RATIO", "Order",
                     "Sales & Orders", ("first shipment versus final shipment as the basis",)),
            KpiEntry("Complaint Rate", "complaint_rate",
                     "complaints / customer contacts x 100", "RATIO", "Customer", "Customer",
                     _COMPLAINT_CONFLICTS),
            KpiEntry("Active Customers", "active_customers",
                     "COUNT DISTINCT customer with an order in the trailing 12 months",
                     "COUNT DISTINCT", "Customer", "Customer",
                     ("trailing 12 versus trailing 24 months as the activity window",)),
            KpiEntry("Incident Rate", "incident_rate",
                     "operational incidents / 1000 transactions", "RATIO", "Location",
                     "Operations", ()),
        ),
        glossary=_common_glossary("Customer") + (
            GlossaryEntry("Account", "A billing relationship with a customer; the grain of "
                          "receivables.", "Receivables & Collections", "overdue_balance"),
            GlossaryEntry("Order", "A confirmed customer request for products or services.",
                          "Sales & Orders", "order_fill_rate"),
            GlossaryEntry("Overdue", "A receivable whose due date has passed without full "
                          "payment.", "Receivables & Collections", "overdue_balance"),
            GlossaryEntry("Write Off", "A receivable removed from the ledger as uncollectable.",
                          "Finance", "write_off_rate"),
            GlossaryEntry("Billed Revenue", "Invoice value issued in the period, before "
                          "credits.", "Finance", "net_revenue"),
            GlossaryEntry("Active Customer", "A customer with at least one order in the "
                          "activity window.", "Customer", "active_customers"),
            GlossaryEntry("Contact", "One inbound customer interaction through any channel.",
                          "Customer", "complaint_rate"),
        ),
        regulatory=(
            RegulatoryPattern("Compliance Return", "Sector regulator",
                              "Submitted on a statutory cadence; consequence outweighs run count."),
            RegulatoryPattern("Statutory", "Financial reporting",
                              "Audited figures; one run per reporting cycle."),
        ),
    )


# --------------------------------------------------------------------------
# Registry and accessors
# --------------------------------------------------------------------------

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
ACCELERATOR_KEYS = tuple(_BUILDERS)

# Kept in step with dpre/synth/industries.py::get_pack so the same word means
# the same industry on both paths; a test asserts the two resolve alike.
_ALIASES = {
    "energy": "utility", "utilities": "utility", "utility_energy": "utility",
    "cpg": "retail", "retail_cpg": "retail", "consumer": "retail",
    "bank": "banking", "financial_services": "banking",
    "health": "healthcare", "provider": "healthcare",
    "gov": "public_sector", "government": "public_sector", "public": "public_sector",
    "telco": "telecom", "communications": "telecom",
    "mfg": "manufacturing", "industrial": "manufacturing",
    "insurer": "insurance",
}


def normalize_key(key: str | None) -> str:
    key = (key or "generic").strip().lower().replace("-", "_").replace(" ", "_")
    key = _ALIASES.get(key, key)
    if key not in _BUILDERS:
        raise KeyError(f"unknown industry '{key}'; known: {', '.join(ACCELERATOR_KEYS)}")
    return key


def get_accelerator(key: str | None) -> IndustryAccelerator:
    return _BUILDERS[normalize_key(key)]()


def list_accelerators() -> list[dict]:
    out = []
    for key in ACCELERATOR_KEYS:
        acc = get_accelerator(key)
        out.append({"key": key, "label": acc.label, "backbone": list(acc.backbone),
                    "domains": list(acc.domains), "kpis": len(acc.kpis),
                    "glossary_terms": len(acc.glossary),
                    "regulatory_patterns": len(acc.regulatory)})
    return out


def backbone_for_industry(key: str | None) -> list[str]:
    """The conformed backbone to pass as ``build_graph(bundle, backbone=...)``."""
    return list(get_accelerator(key).backbone)


#: Words that carry no meaning in an organisation-unit name. A client writes
#: "Credit and Collections Team" where the accelerator says "Credit &
#: Collections"; the two name the same people, so the match must survive the
#: punctuation and the filler.
_UNIT_STOPWORDS = frozenset({
    "and", "the", "of", "for", "team", "teams", "dept", "department", "departments",
    "group", "unit", "units", "division", "function", "office", "services", "service",
    "centre", "center", "coe", "bu",
})


def _unit_tokens(value: str) -> frozenset[str]:
    """Meaningful words in a unit name, with '&' read as 'and' and dropped."""
    cleaned = (value or "").replace("&", " and ").casefold()
    words = re.split(r"[^a-z0-9]+", cleaned)
    return frozenset(w for w in words if w and w not in _UNIT_STOPWORDS)


def _best_match(mapping: dict[str, str], wanted: str) -> str:
    """Exact name first, then the entry whose meaningful words line up.

    A partial overlap is not enough: 'Credit Risk' must not answer for
    'Credit & Collections'. One name's words have to contain the other's, which
    is true for a renamed or padded unit and false for a different one.
    """
    target = (wanted or "").strip()
    if not target:
        return ""
    for name, role in mapping.items():
        if name.casefold() == target.casefold():
            return role
    wanted_tokens = _unit_tokens(target)
    if not wanted_tokens:
        return ""
    best, best_size = "", 0
    for name, role in mapping.items():
        tokens = _unit_tokens(name)
        if not tokens:
            continue
        if (tokens <= wanted_tokens or wanted_tokens <= tokens) and len(tokens) > best_size:
            best, best_size = role, len(tokens)
    return best


def persona_for(key: str | None, business_unit: str) -> str:
    """Role title for a business unit, in the client's own words; '' if none."""
    return _best_match(get_accelerator(key).personas, business_unit)


def steward_role_for(key: str | None, domain: str) -> str:
    """Role-shaped steward for a domain, for the generator and for gap hints."""
    return _best_match(get_accelerator(key).steward_roles, domain)


def kpi_catalogue(key: str | None) -> list[dict]:
    return [k.__dict__ | {"typical_conflicts": list(k.typical_conflicts)}
            for k in get_accelerator(key).kpis]


def regulatory_patterns(key: str | None) -> list[RegulatoryPattern]:
    return list(get_accelerator(key).regulatory)


def is_regulatory_report(key: str | None, report_name: str) -> RegulatoryPattern | None:
    """The pattern a report name matches, or None: a hint to mark it decision-critical."""
    low = (report_name or "").casefold()
    for pattern in get_accelerator(key).regulatory:
        if pattern.pattern.casefold() in low:
            return pattern
    return None


def starter_glossary_records(key: str | None) -> list[GlossaryTermRecord]:
    """Starter terms with real definitions, status ``Starter`` and no steward."""
    acc = get_accelerator(key)
    out = []
    for index, entry in enumerate(acc.glossary, start=1):
        out.append(GlossaryTermRecord(
            term_id=f"ST-{acc.key.upper()}-{index:03d}", term=entry.term,
            definition=entry.definition, domain=entry.domain, sub_domain="",
            steward="", status=STARTER_STATUS))
    return out


def merge_starter_glossary(bundle: ExtractBundle, key: str | None) -> int:
    """Add starter terms the client's glossary lacks; returns how many were added.

    A client's own term always wins: matching is case-insensitive on the term
    name and an existing entry is never overwritten. The added rows carry the
    ``Starter`` status so a card can say the definition is ours, not theirs.
    """
    present = {t.term.strip().casefold() for t in bundle.glossary}
    added = 0
    for record in starter_glossary_records(key):
        if record.term.casefold() in present:
            continue
        bundle.glossary.append(record)
        present.add(record.term.casefold())
        added += 1
    return added


def accelerator_sheets(key: str | None) -> dict[str, list[list]]:
    """The accelerator as workbook tabs, for the Start tab download and for clients."""
    acc = get_accelerator(key)
    readme = [["Field", "Value"],
              ["Industry", acc.label],
              ["Key", acc.key],
              ["What this is", "A starter pack: conformed backbone, KPI dictionary, starter "
                               "glossary, persona map and regulatory-report patterns."],
              ["What this is not", "Client data. Stewards are blank on purpose; a real steward "
                                   "is a fact the client supplies."],
              ["How to use", "Adopt or amend each row in the workshop; the engine merges "
                             "starter terms only where the client glossary has none."]]
    backbone = [["Position", "Entity"]] + [[i, e] for i, e in enumerate(acc.backbone, start=1)]
    kpis = [["Label", "Canonical name", "Formula", "Aggregation", "Grain", "Domain",
             "Regulatory", "Typical conflicts"]]
    kpis += [[k.label, k.canonical_name, k.formula, k.aggregation, k.grain, k.domain,
              "yes" if k.regulatory else "no", "; ".join(k.typical_conflicts)] for k in acc.kpis]
    glossary = [["Term", "Definition", "Domain", "Related KPI", "Steward", "Status"]]
    glossary += [[g.term, g.definition, g.domain, g.related_kpi, "", STARTER_STATUS]
                 for g in acc.glossary]
    personas = [["Business unit", "Persona (role)"]] + [[bu, r] for bu, r in acc.personas.items()]
    stewards = [["Domain", "Steward role (title, not a person)"]]
    stewards += [[d, r] for d, r in acc.steward_roles.items()]
    regulatory = [["Report name pattern", "Regime", "Why decision-critical"]]
    regulatory += [[r.pattern, r.regime, r.why_decision_critical] for r in acc.regulatory]
    return {
        "README": readme, "Backbone": backbone, "KPI_Dictionary": kpis,
        "Starter_Glossary": glossary, "Personas": personas, "Steward_Roles": stewards,
        "Regulatory_Patterns": regulatory,
    }


def write_accelerator_workbook(key: str | None, path):
    from ..util.xlsx import write_workbook
    return write_workbook(path, accelerator_sheets(key))


def all_kpi_labels(keys: Iterable[str] | None = None) -> dict[str, list[str]]:
    """Canonical name -> industries that define it; useful for cross-industry review."""
    out: dict[str, list[str]] = {}
    for key in keys or ACCELERATOR_KEYS:
        for kpi in get_accelerator(key).kpis:
            out.setdefault(kpi.canonical_name, []).append(key)
    return out
