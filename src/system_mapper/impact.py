from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from .update import DIFF_FILE_RE, update_summary_from_diff


def _map_root(root: Path | str, output_root: str) -> Path:
    root_path = Path(root)
    if root_path.name == output_root or root_path.name == ".system-map":
        return root_path
    return root_path / output_root


def _project_root(root: Path | str, map_root: Path, output_root: str) -> Path:
    root_path = Path(root)
    if root_path.name == output_root or root_path.name == ".system-map":
        return map_root.parent
    return root_path


def _changed_files_from_diff(diff: str) -> list[str]:
    changed: list[str] = []
    for _old, new in DIFF_FILE_RE.findall(diff):
        if new not in changed:
            changed.append(new)
    return changed


def _git_diff(root: Path, diff_from: str) -> str:
    result = subprocess.run(
        ["git", "diff", "--no-ext-diff", diff_from, "--"],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git diff failed against {diff_from}")
    return result.stdout


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _load_components(map_root: Path) -> list[dict[str, Any]]:
    components_dir = map_root / "components"
    if not components_dir.exists():
        return []
    return [data for path in sorted(components_dir.rglob("*.json")) if (data := _load_json(path)) is not None]


def _load_edges(map_root: Path) -> list[dict[str, Any]]:
    edges_dir = map_root / "edges"
    if not edges_dir.exists():
        return []
    edges: list[dict[str, Any]] = []
    for path in sorted(edges_dir.rglob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                edges.append(record)
    return edges


def _component_files(summary: dict[str, Any]) -> set[str]:
    files = {str(path) for path in summary.get("scope", []) or []}
    files.update(str(edge.get("source", "")) for edge in summary.get("edges", []) or [] if isinstance(edge, dict))
    files.update(str(record.get("source", "")) for record in summary.get("evidence_ledger", []) or [] if isinstance(record, dict))
    return {path for path in files if path}


def _touches(edge: dict[str, Any], changed_files: set[str]) -> str | None:
    source = str(edge.get("source", ""))
    target = str(edge.get("target", ""))
    if source in changed_files:
        return "outgoing"
    if target in changed_files:
        return "incoming"
    return None


def analyze_repo_impact(
    root: Path | str,
    *,
    diff: str | None = None,
    diff_from: str = "HEAD",
    output_root: str = ".system-map",
) -> dict[str, Any]:
    map_root = _map_root(root, output_root)
    project_root = _project_root(root, map_root, output_root)
    diff_text = diff if diff is not None else _git_diff(project_root, diff_from)
    changed_files = _changed_files_from_diff(diff_text)
    changed_set = set(changed_files)

    affected_components: list[dict[str, Any]] = []
    stale_claims: list[dict[str, str]] = []
    refresh_commands: list[str] = []
    change_summaries: list[dict[str, Any]] = []

    for summary in _load_components(map_root):
        matched = sorted(_component_files(summary) & changed_set)
        if not matched:
            continue
        component = str(summary.get("component", "unknown"))
        affected_components.append({"component": component, "matched_files": matched})
        update = update_summary_from_diff(summary, diff_text).to_dict()
        change_summaries.append(update)
        for claim in update.get("stale_claims", []):
            stale_claims.append({"component": component, **claim})
        refresh_commands.append("uv run system-mapper slice . " + " ".join(matched) + f" --component {component} --json")

    related_edges: list[dict[str, Any]] = []
    seen_edges: set[str] = set()
    for edge in _load_edges(map_root):
        direction = _touches(edge, changed_set)
        if direction is None:
            continue
        payload = {"direction": direction, **edge}
        key = json.dumps(payload, sort_keys=True)
        if key not in seen_edges:
            seen_edges.add(key)
            related_edges.append(payload)

    return {
        "root": str(project_root),
        "map_root": str(map_root),
        "changed_files": changed_files,
        "affected_components": affected_components,
        "stale_claims": stale_claims,
        "related_edges": related_edges,
        "refresh_commands": refresh_commands,
        "change_summaries": change_summaries,
        "unknowns": ["Repo impact is heuristic; refresh affected slices before trusting changed behaviour."],
    }
