from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .packet import build_work_packet
from .planner import OutputLayout, SliceStrategy, build_slice_plan
from .summarizer import summarize_component


NO_CHANGE_REASON = "all planned slices already have packet, summary, and edge artifacts"


def _artifact_path(root: Path, relative_location: str) -> Path:
    path = Path(relative_location)
    return path if path.is_absolute() else root / path


def _all_artifacts_exist(root: Path, locations: dict[str, str]) -> bool:
    return all(_artifact_path(root, location).exists() for location in locations.values())


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_edges(path: Path, summary) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(
            {
                "component": summary.component,
                "kind": edge.kind,
                "source": edge.source,
                "target": edge.target,
                "confidence": edge.confidence,
                "source_line": edge.source_line,
            },
            sort_keys=True,
        )
        for edge in summary.edges
    ]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def run_next_slice(
    root: Path | str,
    *,
    strategy: SliceStrategy = "breadth-first",
    token_limit: int = 45_000,
    output_root: Path | str = ".system-map",
    output_layout: OutputLayout = "2-level",
) -> dict[str, Any]:
    """Advance the deterministic map by writing the next missing slice artifacts.

    This is the smallest useful run-loop primitive: an external cron can call it
    repeatedly, while artifact existence prevents unchanged repositories from
    producing the same packet forever.
    """
    root_path = Path(root).resolve()
    plan = build_slice_plan(
        root_path,
        strategy=strategy,
        token_limit=token_limit,
        output_root=output_root,
        output_layout=output_layout,
    )

    for planned_slice in plan.slices:
        locations = planned_slice.output_locations
        if _all_artifacts_exist(root_path, locations):
            continue

        paths: list[Path | str] = list(planned_slice.paths)
        summary = summarize_component(root_path, paths, component=planned_slice.component)
        packet = build_work_packet(root_path, paths, component=planned_slice.component)
        _write_json(_artifact_path(root_path, locations["summary"]), summary.to_dict())
        _write_edges(_artifact_path(root_path, locations["edges"]), summary)
        _write_json(_artifact_path(root_path, locations["packet"]), packet)
        return {
            "outcome": "advanced",
            "slice": asdict(planned_slice),
            "artifacts": {key: str(_artifact_path(root_path, value)) for key, value in locations.items()},
        }

    return {
        "outcome": "no_change",
        "reason": NO_CHANGE_REASON,
        "planned_slices": len(plan.slices),
    }
