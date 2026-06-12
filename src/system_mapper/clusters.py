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
        sorted_nodes = sorted(nodes)
        clusters.append(
            {
                "id": f"cluster-{index:03d}",
                "nodes": sorted_nodes,
                "node_count": len(sorted_nodes),
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
        "node_count": sum(cluster.get("node_count", 0) for cluster in clusters),
        "edge_count": sum(cluster.get("edge_count", 0) for cluster in clusters),
        "clusters": clusters,
    }


def cluster_edge_file(path: Path | str) -> dict[str, Any]:
    return cluster_edge_records(load_edge_records(path))


# ---------------------------------------------------------------------------
# Subsystem-level summaries from clusters
# ---------------------------------------------------------------------------

def _classify_node_role(node: str, edge_kinds: list[str] | None = None) -> str:
    """Heuristic classification of a node's architectural role."""
    if node.startswith(("http://", "https://")):
        return "external_system"
    if " " in node and node.split(" ")[0] in {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD", "ROUTE"}:
        return "route"
    if node.startswith("cron "):
        return "trigger"
    if ":" in node and ("/" in node or "." in node):
        return "file_symbol"
    if "/" in node or "." in node:
        return "file"
    # Simple names (no /, ., :) could be data stores or symbols.
    # If we have edge context and see data_store edges, prefer data_store.
    if edge_kinds and "data_store" in edge_kinds:
        return "data_store"
    # Default: simple names are likely data stores in graph context
    return "data_store"


GENERIC_SUBSYSTEM_ROOTS = {"src", "lib", "app", "apps", "pkg", "packages", "cmd", "internal", "source", "sources"}


def _meaningful_name_parts(pathish: str) -> list[str]:
    original_parts = [part for part in pathish.split("/") if part]
    parts = list(original_parts)
    while parts and parts[0].lower() in GENERIC_SUBSYSTEM_ROOTS:
        parts = parts[1:]
    if len(parts) == 1 and "." in parts[0] and original_parts:
        return original_parts[:1]
    return parts


def _guess_subsystem_name(nodes: list[str], components: list[str]) -> str:
    """Guess a probable subsystem name from cluster components and nodes."""
    # Prefer component names — they carry domain meaning. Skip generic source
    # roots so dogfood clusters do not all become indistinguishable "src".
    if components:
        from collections import Counter

        parts_list = [_meaningful_name_parts(c) for c in components if c]
        first_parts = [parts[0] for parts in parts_list if parts]
        if first_parts:
            most_common = Counter(first_parts).most_common(1)[0][0]
            return most_common
    # Fallback: use the most common non-generic file directory
    dirs: list[str] = []
    for node in nodes:
        if "/" in node:
            parts = _meaningful_name_parts(node)
            if parts:
                dirs.append(parts[0])
    if dirs:
        from collections import Counter
        return Counter(dirs).most_common(1)[0][0]
    return "unknown"


def build_subsystem_summaries(cluster_report: dict[str, Any]) -> list[dict[str, Any]]:
    """Enrich cluster output with subsystem-level summaries.

    Each subsystem summary includes:
    - probable_subsystem: guessed name for this cluster
    - why_grouped: human-readable reason these nodes belong together
    - main_entrypoints: detected entry points (file:symbol nodes)
    - data_stores: data store targets
    - external_systems: external URL targets
    - routes: HTTP route targets
    - triggers: cron/trigger targets
    - unknowns: structural unknowns (isolated nodes, no clear entry)
    - claims_to_review: items that need human verification
    """
    summaries: list[dict[str, Any]] = []

    for cluster in cluster_report.get("clusters", []):
        nodes = cluster.get("nodes", [])
        components = cluster.get("components", [])
        edge_kinds = cluster.get("edge_kinds", [])

        # Classify nodes by role
        entrypoints: list[str] = []
        data_stores: list[str] = []
        external_systems: list[str] = []
        routes: list[str] = []
        triggers: list[str] = []
        file_symbols: list[str] = []

        for node in nodes:
            role = _classify_node_role(node, edge_kinds)
            if role == "file_symbol":
                file_symbols.append(node)
            elif role == "external_system":
                external_systems.append(node)
            elif role == "route":
                routes.append(node)
            elif role == "trigger":
                triggers.append(node)
            elif role == "data_store":
                data_stores.append(node)

        # Main entrypoints: file:symbol nodes that are likely entry points
        # Prefer nodes with "main", "init", "run", "start", "handler", "controller"
        ep_keywords = {"main", "init", "run", "start", "handler", "controller", "app", "server", "index"}
        main_eps = [n for n in file_symbols if any(kw in n.lower() for kw in ep_keywords)]
        if not main_eps and file_symbols:
            main_eps = file_symbols[:3]  # Top 3 as fallback

        # Build why_grouped reason
        reasons: list[str] = []
        if components:
            reasons.append(f"components: {', '.join(components)}")
        if edge_kinds:
            reasons.append(f"edge kinds: {', '.join(edge_kinds)}")
        if cluster.get("hub_nodes"):
            reasons.append(f"hub nodes: {', '.join(cluster['hub_nodes'])}")
        why_grouped = "; ".join(reasons) if reasons else "connected by graph edges"

        # Structural unknowns
        unknowns: list[str] = []
        if not main_eps:
            unknowns.append("No clear entry point detected in this subsystem.")
        if not components:
            unknowns.append("No component labels available; subsystem boundary is inferred from edges only.")
        if len(nodes) <= 2:
            unknowns.append("Very small cluster; may be a partial view or leaf component.")

        # Claims to review
        claims_to_review: list[str] = []
        if external_systems:
            claims_to_review.append(f"External dependencies: {', '.join(external_systems[:3])}. Verify these are intentional.")
        if data_stores:
            claims_to_review.append(f"Data stores: {', '.join(data_stores[:3])}. Confirm ownership and access patterns.")
        if routes:
            claims_to_review.append(f"Routes: {', '.join(routes[:5])}. Verify API contract and auth requirements.")
        if triggers:
            claims_to_review.append(f"Triggers: {', '.join(triggers[:3])}. Check schedule and failure handling.")

        summaries.append({
            "cluster_id": cluster.get("id", "unknown"),
            "probable_subsystem": _guess_subsystem_name(nodes, components),
            "why_grouped": why_grouped,
            "node_count": len(nodes),
            "edge_count": cluster.get("edge_count", 0),
            "main_entrypoints": main_eps,
            "data_stores": data_stores,
            "external_systems": external_systems,
            "routes": routes,
            "triggers": triggers,
            "components": components,
            "unknowns": unknowns,
            "claims_to_review": claims_to_review,
        })

    return summaries
