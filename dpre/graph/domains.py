"""Data Domain nodes (specification section 4.1, review finding R-44).

The specification's graph has a Data Domain node with an owner, and section 9.1
names the candidate owner as "the Collibra owner of the dominant domain". The
catalog extract carries the domain only as a column attribute, so the node is
derived: one per data domain, its owner and steward taken by majority over the
domain's columns, with a disagreement flag when the catalog is not unanimous.

Ownership (accountable) and stewardship (responsible) stay distinct roles. The
domain owner is the escalation point when no steward can be resolved for a
metric, which is what turns a "steward unassigned" gap into a request that
names who was asked.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field

from ..models import KnowledgeGraph


@dataclass
class DomainNode:
    name: str
    owner: str = ""
    steward: str = ""
    tables: list[str] = field(default_factory=list)
    column_count: int = 0
    owner_votes: dict[str, int] = field(default_factory=dict)
    steward_votes: dict[str, int] = field(default_factory=dict)
    owner_disagreement: bool = False
    steward_disagreement: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def build_domain_nodes(graph: KnowledgeGraph) -> dict[str, DomainNode]:
    """One node per data domain, majority owner and steward, disagreement flagged."""
    owners: dict[str, Counter] = {}
    stewards: dict[str, Counter] = {}
    tables: dict[str, set[str]] = {}
    counts: Counter = Counter()
    for column in graph.columns.values():
        domain = column.domain or ""
        if not domain:
            continue
        counts[domain] += 1
        tables.setdefault(domain, set()).add(column.table_fqn)
        if column.owner_id:
            owners.setdefault(domain, Counter())[column.owner_id] += 1
        # The catalog's own steward on the column, before any glossary override,
        # is the domain-level signal; the glossary speaks per term, not per domain.
        steward = getattr(column, "_column_steward", None)
        steward = column.steward_id if steward is None else steward
        if steward:
            stewards.setdefault(domain, Counter())[steward] += 1

    out: dict[str, DomainNode] = {}
    for domain in sorted(counts):
        owner_votes = owners.get(domain, Counter())
        steward_votes = stewards.get(domain, Counter())
        out[domain] = DomainNode(
            name=domain,
            owner=_majority(owner_votes),
            steward=_majority(steward_votes),
            tables=sorted(tables.get(domain, set())),
            column_count=counts[domain],
            owner_votes=dict(owner_votes.most_common()),
            steward_votes=dict(steward_votes.most_common()),
            owner_disagreement=len(owner_votes) > 1,
            steward_disagreement=len(steward_votes) > 1,
        )
    return out


def _majority(votes: Counter) -> str:
    if not votes:
        return ""
    # Deterministic: most votes, then alphabetical, so a tie never depends on
    # extract order (R-44).
    return sorted(votes.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def domain_nodes(graph: KnowledgeGraph) -> dict[str, DomainNode]:
    """The graph's domain nodes, built on first use when the builder did not."""
    nodes = getattr(graph, "_domains", None)
    if nodes is None:
        nodes = build_domain_nodes(graph)
        setattr(graph, "_domains", nodes)
    return nodes
