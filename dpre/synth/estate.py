"""Builds the synthetic physical estate: systems, tables, columns, glossary.

The estate is what a catalog (Collibra or Alation) would hold. KPI lineage
points into it, and its deliberate gaps - missing definitions, unassigned
stewards, a sunset system, columns the lineage references but the catalog does
not hold - are the defects the engine is built to find.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from .industries import IndustryPack

FACT_DOMAIN_SLOT = (1, 1, 1, 2, 0, 4, 3, 1)
DIM_DOMAIN_SLOT = (0, 0, 0, 2, 2, 3, 4, 4, 4)

PII_HINTS = (
    "name", "email", "address", "phone", "birth", "dob", "national", "tax_identifier",
    "insurance_id", "medical_record", "msisdn", "imei", "card_number", "postcode",
    "credit_score", "patient", "citizen", "policyholder", "staff_number",
)
RESTRICTED_HINTS = ("national", "tax_identifier", "medical_record", "date_of_birth",
                    "birth", "insurance_id", "card_number", "credit_score")

DATA_TYPES = {
    "key": "NUMBER(18,0)",
    "amount": "NUMBER(18,2)",
    "count": "NUMBER(18,0)",
    "rate": "NUMBER(9,4)",
    "duration": "NUMBER(12,2)",
    "date": "DATE",
    "timestamp": "TIMESTAMP_NTZ",
    "code": "VARCHAR(30)",
    "flag": "BOOLEAN",
    "text": "VARCHAR(200)",
}

FIRST_NAMES = (
    "Amara", "Bela", "Caitlin", "Devon", "Elena", "Farid", "Grace", "Hector", "Imani",
    "Jonas", "Kiran", "Lucia", "Marcus", "Nadia", "Omar", "Priya", "Quentin", "Rosa",
    "Samir", "Tessa", "Ulrik", "Vera", "Wesley", "Xiomara", "Yusuf", "Zara",
)
LAST_NAMES = (
    "Adeyemi", "Bianchi", "Caldwell", "Dlamini", "Espinoza", "Fischer", "Gallagher",
    "Hoffmann", "Ibrahim", "Jansen", "Kowalski", "Lindqvist", "Moreau", "Novak",
    "Okafor", "Pereira", "Quintero", "Rasmussen", "Silva", "Tanaka", "Ustinov",
    "Vasquez", "Whitfield", "Xu", "Yilmaz", "Zielinski",
)


@dataclass
class Column:
    system: str
    database: str
    schema: str
    table: str
    column: str
    role: str                 # key | measure | date | attribute
    data_type: str
    label: str
    kind: str = "text"
    business_term: str = ""
    definition: str = ""
    pii_flag: bool = False
    classification: str = "Internal"
    nullable: bool = True
    primary_key: bool = False
    foreign_key: str = ""
    quality_score: float = 0.0
    certification_status: str = ""

    @property
    def fqn(self) -> str:
        return f"{self.system}.{self.database}.{self.schema}.{self.table}.{self.column}"

    @property
    def table_fqn(self) -> str:
        return f"{self.system}.{self.database}.{self.schema}.{self.table}"


@dataclass
class Table:
    system: str
    database: str
    schema: str
    table: str
    kind: str                 # fact | dim | reference | reporting | staging
    entity: str
    domain: str
    sub_domain: str
    sor: bool
    lifecycle: str
    owner: str
    steward: str
    columns: list[Column] = field(default_factory=list)
    row_count: int = 0
    sunset_date: str = ""
    successor_system: str = ""
    source_table_fqn: str = ""      # for reporting views

    @property
    def fqn(self) -> str:
        return f"{self.system}.{self.database}.{self.schema}.{self.table}"


@dataclass
class Estate:
    pack: IndustryPack
    tables: dict[str, Table] = field(default_factory=dict)
    columns: dict[str, Column] = field(default_factory=dict)
    glossary: list[dict] = field(default_factory=list)
    lineage: list[dict] = field(default_factory=list)
    stewards_by_domain: dict[str, str] = field(default_factory=dict)
    owners_by_domain: dict[str, str] = field(default_factory=dict)
    fact_tables: list[Table] = field(default_factory=list)
    dim_tables: list[Table] = field(default_factory=list)
    reporting_tables: dict[str, Table] = field(default_factory=dict)

    def column(self, fqn: str) -> Column | None:
        return self.columns.get(fqn)

    def table_of(self, fqn: str) -> Table | None:
        return self.tables.get(fqn)


def _person(rng: random.Random) -> str:
    return f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"


def _classify(column_name: str, role: str) -> tuple[bool, str]:
    low = column_name.lower()
    pii = any(h in low for h in PII_HINTS)
    if any(h in low for h in RESTRICTED_HINTS):
        return True, "Restricted"
    if pii:
        return True, "Confidential"
    if role == "measure":
        return False, "Internal"
    return False, "Internal"


def _definition_for(label: str, table: Table, kind: str) -> str:
    if kind == "measure":
        return f"{label} recorded on {table.table} in the {table.domain} domain, at {table.entity} grain."
    if kind == "key":
        return f"Surrogate key identifying a {table.entity} row in {table.table}."
    if kind == "date":
        return f"{label} associated with the {table.entity} record in {table.table}."
    return f"{label} attribute of {table.entity} maintained in {table.system}."


def build_estate(pack: IndustryPack, rng: random.Random, params) -> Estate:
    estate = Estate(pack=pack)
    domains = pack.domains

    for domain in domains:
        estate.stewards_by_domain[domain] = _person(rng)
        estate.owners_by_domain[domain] = _person(rng)

    def add_table(tbl: Table) -> Table:
        estate.tables[tbl.fqn] = tbl
        return tbl

    def add_column(tbl: Table, name: str, role: str, kind: str, label: str,
                   primary_key: bool = False, foreign_key: str = "") -> Column:
        pii, classification = _classify(name, role)
        col = Column(
            system=tbl.system, database=tbl.database, schema=tbl.schema, table=tbl.table,
            column=name, role=role, data_type=DATA_TYPES.get(kind, "VARCHAR(200)"),
            label=label, kind=kind, pii_flag=pii, classification=classification,
            nullable=not primary_key, primary_key=primary_key, foreign_key=foreign_key,
            quality_score=round(rng.uniform(0.55, 0.99), 2),
            certification_status=rng.choice(["Certified", "Candidate", "", "", "Certified"]),
        )
        tbl.columns.append(col)
        estate.columns[col.fqn] = col
        return col

    # ---- dimensions first, so facts can point their foreign keys at them ----
    dim_by_entity: dict[str, Table] = {}
    for i, dim in enumerate(pack.dims):
        system = pack.system(dim.system_index)
        domain = domains[DIM_DOMAIN_SLOT[i % len(DIM_DOMAIN_SLOT)] % len(domains)]
        tbl = add_table(Table(
            system=system.code, database=f"{system.code}DB", schema=dim.schema, table=dim.table,
            kind="dim", entity=dim.entity, domain=domain, sub_domain=f"{dim.entity} Master",
            sor=system.sor, lifecycle=system.lifecycle,
            owner=estate.owners_by_domain[domain], steward=estate.stewards_by_domain[domain],
            row_count=rng.randint(1_200, 900_000) if dim.entity not in ("Calendar",) else 4_000,
        ))
        key_col = f"{dim.entity.lower().replace(' ', '_')}_key"
        add_column(tbl, key_col, "key", "key", f"{dim.entity} Key", primary_key=True)
        for attr in dim.attributes:
            kind = ("date" if attr.endswith("_date") else
                    "timestamp" if attr.endswith("_at") or attr.endswith("_ts") else
                    "flag" if attr.endswith("_flag") else
                    "code" if attr.endswith("_code") or attr.endswith("_number") else "text")
            add_column(tbl, attr, "attribute", kind, _label(attr))
        # a few filler attributes so the catalog is wider than the BI estate
        for n in range(params.dim_filler_columns):
            add_column(tbl, f"{dim.entity.lower().replace(' ', '_')}_attribute_{n+1}",
                       "attribute", "text", f"{dim.entity} Attribute {n + 1}")
        estate.dim_tables.append(tbl)
        dim_by_entity.setdefault(dim.entity, tbl)

    # ---- facts ----
    for i, fact in enumerate(pack.facts):
        system = pack.system(fact.system_index)
        domain = domains[FACT_DOMAIN_SLOT[i % len(FACT_DOMAIN_SLOT)] % len(domains)]
        tbl = add_table(Table(
            system=system.code, database=f"{system.code}DB", schema=fact.schema, table=fact.table,
            kind="fact", entity=fact.grain, domain=domain,
            sub_domain=f"{fact.grain} Activity", sor=system.sor, lifecycle=system.lifecycle,
            owner=estate.owners_by_domain[domain], steward=estate.stewards_by_domain[domain],
            row_count=rng.randint(500_000, 90_000_000),
            sunset_date="2027-03-31" if system.lifecycle == "sunset" else "",
        ))
        add_column(tbl, f"{fact.table.lower().replace('fct_', '')}_row_key", "key", "key",
                   f"{_label(fact.table)} Row Key", primary_key=True)
        # A fact carries foreign keys only to entities at or above its grain: a
        # premise-grain fact has no meter key. That is what lets grain inference
        # read the grain off the catalog rather than be told it.
        if fact.grain in pack.backbone:
            reachable = pack.backbone[: pack.backbone.index(fact.grain) + 1]
        else:
            reachable = list(pack.backbone)
        existing = {c.column for c in tbl.columns}
        for entity in reachable:
            ref = dim_by_entity.get(entity)
            key_name = f"{entity.lower().replace(' ', '_')}_key"
            if key_name in existing:
                continue          # the surrogate key already carries this name
            existing.add(key_name)
            add_column(tbl, key_name, "key", "key", f"{entity} Key",
                       foreign_key=f"{ref.fqn}.{key_name}" if ref else "")
        for date_col in fact.date_columns:
            kind = "timestamp" if date_col.endswith("_ts") else "date"
            add_column(tbl, date_col, "date", kind, _label(date_col))
        for measure in fact.measures:
            add_column(tbl, measure.column, "measure", measure.kind, measure.label)
        for attr in ("status_code", "source_system_code", "created_by", "updated_at",
                     "batch_id", "record_hash"):
            kind = ("timestamp" if attr.endswith("_at") else
                    "code" if attr.endswith("_code") else "text")
            add_column(tbl, attr, "attribute", kind, _label(attr))
        for n in range(params.fact_filler_columns):
            add_column(tbl, f"{fact.table.lower()}_attribute_{n+1}", "attribute", "text",
                       f"{_label(fact.table)} Attribute {n + 1}")
        estate.fact_tables.append(tbl)

    # ---- reference tables ----
    for name in pack.reference_tables:
        system = pack.system(0)
        domain = domains[0]
        tbl = add_table(Table(
            system=system.code, database=f"{system.code}DB", schema=pack.facts[0].schema,
            table=name, kind="reference", entity=_label(name), domain=domain,
            sub_domain="Reference", sor=True, lifecycle="active",
            owner=estate.owners_by_domain[domain], steward=estate.stewards_by_domain[domain],
            row_count=rng.randint(12, 8_000),
        ))
        add_column(tbl, f"{name.lower().replace('ref_', '')}_code", "key", "code",
                   f"{_label(name)} Code", primary_key=True)
        for attr in ("description", "sort_order", "active_flag", "effective_date", "end_date"):
            kind = ("date" if attr.endswith("_date") else
                    "flag" if attr.endswith("_flag") else
                    "count" if attr.endswith("_order") else "text")
            add_column(tbl, attr, "attribute", kind, _label(attr))
        estate.dim_tables.append(tbl)

    # ---- reporting views: the layer Cognos actually points at (exercises ER-3) ----
    core = pack.system(0)
    for fact_tbl in estate.fact_tables[:params.reporting_view_count]:
        view = add_table(Table(
            system=core.code, database=f"{core.code}DB", schema="RPT",
            table=f"V_{fact_tbl.table.replace('FCT_', '')}", kind="reporting",
            entity=fact_tbl.entity, domain=fact_tbl.domain, sub_domain="Reporting layer",
            sor=False, lifecycle="active", owner=fact_tbl.owner, steward=fact_tbl.steward,
            row_count=fact_tbl.row_count, source_table_fqn=fact_tbl.fqn,
        ))
        for col in fact_tbl.columns:
            if col.role in ("measure", "key") or col.column.endswith("_date"):
                new_col = add_column(view, col.column, col.role, col.kind, col.label,
                                     primary_key=col.primary_key)
                estate.lineage.append({
                    "level": "column",
                    "src": col.fqn, "tgt": new_col.fqn,
                    "transformation": "1:1 view projection",
                })
        estate.lineage.append({
            "level": "table", "src": fact_tbl.fqn, "tgt": view.fqn,
            "transformation": "reporting view",
        })
        estate.reporting_tables[fact_tbl.fqn] = view

    # ---- staging filler tables so the catalog is wider than the BI estate ----
    landing_targets = estate.fact_tables + [t for t in estate.dim_tables if t.kind == "dim"]
    for i in range(params.staging_tables):
        target = landing_targets[i % len(landing_targets)]
        system = pack.system(i % 4)
        domain = target.domain
        tbl = add_table(Table(
            system=system.code, database=f"{system.code}DB", schema="STG",
            table=f"STG_{target.table.replace('FCT_', '').replace('DIM_', '')}_{i+1:02d}",
            kind="staging", entity=target.entity, domain=domain, sub_domain="Staging",
            sor=False, lifecycle=system.lifecycle, owner=estate.owners_by_domain[domain],
            steward=estate.stewards_by_domain[domain], row_count=rng.randint(10_000, 4_000_000),
        ))
        add_column(tbl, "staging_key", "key", "key", "Staging Key", primary_key=True)
        landed = [c for c in target.columns if c.role in ("measure", "key", "date")]
        for n, target_col in enumerate(landed[:params.staging_columns]):
            src_col = add_column(tbl, f"src_{target_col.column}", "attribute", target_col.kind,
                                 f"Source {target_col.label}")
            estate.lineage.append({
                "level": "column", "src": src_col.fqn, "tgt": target_col.fqn,
                "transformation": "batch load",
            })
        estate.lineage.append({
            "level": "table", "src": tbl.fqn, "tgt": target.fqn,
            "transformation": "batch load",
        })

    # foreign-key edges make the catalog lineage tab look like a real extract
    for fact_tbl in estate.fact_tables:
        for col in fact_tbl.columns:
            if col.foreign_key and col.foreign_key in estate.columns:
                estate.lineage.append({
                    "level": "column", "src": col.foreign_key, "tgt": col.fqn,
                    "transformation": "foreign key",
                })

    _attach_glossary(estate, rng, params)
    _diverge_business_terms(estate, rng, params)
    _apply_catalog_gaps(estate, rng, params)
    return estate


def _label(raw: str) -> str:
    cleaned = raw.replace("FCT_", "").replace("DIM_", "").replace("REF_", "")
    return " ".join(part.capitalize() for part in cleaned.replace("_", " ").split())


def _attach_glossary(estate: Estate, rng: random.Random, params) -> None:
    """Create business terms for measures and the attributes people ask about."""
    seen: dict[str, dict] = {}
    for table in estate.tables.values():
        if table.kind in ("staging", "reporting"):
            continue
        for col in table.columns:
            if col.role in ("measure", "date") or (
                    col.role == "attribute" and "attribute_" not in col.column):
                term_name = col.label
                key = term_name.lower()
                if key not in seen and len(seen) < params.glossary_terms:
                    steward = estate.stewards_by_domain.get(table.domain, "")
                    seen[key] = {
                        "term_id": f"BT-{len(seen)+1:04d}",
                        "term": term_name,
                        "definition": _definition_for(term_name, table, col.role),
                        "domain": table.domain,
                        "sub_domain": table.sub_domain,
                        "steward": steward,
                        "status": rng.choice(["Approved", "Approved", "Draft", "Approved"]),
                    }
                if key in seen:
                    col.business_term = seen[key]["term"]
                    col.definition = seen[key]["definition"]
    estate.glossary = list(seen.values())


TERM_SYNONYMS = {
    "amount": "Value", "balance": "Position", "count": "Volume", "revenue": "Income",
    "cost": "Spend", "days": "Ageing", "rate": "Ratio", "arrears": "Overdue",
    "payment": "Remittance", "charge": "Billing", "loss": "Impairment",
    "premium": "Subscription", "sales": "Turnover", "spend": "Outlay",
}


def _diverge_business_terms(estate: Estate, rng: random.Random, params) -> None:
    """Give some columns a business term that reads nothing like the column name.

    Without this every term is the column label title-cased, ER-2 resolves
    everything and ER-5 never earns its keep - which is not what a real glossary
    looks like.
    """
    measures = [c for c in estate.columns.values() if c.role == "measure" and c.business_term]
    if not measures:
        return
    count = max(1, int(len(measures) * params.divergent_term_rate))
    for column in rng.sample(measures, k=min(count, len(measures))):
        tokens = column.label.split()
        renamed = [TERM_SYNONYMS.get(t.lower(), t) for t in tokens]
        if [t.lower() for t in renamed] == [t.lower() for t in tokens]:
            renamed = renamed + ["Position"]
        term = " ".join(renamed)
        table = estate.tables[column.table_fqn]
        column.business_term = term
        column.definition = (f"{term}: the governed reading of {column.label.lower()} "
                             f"held on {table.table}.")
        estate.glossary.append({
            "term_id": f"BT-{len(estate.glossary)+1:04d}",
            "term": term,
            "definition": column.definition,
            "domain": table.domain,
            "sub_domain": table.sub_domain,
            "steward": estate.stewards_by_domain.get(table.domain, ""),
            "status": "Approved",
        })


def _apply_catalog_gaps(estate: Estate, rng: random.Random, params) -> None:
    """Plant the metadata-quality defects: missing definitions, no steward."""
    all_columns = [c for c in estate.columns.values()]
    rng.shuffle(all_columns)
    n_missing = int(len(all_columns) * params.missing_definition_rate)
    for col in all_columns[:n_missing]:
        col.business_term = ""
        col.definition = ""
    n_no_steward = int(len(estate.glossary) * params.unassigned_steward_rate)
    for term in rng.sample(estate.glossary, k=max(1, n_no_steward)):
        term["steward"] = ""
