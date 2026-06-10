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
    claim_store_path: str | None = None,
) -> dict[str, Any]:
    """Advance the deterministic map by writing the next missing slice artifacts.

    When strategy is "uncertainty-aware", the next slice is chosen based on:
    - highest uncertainty (most unknowns)
    - most conflicted claims
    - stale claims
    - low-confidence areas
    - changed files
    """
    root_path = Path(root).resolve()

    # For uncertainty-aware, we need to inspect the claim store
    if strategy == "uncertainty-aware":
        return _run_uncertainty_aware_next(
            root_path, token_limit=token_limit, output_root=output_root,
            output_layout=output_layout, claim_store_path=claim_store_path,
        )

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


def _run_uncertainty_aware_next(
    root_path: Path,
    *,
    token_limit: int,
    output_root: str | Path,
    output_layout: OutputLayout,
    claim_store_path: str | None,
) -> dict[str, Any]:
    """Choose the next slice based on investigation value signals."""
    # Build a standard plan first
    plan = build_slice_plan(
        root_path,
        strategy="breadth-first",
        token_limit=token_limit,
        output_root=output_root,
        output_layout=output_layout,
    )

    if not plan.slices:
        return {
            "outcome": "no_change",
            "reason": "No slices planned.",
            "planned_slices": 0,
        }

    # Score each slice by investigation value
    best_slice = None
    best_score = -1
    best_reason = ""

    for planned_slice in plan.slices:
        locations = planned_slice.output_locations
        if _all_artifacts_exist(root_path, locations):
            continue

        score = 0
        reasons: list[str] = []

        # Check for existing artifacts with unknowns
        summary_path = _artifact_path(root_path, locations.get("summary", ""))
        if summary_path.exists():
            try:
                existing = json.loads(summary_path.read_text(encoding="utf-8"))
                unknowns = existing.get("unknowns", [])
                if unknowns:
                    score += len(unknowns) * 3
                    reasons.append(f"{len(unknowns)} unknowns")
                stale_claims = existing.get("stale_claims", [])
                if stale_claims:
                    score += len(stale_claims) * 4
                    reasons.append(f"{len(stale_claims)} stale claims")
                low_conf = sum(1 for c in existing.get("claims", []) if isinstance(c, dict) and c.get("confidence") == "low")
                if low_conf:
                    score += low_conf * 2
                    reasons.append(f"{low_conf} low-confidence claims")
            except (json.JSONDecodeError, OSError):
                pass

        # Check claim store for conflicts/staleness
        if claim_store_path:
            try:
                from .claims import ClaimStore
                store = ClaimStore(claim_store_path)
                component = planned_slice.component
                component_claims = store.list_claims(component=component)
                stale = [c for c in component_claims if c.status == "stale"]
                if stale:
                    score += len(stale) * 5
                    reasons.append(f"{len(stale)} stale claims in store")
                low_conf_claims = [c for c in component_claims if c.confidence == "low"]
                if low_conf_claims:
                    score += len(low_conf_claims) * 2
                    reasons.append(f"{len(low_conf_claims)} low-confidence claims in store")
                conflicts = store.get_conflicts()
                for conflict in conflicts:
                    topic = conflict.get("topic", "")
                    if component in topic:
                        score += 6
                        reasons.append(f"conflict: {topic}")
            except (FileNotFoundError, KeyError):
                pass

        # Prefer slices with more files (more central)
        score += len(planned_slice.paths)
        if len(planned_slice.paths) > 1:
            reasons.append(f"{len(planned_slice.paths)} files")

        if score > best_score:
            best_score = score
            best_slice = planned_slice
            best_reason = "; ".join(reasons) if reasons else "next uninspected slice"

    if best_slice is None:
        return {
            "outcome": "no_change",
            "reason": NO_CHANGE_REASON,
            "planned_slices": len(plan.slices),
        }

    # Write the best slice
    locations = best_slice.output_locations
    paths = [str(p) for p in best_slice.paths]
    summary = summarize_component(root_path, paths, component=best_slice.component)
    packet = build_work_packet(root_path, paths, component=best_slice.component)
    _write_json(_artifact_path(root_path, locations["summary"]), summary.to_dict())
    _write_edges(_artifact_path(root_path, locations["edges"]), summary)
    _write_json(_artifact_path(root_path, locations["packet"]), packet)
    return {
        "outcome": "advanced",
        "strategy": "uncertainty-aware",
        "slice": asdict(best_slice),
        "selection_reason": best_reason,
        "investigation_score": best_score,
        "artifacts": {key: str(_artifact_path(root_path, value)) for key, value in locations.items()},
    }
