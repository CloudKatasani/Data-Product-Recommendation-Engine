"""Louvain community detection on a weighted undirected graph (stdlib only).

Modularity optimization in two repeated phases: greedy local moves, then graph
aggregation. The resolution parameter controls cluster size and is swept at
three settings so a reviewer can compare (specification section 6.1).
"""
from __future__ import annotations

import random
from collections import defaultdict


class Graph:
    """Weighted undirected graph with self-loops, as Louvain aggregation needs."""

    def __init__(self) -> None:
        self.adjacency: dict[str, dict[str, float]] = defaultdict(dict)
        self.total_weight: float = 0.0

    def add_edge(self, a: str, b: str, weight: float = 1.0) -> None:
        if weight <= 0:
            return
        self.adjacency[a][b] = self.adjacency[a].get(b, 0.0) + weight
        if a != b:
            self.adjacency[b][a] = self.adjacency[b].get(a, 0.0) + weight
        self.total_weight += weight

    def add_node(self, node: str) -> None:
        self.adjacency.setdefault(node, {})

    @property
    def nodes(self) -> list[str]:
        return list(self.adjacency)

    def degree(self, node: str) -> float:
        neighbours = self.adjacency.get(node, {})
        return sum(neighbours.values()) + neighbours.get(node, 0.0)

    def self_loop(self, node: str) -> float:
        return self.adjacency.get(node, {}).get(node, 0.0)


def modularity(graph: Graph, communities: dict[str, str], resolution: float = 1.0) -> float:
    m = graph.total_weight
    if m <= 0:
        return 0.0
    internal: dict[str, float] = defaultdict(float)
    degrees: dict[str, float] = defaultdict(float)
    for node, neighbours in graph.adjacency.items():
        community = communities[node]
        degrees[community] += graph.degree(node)
        for neighbour, weight in neighbours.items():
            if communities.get(neighbour) == community:
                internal[community] += weight if neighbour != node else 2 * weight
    total = 0.0
    for community, weight in internal.items():
        total += weight / (2 * m) - resolution * (degrees[community] / (2 * m)) ** 2
    return total


def _one_level(graph: Graph, resolution: float, rng: random.Random) -> dict[str, str]:
    communities = {node: node for node in graph.nodes}
    degrees = {node: graph.degree(node) for node in graph.nodes}
    community_degree = dict(degrees)
    m2 = 2 * graph.total_weight
    if m2 <= 0:
        return communities

    improved = True
    passes = 0
    while improved and passes < 30:
        improved = False
        passes += 1
        nodes = graph.nodes
        rng.shuffle(nodes)
        for node in nodes:
            current = communities[node]
            degree = degrees[node]
            community_degree[current] -= degree

            weights: dict[str, float] = defaultdict(float)
            for neighbour, weight in graph.adjacency[node].items():
                if neighbour == node:
                    continue
                weights[communities[neighbour]] += weight

            best_community, best_gain = current, 0.0
            base = weights.get(current, 0.0) - resolution * community_degree[current] * degree / m2
            for community, weight in weights.items():
                gain = weight - resolution * community_degree[community] * degree / m2
                if gain - base > best_gain + 1e-12:
                    best_gain = gain - base
                    best_community = community

            community_degree[best_community] += degree
            if best_community != current:
                communities[node] = best_community
                improved = True
    return communities


def _aggregate(graph: Graph, communities: dict[str, str]) -> Graph:
    aggregated = Graph()
    for node, neighbours in graph.adjacency.items():
        source = communities[node]
        aggregated.add_node(source)
        for neighbour, weight in neighbours.items():
            target = communities[neighbour]
            if source == target and node > neighbour:
                continue           # each internal edge once
            if source == target and node == neighbour:
                aggregated.add_edge(source, target, weight)
            elif source == target:
                aggregated.add_edge(source, target, weight)
            elif node < neighbour:
                aggregated.add_edge(source, target, weight)
    return aggregated


def louvain(graph: Graph, resolution: float = 1.0, seed: int = 17,
            max_levels: int = 8) -> dict[str, str]:
    """Return ``{node: community_id}``."""
    rng = random.Random(seed)
    if not graph.nodes:
        return {}
    mapping = {node: node for node in graph.nodes}
    current = graph
    for _ in range(max_levels):
        communities = _one_level(current, resolution, rng)
        if len(set(communities.values())) == len(current.nodes):
            break
        mapping = {node: communities[community] for node, community in mapping.items()}
        current = _aggregate(current, communities)
        if len(current.nodes) <= 1:
            break
    # Relabel to compact, deterministic ids.
    labels: dict[str, str] = {}
    out: dict[str, str] = {}
    for node in sorted(mapping):
        community = mapping[node]
        if community not in labels:
            labels[community] = f"C{len(labels):03d}"
        out[node] = labels[community]
    return out


def connected_components(graph: Graph) -> dict[str, str]:
    seen: dict[str, str] = {}
    index = 0
    for node in sorted(graph.nodes):
        if node in seen:
            continue
        label = f"K{index:03d}"
        index += 1
        stack = [node]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen[current] = label
            for neighbour in graph.adjacency.get(current, {}):
                if neighbour not in seen:
                    stack.append(neighbour)
    return seen
