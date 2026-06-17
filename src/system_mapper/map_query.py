from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

WORD_RE = re.compile(r"[A-Za-z0-9_]+")


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in WORD_RE.findall(text) if len(token) > 1}


def _system_map_root(root: Path | str, output_root: str = ".system-map") -> Path:
    root_path = Path(root)
    if root_path.name == output_root or root_path.name == ".system-map":
        return root_path
    return root_path / output_root


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _load_component_summaries(map_root: Path) -> list[dict[str, Any]]:
    components_dir = map_root / "components"
    if not components_dir.exists():
        return []
    summaries: list[dict[str, Any]] = []
    for path in sorted(components_dir.rglob("*.json")):
        data = _load_json(path)
        if data is not None:
            data.setdefault("_source_path", str(path))
            summaries.append(data)
    return summaries


def _load_edges(map_root: Path) -> list[dict[str, Any]]:
    edges_dir = map_root / "edges"
    if not edges_dir.exists():
        return []
    records: list[dict[str, Any]] = []
    for path in sorted(edges_dir.rglob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                record.setdefault("_source_path", str(path))
                records.append(record)
    return records


def _flatten_search_text(summary: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("component", "purpose", "scope", "entry_points", "inputs", "outputs", "business_rules", "human_steps", "risks", "unknowns"):
        value = summary.get(key)
        if isinstance(value, list):
            parts.extend(str(item) for item in value)
        elif value is not None:
            parts.append(str(value))
    for claim in summary.get("claims", []) or []:
        if isinstance(claim, dict):
            parts.extend(str(claim.get(key, "")) for key in ("id", "type", "text", "confidence"))
    for evidence in summary.get("evidence_ledger", []) or []:
        if isinstance(evidence, dict):
            parts.extend(str(evidence.get(key, "")) for key in ("id", "source", "kind", "excerpt"))
    return "\n".join(part for part in parts if part)


def _score_summary(summary: dict[str, Any], query_tokens: set[str]) -> int:
    if not query_tokens:
        return 0
    text = _flatten_search_text(summary)
    text_tokens = _tokens(text)
    overlap = query_tokens & text_tokens
    score = len(overlap)
    component_tokens = _tokens(str(summary.get("component", "")))
    purpose_tokens = _tokens(str(summary.get("purpose", "")))
    score += len(query_tokens & component_tokens) * 3
    score += len(query_tokens & purpose_tokens) * 2
    return score


def _edge_touches_summary(edge: dict[str, Any], summary: dict[str, Any]) -> bool:
    component = str(summary.get("component", ""))
    if component and str(edge.get("component", "")) == component:
        return True
    scope = {str(path) for path in summary.get("scope", []) or []}
    source = str(edge.get("source", ""))
    target = str(edge.get("target", ""))
    return any(source.startswith(path) or target.startswith(path) for path in scope)


def _format_answer_context(matches: list[dict[str, Any]], related_edges: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for match in matches:
        summary = match["summary"]
        lines.append(f"## {summary.get('component', '(unknown component)')}")
        if summary.get("purpose"):
            lines.append(f"Purpose: {summary['purpose']}")
        if summary.get("scope"):
            lines.append("Scope: " + ", ".join(map(str, summary.get("scope", []))))
        claims = summary.get("claims", []) or []
        if claims:
            lines.append("Claims:")
            for claim in claims[:5]:
                if not isinstance(claim, dict):
                    continue
                refs = ", ".join(map(str, claim.get("evidence_refs", []) or []))
                suffix = f" [evidence: {refs}]" if refs else ""
                lines.append(f"- {claim.get('id', '(claim)')}: {claim.get('text', '')}{suffix}")
        evidence = summary.get("evidence_ledger", []) or []
        if evidence:
            lines.append("Evidence:")
            for record in evidence[:5]:
                if not isinstance(record, dict):
                    continue
                loc = f"{record.get('source', '')}:{record.get('line_start', '')}"
                lines.append(f"- {record.get('id', '(evidence)')} ({record.get('kind', '')}, {loc}): {record.get('excerpt', '')}")
        lines.append("")
    if related_edges:
        lines.append("## Related graph edges")
        for edge in related_edges[:20]:
            lines.append(
                f"- {edge.get('source', '')} --[{edge.get('kind', '')}]--> {edge.get('target', '')}"
                f" (component={edge.get('component', '')}, confidence={edge.get('confidence', '')})"
            )
    return "\n".join(lines).strip()


def query_system_map(root: Path | str, query: str, *, limit: int = 5, output_root: str = ".system-map") -> dict[str, Any]:
    """Search generated map artifacts and return compact graph-expanded context.

    This borrows the purpose of Understand Anything's chat-context builder and
    codebase-memory-mcp's structural query surface without adding an embedding
    dependency: find relevant mapped components, then expand by one hop through
    already-generated edge JSONL records.
    """
    map_root = _system_map_root(root, output_root)
    query_tokens = _tokens(query)
    summaries = _load_component_summaries(map_root)
    edges = _load_edges(map_root)

    scored = [
        {"summary": summary, "score": _score_summary(summary, query_tokens)}
        for summary in summaries
    ]
    matches = [item for item in scored if item["score"] > 0]
    matches.sort(key=lambda item: (-int(item["score"]), str(item["summary"].get("component", ""))))
    matches = matches[:limit]

    related_edges: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str, str, str]] = set()
    for match in matches:
        for edge in edges:
            if not _edge_touches_summary(edge, match["summary"]):
                continue
            key = (
                str(edge.get("component", "")),
                str(edge.get("kind", "")),
                str(edge.get("source", "")),
                str(edge.get("target", "")),
            )
            if key in seen_edges:
                continue
            seen_edges.add(key)
            related_edges.append(edge)

    public_matches = [
        {
            "component": match["summary"].get("component", ""),
            "score": match["score"],
            "purpose": match["summary"].get("purpose", ""),
            "scope": match["summary"].get("scope", []),
            "source_path": match["summary"].get("_source_path", ""),
        }
        for match in matches
    ]
    return {
        "query": query,
        "map_root": str(map_root),
        "matches": public_matches,
        "related_edges": related_edges,
        "answer_context": _format_answer_context(matches, related_edges),
        "warnings": [] if map_root.exists() else [f"Map root not found: {map_root}"],
    }
