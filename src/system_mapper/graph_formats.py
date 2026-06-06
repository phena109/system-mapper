from __future__ import annotations

import re

from .models import ComponentSummary, Edge


_NODE_ID_RE = re.compile(r"[^0-9A-Za-z_]")


def _node_id(label: str) -> str:
    """Return a stable Mermaid-safe identifier for a source or target label."""
    identifier = _NODE_ID_RE.sub("_", label).strip("_")
    if not identifier:
        identifier = "node"
    if identifier[0].isdigit():
        identifier = "n_" + identifier
    while "__" in identifier:
        identifier = identifier.replace("__", "_")
    return identifier


def _quote_label(label: str) -> str:
    return label.replace("\\", "\\\\").replace('"', r'\"')


def _node_line(label: str) -> str:
    node_id = _node_id(label)
    return f'  {node_id}["{_quote_label(label)}"]'


def _edge_line(edge: Edge) -> str:
    source = _node_id(edge.source)
    target = _node_id(edge.target)
    return f"  {source} -->|{edge.kind} / {edge.confidence}| {target}"


def _dot_quote(value: str) -> str:
    return '"' + _quote_label(value) + '"'


def _dot_node_line(label: str) -> str:
    quoted = _dot_quote(label)
    return f"  {quoted} [label={quoted}];"


def _dot_edge_line(edge: Edge) -> str:
    label = _dot_quote(f"{edge.kind} / {edge.confidence}")
    return f"  {_dot_quote(edge.source)} -> {_dot_quote(edge.target)} [label={label}];"


def render_mermaid(summary: ComponentSummary) -> str:
    """Render a component's deterministic edge records as a Mermaid flowchart."""
    lines = ["flowchart TD"]
    seen_nodes: set[str] = set()
    for edge in summary.edges:
        for label in (edge.source, edge.target):
            node_id = _node_id(label)
            if node_id not in seen_nodes:
                lines.append(_node_line(label))
                seen_nodes.add(node_id)
        lines.append(_edge_line(edge))
    if not summary.edges:
        label = summary.component or "component"
        lines.append(_node_line(label))
    return "\n".join(lines) + "\n"


def render_dot(summary: ComponentSummary) -> str:
    """Render a component's deterministic edge records as Graphviz DOT."""
    lines = ["digraph system_map {"]
    seen_nodes: set[str] = set()
    for edge in summary.edges:
        for label in (edge.source, edge.target):
            if label not in seen_nodes:
                lines.append(_dot_node_line(label))
                seen_nodes.add(label)
        lines.append(_dot_edge_line(edge))
    if not summary.edges:
        lines.append(_dot_node_line(summary.component or "component"))
    lines.append("}")
    return "\n".join(lines) + "\n"
