from __future__ import annotations

import re
from typing import Any

from .models import ChangeUpdate

DIFF_FILE_RE = re.compile(r"^diff --git a/(.*?) b/(.*?)$", re.M)
ADDED_URL_RE = re.compile(r"^\+.*?(https?://[^\s'\"),}]+)", re.M)
REMOVED_URL_RE = re.compile(r"^-.*?(https?://[^\s'\"),}]+)", re.M)
ADDED_SYMBOL_RE = re.compile(r"^\+\s*(?:def|function|class)\s+([A-Za-z_][\w]*)", re.M)


def update_summary_from_diff(previous: dict[str, Any], diff: str) -> ChangeUpdate:
    component = str(previous.get("component", "unknown"))
    changed_files = []
    for _old, new in DIFF_FILE_RE.findall(diff):
        if new not in changed_files:
            changed_files.append(new)

    added_urls = ADDED_URL_RE.findall(diff)
    removed_urls = REMOVED_URL_RE.findall(diff)
    added_symbols = ADDED_SYMBOL_RE.findall(diff)

    behaviour_changes: list[str] = []
    interface_changes: list[str] = []
    edge_changes: list[str] = []
    possibly_stale_sources: list[str] = []

    for url in added_urls:
        behaviour_changes.append(f"New or changed external behaviour references {url}")
        edge_changes.append(f"external edge may now target {url}")
    for url in removed_urls:
        edge_changes.append(f"external edge may no longer target {url}")
    for symbol in added_symbols:
        interface_changes.append(f"New code entry point or type added: {symbol}")

    docs_changed = [path for path in changed_files if path.lower().endswith((".md", ".rst", ".txt", ".adoc"))]
    code_changed = [path for path in changed_files if path not in docs_changed]
    previous_sources = set(previous.get("last_updated_from", [])) | {
        edge.get("source", "") for edge in previous.get("edges", []) if isinstance(edge, dict)
    }
    if code_changed:
        for source in sorted(previous_sources):
            if source.lower().endswith((".md", ".rst", ".txt", ".adoc")):
                possibly_stale_sources.append(f"{source} may be stale after code changes: {', '.join(code_changed)}")
    for doc in docs_changed:
        if code_changed or added_urls or removed_urls:
            possibly_stale_sources.append(f"{doc} should be checked against implemented behaviour")

    if not behaviour_changes and changed_files:
        behaviour_changes.append("Files changed; no obvious behaviour change detected by heuristic diff scan")

    downstream = []
    for edge in previous.get("edges", []):
        if isinstance(edge, dict) and edge.get("target"):
            downstream.append(str(edge["target"]))

    unknowns = ["Heuristic diff analysis cannot prove runtime behaviour; re-run component slice summaries for changed files"]
    changelog_entry = f"{component}: changed {len(changed_files)} file(s); " + "; ".join(behaviour_changes[:3])
    return ChangeUpdate(
        component,
        changed_files,
        behaviour_changes,
        interface_changes,
        edge_changes,
        possibly_stale_sources,
        downstream[:20],
        unknowns,
        changelog_entry,
    )
