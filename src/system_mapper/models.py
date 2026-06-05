from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class InventoryItem:
    path: str
    kind: str
    language: str
    size_bytes: int


@dataclass
class Inventory:
    root: str
    items: list[InventoryItem]
    counts_by_kind: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Evidence:
    source: str
    kind: str
    symbols: list[str] = field(default_factory=list)
    notes: str = ""
    freshness: str = "unknown"


@dataclass
class Edge:
    kind: str
    source: str
    target: str
    confidence: str = "medium"


@dataclass
class ComponentSummary:
    component: str
    scope: list[str]
    purpose: str
    evidence: list[Evidence]
    edges: list[Edge]
    entry_points: list[str]
    inputs: list[str]
    outputs: list[str]
    business_rules: list[str]
    human_steps: list[str]
    risks: list[str]
    unknowns: list[str]
    suggested_next: list[str]
    confidence: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ChangeUpdate:
    component: str
    changed_files: list[str]
    behaviour_changes: list[str]
    interface_changes: list[str]
    edge_changes: list[str]
    possibly_stale_sources: list[str]
    downstream_to_reinspect: list[str]
    unknowns: list[str]
    changelog_entry: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
