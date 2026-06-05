from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path

from .inventory import classify
from .models import ComponentSummary, Edge, Evidence

URL_RE = re.compile(r"https?://[^\s'\"),}]+")
FUNC_RE = re.compile(
    r"^\s*(?:def|function)\s+([A-Za-z_][\w]*)"
    r"|^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_][\w]*)"
    r"|^\s*class\s+([A-Za-z_][\w]*)",
    re.M,
)
TABLE_ASSIGN_RE = re.compile(r"\b[A-Za-z_]*TABLE[A-Za-z_]*\s*=\s*[\"']([A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)?)[\"']", re.I)
SQL_TABLE_RE = re.compile(r"\b(?:from|join)\s+([A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)?)", re.I)
CRON_RE = re.compile(r"(?:\d+|\*)\s+(?:\d+|\*)\s+(?:\d+|\*)\s+(?:\d+|\*)\s+(?:\d+|\*)")
MANUAL_RE = re.compile(r"\b(manual|human|admin|operator|retry|runbook|ask|approval)\b", re.I)
BUSINESS_RE = re.compile(r"\b(rule|must|cannot|should|policy|approval|required|limit|threshold)\b", re.I)


def _safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _content_revision(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _symbols(text: str) -> list[str]:
    found: list[str] = []
    for match in FUNC_RE.finditer(text):
        symbol = next((g for g in match.groups() if g), None)
        if symbol:
            found.append(symbol)
    return found[:20]


def _python_symbols(text: str) -> list[str]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return _symbols(text)

    nodes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    nodes.sort(key=lambda node: (node.lineno, node.col_offset))
    return [node.name for node in nodes[:20]]


def _sentence_with(text: str, pattern: re.Pattern[str]) -> str:
    for line in text.splitlines():
        if pattern.search(line):
            return line.strip()[:240]
    return ""


def _tables(text: str) -> list[str]:
    found: list[str] = []
    for table in TABLE_ASSIGN_RE.findall(text):
        found.append(table)
    for line in text.splitlines():
        stripped = line.strip().lower()
        if stripped.startswith(("from ", "import ", "from.")) or " import " in stripped:
            continue
        if any(keyword in stripped for keyword in ("select ", " update ", " delete ", " insert ", " join ")):
            found.extend(SQL_TABLE_RE.findall(line))
    return found


def _module_to_repo_path(root: Path, module: str) -> str | None:
    candidate = root.joinpath(*module.split("."))
    file_candidate = candidate.with_suffix(".py")
    if file_candidate.is_file():
        return str(file_candidate.relative_to(root))
    package_candidate = candidate / "__init__.py"
    if package_candidate.is_file():
        return str(package_candidate.relative_to(root))
    return None


def _relative_import_module(path: Path, root: Path, module: str | None, level: int) -> str | None:
    try:
        package_parts = path.parent.relative_to(root).parts
    except ValueError:
        return None
    keep = len(package_parts) - max(level - 1, 0)
    if keep < 0:
        return None
    parts = list(package_parts[:keep])
    if module:
        parts.extend(part for part in module.split(".") if part)
    return ".".join(parts) if parts else None


def _python_internal_dependencies(root: Path, path: Path, text: str) -> list[str]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []

    targets: list[str] = []
    seen: set[str] = set()

    def add(module: str | None) -> None:
        if not module:
            return
        target = _module_to_repo_path(root, module)
        if target and target not in seen:
            targets.append(target)
            seen.add(target)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                add(_relative_import_module(path, root, node.module, node.level))
            else:
                add(node.module)
    return targets


def summarize_component(root: Path | str, paths: list[Path | str], component: str | None = None) -> ComponentSummary:
    root = Path(root).resolve()
    resolved = [(Path(p) if Path(p).is_absolute() else root / str(p)).resolve() for p in paths]
    rel_scope = [str(p.relative_to(root)) if p.is_relative_to(root) else str(p) for p in resolved]
    name = component or (rel_scope[0] if len(rel_scope) == 1 else root.name)

    evidence: list[Evidence] = []
    edges: list[Edge] = []
    entry_points: list[str] = []
    inputs: list[str] = []
    outputs: list[str] = []
    business_rules: list[str] = []
    human_steps: list[str] = []
    suggested_next: list[str] = []

    for path, rel in zip(resolved, rel_scope):
        text = _safe_read(path)
        kind, _language = classify(path)
        if kind == "code" and path.suffix == ".py":
            symbols = _python_symbols(text)
        else:
            symbols = _symbols(text) if kind == "code" else []
        note_parts: list[str] = []
        if symbols:
            entry_points.extend(f"{rel}:{symbol}" for symbol in symbols[:8])
            note_parts.append("symbols: " + ", ".join(symbols[:8]))
        manual = _sentence_with(text, MANUAL_RE)
        if manual:
            human_steps.append(f"{rel}: {manual}")
            note_parts.append(manual)
        business = _sentence_with(text, BUSINESS_RE)
        if business:
            business_rules.append(f"{rel}: {business}")
        if CRON_RE.search(text):
            edges.append(Edge("trigger", rel, "cron schedule", "medium"))
        for url in URL_RE.findall(text):
            edges.append(Edge("external", rel, url, "high" if kind == "code" else "medium"))
        for table in _tables(text):
            if table.lower() not in {"table", "from", "join", "def", "function"}:
                edges.append(Edge("data_store", rel, table, "medium"))
        if kind == "code" and path.suffix == ".py":
            for target in _python_internal_dependencies(root, path, text):
                edges.append(Edge("internal", rel, target, "high"))
        if kind == "config":
            inputs.append(f"configuration: {rel}")
        if kind == "document":
            suggested_next.append(f"Check code/config that implements claims in {rel}")
        evidence.append(Evidence(rel, kind, symbols, "; ".join(note_parts), _content_revision(text)))

    purpose = "Evidence-backed component map for " + name
    if entry_points:
        purpose = f"Appears to expose code entry points for {name}: " + ", ".join(entry_points[:3])
    elif any(ev.kind == "document" for ev in evidence):
        purpose = f"Documented component/process area for {name}; implementation evidence may be incomplete."

    unknowns = []
    if not entry_points:
        unknowns.append("Code entry points not found in inspected scope")
    if human_steps:
        unknowns.append("Operational process needs human confirmation")
    if any(ev.kind == "document" for ev in evidence):
        unknowns.append("Documentation freshness not verified")
    if not edges:
        unknowns.append("No dependency/data-flow edges detected; inspect neighbouring files")

    risks = []
    if any(e.kind in {"external", "data_store"} for e in edges):
        risks.append("Touches external systems or data stores; changes may affect downstream behaviour")
    if human_steps:
        risks.append("Manual operational behaviour may be undocumented or stale")

    confidence = {
        "purpose": "high" if entry_points else "medium" if evidence else "low",
        "interfaces": "medium" if edges or entry_points else "low",
        "business_rules": "medium" if business_rules else "low",
        "operational_process": "medium" if human_steps else "low",
    }

    return ComponentSummary(
        component=name,
        scope=rel_scope,
        purpose=purpose,
        evidence=evidence,
        edges=edges,
        entry_points=entry_points,
        inputs=inputs,
        outputs=outputs,
        business_rules=business_rules,
        human_steps=human_steps,
        risks=risks,
        unknowns=unknowns,
        suggested_next=suggested_next,
        confidence=confidence,
    )
