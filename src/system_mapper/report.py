from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from .adr import list_decisions
from .map_query import _load_component_summaries, _load_edges, _system_map_root


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _component_name(summary: dict[str, Any]) -> str:
    return str(summary.get("component") or "(unknown component)")


def _edge_degree_by_component(edges: list[dict[str, Any]], summaries: list[dict[str, Any]]) -> Counter[str]:
    touched: dict[str, set[int]] = {_component_name(summary): set() for summary in summaries}
    scope_by_component = {
        _component_name(summary): {str(path) for path in _safe_list(summary.get("scope"))}
        for summary in summaries
    }
    for index, edge in enumerate(edges):
        component = str(edge.get("component") or "")
        if component in touched:
            touched[component].add(index)
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        for name, scope in scope_by_component.items():
            if any(source.startswith(path) or target.startswith(path) for path in scope):
                touched[name].add(index)
    degree: Counter[str] = Counter()
    for component, edge_indexes in touched.items():
        degree[component] = len(edge_indexes)
    return degree


def _build_reading_path(summaries: list[dict[str, Any]], edges: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    degree = _edge_degree_by_component(edges, summaries)
    ranked = sorted(
        summaries,
        key=lambda summary: (-degree[_component_name(summary)], _component_name(summary)),
    )
    return [
        {
            "component": _component_name(summary),
            "purpose": str(summary.get("purpose") or ""),
            "scope": _safe_list(summary.get("scope")),
            "edge_count": degree[_component_name(summary)],
            "unknown_count": len(_safe_list(summary.get("unknowns"))),
        }
        for summary in ranked[:limit]
    ]


def _collect_claim_stats(summaries: list[dict[str, Any]]) -> dict[str, int]:
    stats = {"claims": 0, "evidence_records": 0, "unknowns": 0}
    for summary in summaries:
        stats["claims"] += len(_safe_list(summary.get("claims")))
        stats["evidence_records"] += len(_safe_list(summary.get("evidence_ledger")))
        stats["unknowns"] += len(_safe_list(summary.get("unknowns")))
    return stats


def _format_confidence(summary: dict[str, Any]) -> str:
    confidence = summary.get("confidence")
    if isinstance(confidence, dict) and confidence:
        return ", ".join(f"{key}={value}" for key, value in sorted(confidence.items()))
    if confidence:
        return str(confidence)
    return "not recorded"


def _format_markdown(
    *,
    map_root: Path,
    summaries: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    reading_path: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    claim_stats: dict[str, int],
) -> str:
    lines: list[str] = [
        "# System map report",
        "",
        f"Map root: `{map_root}`",
        "",
        "## Overview",
        "",
        f"- Components mapped: {len(summaries)}",
        f"- Graph edges: {len(edges)}",
        f"- Claims: {claim_stats['claims']}",
        f"- Evidence records: {claim_stats['evidence_records']}",
        f"- Open unknowns: {claim_stats['unknowns']}",
        f"- Architecture decisions: {len(decisions)}",
        "",
        "## Guided reading path",
        "",
    ]
    if reading_path:
        for idx, item in enumerate(reading_path, start=1):
            scope = ", ".join(map(str, item["scope"])) or "scope not recorded"
            lines.append(
                f"{idx}. **{item['component']}** — {item['purpose'] or 'purpose not recorded'} "
                f"(edges: {item['edge_count']}, unknowns: {item['unknown_count']}; scope: {scope})"
            )
    else:
        lines.append("No mapped components found yet. Run `system-mapper next <root> --output-layout flat` first.")

    lines.extend(["", "## Component details", ""])
    for summary in sorted(summaries, key=_component_name):
        name = _component_name(summary)
        lines.append(f"### {name}")
        lines.append("")
        lines.append(f"- Purpose: {summary.get('purpose') or 'not recorded'}")
        scope = ", ".join(map(str, _safe_list(summary.get("scope")))) or "not recorded"
        lines.append(f"- Scope: {scope}")
        lines.append(f"- Confidence: {_format_confidence(summary)}")
        claims = _safe_list(summary.get("claims"))
        if claims:
            lines.append("- Claims:")
            for claim in claims[:3]:
                if isinstance(claim, dict):
                    refs = ", ".join(map(str, _safe_list(claim.get("evidence_refs"))))
                    suffix = f" [evidence: {refs}]" if refs else ""
                    lines.append(f"  - {claim.get('id', '(claim)')}: {claim.get('text', '')}{suffix}")
        unknowns = _safe_list(summary.get("unknowns"))
        if unknowns:
            lines.append("- Unknowns / follow-up:")
            for unknown in unknowns[:5]:
                lines.append(f"  - {unknown}")
        evidence = _safe_list(summary.get("evidence_ledger"))
        if evidence:
            lines.append("- Evidence examples:")
            for record in evidence[:3]:
                if isinstance(record, dict):
                    lines.append(
                        f"  - {record.get('id', '(evidence)')} `{record.get('source', '')}:{record.get('line_start', '')}` "
                        f"{record.get('excerpt', '')}"
                    )
        lines.append("")

    lines.extend(["## Architecture decisions", ""])
    if decisions:
        for decision in decisions[:10]:
            lines.append(
                f"- **{decision.get('id', '(adr)')}** [{decision.get('status', '')}] "
                f"{decision.get('title', '')}: {decision.get('decision', '')}"
            )
    else:
        lines.append("No architecture decisions recorded in `.system-map/architecture-decisions.json`.")

    lines.extend(["", "## Next useful commands", ""])
    lines.append("- Query this map: `system-mapper map-query <root> \"what should I inspect?\" --snippets`")
    lines.append("- Continue mapping: `system-mapper next <root> --output-layout flat`")
    lines.append("- Check quality: `system-mapper quality <summary-or-worker-json>`")
    return "\n".join(lines).rstrip() + "\n"


def build_map_report(
    root: Path | str,
    *,
    output_root: str = ".system-map",
    reading_limit: int = 8,
) -> dict[str, Any]:
    """Build a human-facing report over existing map artifacts.

    This is the lightweight Understand-Anything-inspired front surface: it turns
    already-generated deterministic artifacts into an immediately readable tour
    without hiding evidence, confidence, or unknowns.
    """
    map_root = _system_map_root(root, output_root)
    summaries = _load_component_summaries(map_root)
    edges = _load_edges(map_root)
    decisions = list_decisions(map_root / "architecture-decisions.json")
    reading_path = _build_reading_path(summaries, edges, reading_limit)
    claim_stats = _collect_claim_stats(summaries)
    markdown = _format_markdown(
        map_root=map_root,
        summaries=summaries,
        edges=edges,
        reading_path=reading_path,
        decisions=decisions,
        claim_stats=claim_stats,
    )
    return {
        "map_root": str(map_root),
        "component_count": len(summaries),
        "edge_count": len(edges),
        "claim_stats": claim_stats,
        "reading_path": reading_path,
        "architecture_decisions": decisions,
        "markdown": markdown,
        "warnings": [] if map_root.exists() else [f"Map root not found: {map_root}"],
    }
