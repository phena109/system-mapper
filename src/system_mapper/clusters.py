from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable


def load_edge_records(path: Path | str) -> list[dict[str, Any]]:
    """Load graph JSONL records emitted by `system-mapper graph`.

    Blank lines are ignored so shell-appended edge files remain readable.
    """
    records: list[dict[str, Any]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        payload = json.loads(stripped)
        if isinstance(payload, dict):
            records.append(payload)
    return records


def _record_source(record: dict[str, Any]) -> str:
    source = str(record.get("source", ""))
    line = record.get("source_line")
    if line is None:
        return source
    return f"{source}:{line}"


def _record_source_sort_key(citation: str) -> tuple[str, int, str]:
    source, separator, line = citation.rpartition(":")
    if separator:
        try:
            return source, int(line), ""
        except ValueError:
            return citation, -1, ""
    return citation, -1, ""


def _edge_endpoints(record: dict[str, Any]) -> tuple[str, str] | None:
    source = record.get("source")
    target = record.get("target")
    if source is None or target is None:
        return None
    return str(source), str(target)


def _connected_components(records: list[dict[str, Any]]) -> list[tuple[set[str], list[dict[str, Any]]]]:
    adjacency: dict[str, set[str]] = {}
    edges_by_node: dict[str, list[dict[str, Any]]] = {}

    for record in records:
        endpoints = _edge_endpoints(record)
        if endpoints is None:
            continue
        source, target = endpoints
        adjacency.setdefault(source, set()).add(target)
        adjacency.setdefault(target, set()).add(source)
        edges_by_node.setdefault(source, []).append(record)
        edges_by_node.setdefault(target, []).append(record)

    components: list[tuple[set[str], list[dict[str, Any]]]] = []
    visited: set[str] = set()
    for start in sorted(adjacency):
        if start in visited:
            continue
        stack = [start]
        nodes: set[str] = set()
        component_edges: list[dict[str, Any]] = []
        seen_edge_ids: set[int] = set()
        while stack:
            node = stack.pop()
            if node in visited:
                continue
            visited.add(node)
            nodes.add(node)
            for record in edges_by_node.get(node, []):
                record_id = id(record)
                if record_id not in seen_edge_ids:
                    component_edges.append(record)
                    seen_edge_ids.add(record_id)
                endpoints = _edge_endpoints(record)
                if endpoints is None:
                    continue
                for neighbour in endpoints:
                    if neighbour not in visited:
                        stack.append(neighbour)
        components.append((nodes, component_edges))
    return components


def _hub_nodes(nodes: Iterable[str], records: list[dict[str, Any]]) -> list[str]:
    degree = {node: 0 for node in nodes}
    for record in records:
        endpoints = _edge_endpoints(record)
        if endpoints is None:
            continue
        source, target = endpoints
        if source in degree:
            degree[source] += 1
        if target in degree:
            degree[target] += 1
    max_degree = max(degree.values(), default=0)
    if max_degree <= 1:
        return []
    return sorted(node for node, count in degree.items() if count == max_degree)


def cluster_edge_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Group graph edge records into connected component communities.

    The output is deliberately deterministic and evidence-preserving: every
    cluster keeps source:line citations from the underlying edge records so a
    low-context worker can jump back to the exact graph evidence.
    """
    components = _connected_components(records)
    components.sort(key=lambda item: (-len(item[1]), sorted(item[0])[0] if item[0] else ""))

    clusters: list[dict[str, Any]] = []
    for index, (nodes, cluster_records) in enumerate(components, start=1):
        clusters.append(
            {
                "id": f"cluster-{index:03d}",
                "nodes": sorted(nodes),
                "edge_count": len(cluster_records),
                "edge_kinds": sorted({str(record.get("kind", "unknown")) for record in cluster_records}),
                "components": sorted({str(record.get("component")) for record in cluster_records if record.get("component")}),
                "evidence_sources": sorted(
                    {_record_source(record) for record in cluster_records if record.get("source")},
                    key=_record_source_sort_key,
                ),
                "hub_nodes": _hub_nodes(nodes, cluster_records),
            }
        )

    return {
        "input_edges": len(records),
        "cluster_count": len(clusters),
        "clusters": clusters,
    }


def cluster_edge_file(path: Path | str) -> dict[str, Any]:
    return cluster_edge_records(load_edge_records(path))
