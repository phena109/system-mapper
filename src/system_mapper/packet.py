from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from .prompts import slice_prompt
from .summarizer import summarize_component


CONTRACT = "system-mapper.work-packet.v1"


def _next_actions(summary_dict: dict[str, Any]) -> list[str]:
    actions: list[str] = []
    for unknown in summary_dict.get("unknowns", [])[:5]:
        actions.append(f"Inspect or answer unknown: {unknown}")
    for suggestion in summary_dict.get("suggested_next", [])[:5]:
        actions.append(suggestion)
    if not actions:
        actions.append("Review evidence, edges, and confidence before merging this slice into a higher-level map")
    return actions[:8]


def build_work_packet(root: Path | str, paths: list[Path | str], component: str | None = None) -> dict[str, Any]:
    """Package a bounded slice for a low-context AI worker.

    The packet keeps deterministic evidence, machine-readable edges, the
    slice-analysis prompt contract, and explicit unknown-driven next actions
    together so a weak worker does not need to rediscover neighbouring context.
    """
    summary = summarize_component(root, paths, component)
    summary_dict = summary.to_dict()
    edge_records = [asdict(edge) for edge in summary.edges]
    return {
        "contract": CONTRACT,
        "component": summary.component,
        "scope": summary.scope,
        "prompt": slice_prompt(summary.component),
        "summary": summary_dict,
        "edge_records": edge_records,
        "unknowns": list(summary.unknowns),
        "next_actions": _next_actions(summary_dict),
    }
