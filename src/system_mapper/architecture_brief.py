from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .clusters import load_edge_records


def _file_of(node: str) -> str:
    """Extract the file path from a node label.

    Nodes can be:
    - "src/index.ts"          (already a file)
    - "src/index.ts:42"       (file with line)
    - "src/index.ts:myFunc"   (file:local-symbol)
    - "POST /maps/{id}"       (route target — keep as-is)
    - "https://..."           (URL — keep as-is)
    - "invoices"              (data store — keep as-is)
    """
    if node.startswith(("http://", "https://")):
        return ""
    if " " in node and node.split(" ")[0] in {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD", "ROUTE"}:
        return ""
    # Strip trailing :line or :symbol
    for sep in (":",):
        if sep in node:
            candidate = node.rsplit(sep, 1)[0]
            if candidate != node and ("/" in candidate or "." in candidate):
                return candidate
    # If it looks like a file path (has extension or slash), keep it
    if "/" in node or "." in node:
        return node
    return ""


def build_architecture_brief(
    edge_jsonl: Path | str,
    *,
    top_file_edges: int = 20,
    min_edge_weight: int = 1,
) -> dict[str, Any]:
    """Produce a human-readable architecture brief from clustered edges.

    This is the missing layer the ChatGPT review identified:

        code → edges → clusters → **architecture brief**

    The brief answers:
    - What are the main file-to-file relationships?
    - What appears to be the entry point?
    - What are the architectural layers?
    - What external systems does it touch?
    - What data stores does it use?
    """
    records = load_edge_records(edge_jsonl)

    # --- Build file-to-file edge weights ---
    file_edges: dict[tuple[str, str], int] = defaultdict(int)
    file_edge_kinds: dict[tuple[str, str], set[str]] = defaultdict(set)
    all_files: set[str] = set()
    external_targets: dict[str, int] = defaultdict(int)
    data_stores: dict[str, int] = defaultdict(int)
    routes: dict[str, int] = defaultdict(int)
    triggers: dict[str, int] = defaultdict(int)

    for record in records:
        source = str(record.get("source", ""))
        target = str(record.get("target", ""))
        kind = str(record.get("kind", ""))

        src_file = _file_of(source)
        tgt_file = _file_of(target)

        # Track all known files
        if src_file:
            all_files.add(src_file)
        if tgt_file:
            all_files.add(tgt_file)

        # File-to-file edges
        if src_file and tgt_file and src_file != tgt_file:
            key = (src_file, tgt_file)
            file_edges[key] += 1
            file_edge_kinds[key].add(kind)

        # External dependencies
        if kind == "external" and target.startswith(("http://", "https://")):
            # Normalize URL to domain
            try:
                from urllib.parse import urlparse
                parsed = urlparse(target)
                domain = parsed.netloc or target
                external_targets[domain] += 1
            except Exception:
                external_targets[target] += 1

        # Data stores
        if kind == "data_store":
            data_stores[target] += 1

        # Routes
        if kind == "route":
            routes[target] += 1

        # Triggers
        if kind == "trigger":
            triggers[target] += 1

    # --- Rank file edges by weight ---
    ranked_edges = sorted(file_edges.items(), key=lambda item: -item[1])
    top_edges = [
        {
            "from": src,
            "to": tgt,
            "weight": weight,
            "kinds": sorted(file_edge_kinds[(src, tgt)]),
        }
        for (src, tgt), weight in ranked_edges
        if weight >= min_edge_weight
    ][:top_file_edges]

    # --- Identify likely entry point ---
    # The file with the most outgoing edges to other files is likely the entry
    out_degree: dict[str, int] = defaultdict(int)
    in_degree: dict[str, int] = defaultdict(int)
    for (src, tgt), weight in file_edges.items():
        out_degree[src] += weight
        in_degree[tgt] += weight

    # Entry point: high out-degree, low in-degree
    entry_candidates = sorted(
        all_files,
        key=lambda f: -(out_degree.get(f, 0) - in_degree.get(f, 0)),
    )
    likely_entry = entry_candidates[0] if entry_candidates else None

    # --- Identify layers ---
    # Group files by their relationship to the entry point
    layers: dict[str, list[str]] = {
        "entry": [],
        "core": [],
        "leaf": [],
        "unreachable": [],
    }
    if likely_entry:
        layers["entry"].append(likely_entry)
        # Core: files directly connected to entry
        core_files = set()
        for (src, tgt), _ in file_edges.items():
            if src == likely_entry:
                core_files.add(tgt)
            if tgt == likely_entry:
                core_files.add(src)
        layers["core"] = sorted(core_files - {likely_entry})
        # Leaf: files connected only to core, not entry
        leaf_files = set()
        for f in all_files:
            if f in layers["entry"] or f in core_files:
                continue
            connected_to_core = any(
                (f, c) in file_edges or (c, f) in file_edges
                for c in core_files
            )
            if connected_to_core:
                leaf_files.add(f)
        layers["leaf"] = sorted(leaf_files)
        # Unreachable: everything else
        reachable = {likely_entry} | core_files | leaf_files
        layers["unreachable"] = sorted(all_files - reachable)

    # --- Build the text brief ---
    lines: list[str] = []

    if likely_entry:
        lines.append(f"Likely entry point: {likely_entry}")
        lines.append("")

    if top_edges:
        lines.append("Top file-to-file relationships:")
        for edge in top_edges:
            kinds = ", ".join(edge["kinds"])
            lines.append(f"  {edge['from']} → {edge['to']}  (weight={edge['weight']}, kinds={kinds})")
        lines.append("")

    if layers["core"]:
        lines.append("Core files (directly connected to entry):")
        for f in layers["core"]:
            lines.append(f"  {f}")
        lines.append("")

    if layers["leaf"]:
        lines.append("Leaf/supporting files:")
        for f in layers["leaf"]:
            lines.append(f"  {f}")
        lines.append("")

    if external_targets:
        lines.append("External dependencies:")
        for domain, count in sorted(external_targets.items(), key=lambda x: -x[1]):
            lines.append(f"  {domain}  ({count} reference{'s' if count != 1 else ''})")
        lines.append("")

    if data_stores:
        lines.append("Data stores:")
        for store, count in sorted(data_stores.items(), key=lambda x: -x[1]):
            lines.append(f"  {store}  ({count} reference{'s' if count != 1 else ''})")
        lines.append("")

    if routes:
        lines.append("Routes:")
        for route, count in sorted(routes.items(), key=lambda x: -x[1])[:15]:
            lines.append(f"  {route}")
        lines.append("")

    if triggers:
        lines.append("Triggers:")
        for trigger, count in sorted(triggers.items(), key=lambda x: -x[1]):
            lines.append(f"  {trigger}")
        lines.append("")

    if layers["unreachable"]:
        lines.append(f"Unreachable/isolated files ({len(layers['unreachable'])}):")
        for f in layers["unreachable"][:10]:
            lines.append(f"  {f}")
        if len(layers["unreachable"]) > 10:
            lines.append(f"  ... and {len(layers['unreachable']) - 10} more")
        lines.append("")

    return {
        "text_brief": "\n".join(lines).strip(),
        "likely_entry": likely_entry,
        "top_file_edges": top_edges,
        "layers": {k: v for k, v in layers.items() if v},
        "external_dependencies": dict(sorted(external_targets.items(), key=lambda x: -x[1])),
        "data_stores": dict(sorted(data_stores.items(), key=lambda x: -x[1])),
        "routes": dict(sorted(routes.items(), key=lambda x: -x[1])),
        "triggers": dict(sorted(triggers.items(), key=lambda x: -x[1])),
        "stats": {
            "total_files": len(all_files),
            "file_to_file_edges": len(file_edges),
            "external_targets": len(external_targets),
            "data_stores": len(data_stores),
        },
    }
