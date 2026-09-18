# Changelog

Entries are grouped by what changed for someone using the engine, not by the
order the commits landed.

## 1.0.0

The first release that a consulting engagement could be run on.

### The engine

- Two entry paths, Manual and Automated, ending in the same `ExtractBundle`, so
  nothing downstream knows which was used. Nine industries.
- Nine chartered agents. Ingestor, Resolver, Canonicalizer, Clusterer, Scorer,
  Narrator and Critic find the backlog; Programme prices and sequences it;
  Assessor measures the run that produced it.
- Entity resolution ER-1 to ER-6, each with its own confidence, and a
  quarantine with reason codes for what will not resolve.
- Expressions and DAX measures parsed to an AST and fingerprinted as
  `SHA-256(aggregation + sorted operand columns + arithmetic shape)`. Cognos
  and Power BI fingerprint alike, so one KPI in two tools is one metric.
- Louvain clustering under a grain constraint, entity master extraction and
  consumer-aligned composites.
- Four scored dimensions from named features, every one of them backed by
  evidence rows. A score without evidence cannot be written.

### Governance

- Hash-chained decision ledger with append-only triggers, verified by
  recomputation. `dpre audit` names the first row that does not verify.
- Gates bind at acceptance, not only at scoring. A Blocked candidate cannot be
  Accepted; an Exploratory one needs a recorded consumer confirmation or an
  exception naming the gate, the rationale and a second approver.
- Decisions outlive the run. A confirmed consumer, an adjudicated conflict, an
  accepted metric name and a report marked decision-critical are keyed by
  lineage and re-applied to the next run.
- Identity is authenticated rather than asserted: a trusted single sign-on
  proxy, a bearer token, or a loopback development name. A non-loopback bind
  without an identity source is refused at startup.
- Roles map to actions and actions scope to domains. A council member cannot
  approve a weight version trained on their own decisions.

### The programme layer

- Build effort with its drivers, benefit attributed once across the estate,
  payback, three-year net present value, delivery waves that respect
  dependencies, and a RAID log.
- Status against the specification's section 14.2 measures, with a RAG per
  measure and an honest "not measurable" where the engine cannot see the input.
- Planned against realised benefit, tracked in units a client can verify.

### Assurance

- Measured detection: planted against detected per defect class, with the
  misses named. Recall runs 82 to 96 per cent across the nine packs.
- Input data quality by input and dimension, remediation units with an owner
  and a priority, a stewardship register of questions rather than assignments,
  and a written bias assessment with the mitigation in code.
- A controls matrix of 24 controls, each verified against the code, with DMBOK,
  DCAM and COBIT mappings and a RACI.
- 93 assumptions and the eight open decisions D-01 to D-08, each naming the
  position the engine takes meanwhile.

### Deliverables

- Executive summary in Markdown and HTML, a backlog workbook and a printable
  dossier per candidate, cut in the same process as the run. A synthetic run is
  banner-marked on every page.
- Industry accelerators: a curated backbone, KPI dictionary, personas, steward
  roles, regulatory patterns and starter glossary per industry.
- An extract request pack generated from the ingestion contract itself, so it
  cannot ask for a column the engine does not read.

### Known limitations

Read the "Deliberate limitations" section of the README, the bias register
(`dpre assess --bias`) and `docs/assumptions-register.md`. Every money figure is
illustrative until a client's own rates replace the shipped assumptions.
