from __future__ import annotations

import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from .inventory import build_inventory
from .summarizer import summarize_component

DEFAULT_TOKEN_LIMIT = 45_000
CHARS_PER_TOKEN_ESTIMATE = 4

SliceStrategy = Literal["breadth-first", "depth-first", "chronological", "dependency-aware", "uncertainty-aware"]
OutputLayout = Literal["flat", "1-level", "2-level"]

LANGUAGE_PRIORITY = {
    ".php": 0,
    ".c": 1,
    ".h": 2,
    ".cpp": 3,
    ".hpp": 4,
    ".cc": 5,
    ".cxx": 6,
    ".java": 7,
    ".cs": 8,
    ".go": 9,
}


@dataclass
class PlannedSlice:
    component: str
    paths: list[str]
    estimated_tokens: int
    output_locations: dict[str, str]
    rationale: str


@dataclass
class SlicePlan:
    root: str
    strategy: str
    token_limit: int
    output_root: str
    output_layout: str
    slices: list[PlannedSlice]

    def to_dict(self) -> dict:
        return asdict(self)


def estimate_tokens(size_bytes: int) -> int:
    """Conservative-enough token estimate for source/document text files."""
    return max(1, (size_bytes + CHARS_PER_TOKEN_ESTIMATE - 1) // CHARS_PER_TOKEN_ESTIMATE)


def _safe_slug(value: str) -> str:
    slug = "".join(ch if ch.isalnum() else "-" for ch in value.lower()).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "root"


def _commit_timestamps(root: Path, paths: list[str]) -> dict[str, int]:
    if not (root / ".git").exists():
        return {}
    timestamps: dict[str, int] = {}
    for path in paths:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%ct", "--", path],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        try:
            timestamps[path] = int(result.stdout.strip() or "0")
        except ValueError:
            timestamps[path] = 0
    return timestamps


def _language_priority(path: str) -> int:
    return LANGUAGE_PRIORITY.get(Path(path).suffix.lower(), 100)


def _ordered_items(root: Path, strategy: SliceStrategy):
    inventory = build_inventory(root)
    candidates = [item for item in inventory.items if item.kind in {"code", "document", "config"}]
    if strategy == "dependency-aware":
        edge_counts: dict[str, int] = {}
        for item in candidates:
            summary = summarize_component(root, [item.path], component=Path(item.path).with_suffix("").as_posix())
            edge_counts[item.path] = len(summary.edges)
        return sorted(candidates, key=lambda item: (-edge_counts.get(item.path, 0), len(Path(item.path).parts), _language_priority(item.path), item.path))
    if strategy == "depth-first":
        return sorted(candidates, key=lambda item: (_language_priority(item.path), item.path))
    if strategy == "chronological":
        timestamps = _commit_timestamps(root, [item.path for item in candidates])
        return sorted(candidates, key=lambda item: (-timestamps.get(item.path, 0), _language_priority(item.path), item.path))
    # Breadth first is the default because it gets a whole-system shape before digging deep.
    return sorted(candidates, key=lambda item: (len(Path(item.path).parts), _language_priority(item.path), item.path))


def _component_for(paths: list[str]) -> str:
    if len(paths) == 1:
        path = Path(paths[0])
        return str(path.with_suffix(""))
    first = Path(paths[0])
    if len(first.parts) >= 2:
        return "/".join(first.parts[:2])
    return first.stem


def _locations(output_root: str, layout: OutputLayout, component: str) -> dict[str, str]:
    parts = [part for part in component.split("/") if part]
    if layout == "flat" or not parts:
        base_dir = Path(output_root)
        name = _safe_slug(component)
    elif layout == "1-level":
        base_dir = Path(output_root) / _safe_slug(parts[0])
        name = _safe_slug("-".join(parts[1:]) or parts[0])
    else:
        if len(parts) >= 2:
            base_dir = Path(output_root) / _safe_slug(parts[0]) / _safe_slug(parts[1])
            name = _safe_slug("-".join(parts[2:]) or parts[1])
        else:
            base_dir = Path(output_root) / _safe_slug(parts[0])
            name = _safe_slug(parts[0])
    return {
        "packet": str(base_dir / "packets" / f"{name}.json"),
        "summary": str(base_dir / "components" / f"{name}.json"),
        "edges": str(base_dir / "edges" / f"{name}.jsonl"),
    }


def _slice_rationale(root: Path, paths: list[str], strategy: SliceStrategy, estimated_tokens: int) -> str:
    """Explain why a planned slice is useful for a low-context worker."""
    parts = [f"strategy={strategy}", f"estimated_tokens={estimated_tokens}"]
    if strategy == "dependency-aware":
        summary_paths: list[Path | str] = list(paths)
        summary = summarize_component(root, summary_paths, component=_component_for(paths))
        edge_kinds = sorted({edge.kind for edge in summary.edges})
        parts.append(f"edge_count={len(summary.edges)}")
        if edge_kinds:
            parts.append("edge_kinds=" + ",".join(edge_kinds))
        if summary.unknowns:
            parts.append(f"unknown_count={len(summary.unknowns)}")
    elif strategy == "breadth-first":
        parts.append("reason=shallow system shape before deeper inspection")
    elif strategy == "depth-first":
        parts.append("reason=stable path order for focused folder descent")
    elif strategy == "chronological":
        parts.append("reason=recently changed evidence first")
    return "; ".join(parts)


def build_slice_plan(
    root: Path | str,
    strategy: SliceStrategy = "breadth-first",
    token_limit: int = DEFAULT_TOKEN_LIMIT,
    output_root: Path | str = ".system-map",
    output_layout: OutputLayout = "2-level",
) -> SlicePlan:
    root_path = Path(root).resolve()
    output_root_str = str(output_root)
    slices: list[PlannedSlice] = []
    current_paths: list[str] = []
    current_tokens = 0

    def flush() -> None:
        nonlocal current_paths, current_tokens
        if not current_paths:
            return
        component = _component_for(current_paths)
        slices.append(
            PlannedSlice(
                component=component,
                paths=current_paths,
                estimated_tokens=current_tokens,
                output_locations=_locations(output_root_str, output_layout, component),
                rationale=_slice_rationale(root_path, current_paths, strategy, current_tokens),
            )
        )
        current_paths = []
        current_tokens = 0

    for item in _ordered_items(root_path, strategy):
        item_tokens = estimate_tokens(item.size_bytes)
        if current_paths and current_tokens + item_tokens > token_limit:
            flush()
        # Huge single files are kept as their own slice but clearly marked over budget.
        current_paths.append(item.path)
        current_tokens += item_tokens
        if current_tokens >= token_limit:
            flush()
    flush()

    return SlicePlan(
        root=str(root_path),
        strategy=strategy,
        token_limit=token_limit,
        output_root=output_root_str,
        output_layout=output_layout,
        slices=slices,
    )
