from __future__ import annotations

from collections import Counter
from pathlib import Path

from .models import Inventory, InventoryItem

SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "venv",
    ".venv",
    "__pycache__",
    ".cache",
    "dist",
    "build",
    ".next",
    ".tox",
    "vendor",
    "third_party",
}

CODE_EXTS = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".kt": "Kotlin",
    ".rb": "Ruby",
    ".php": "PHP",
    ".cs": "C#",
    ".cpp": "C++",
    ".c": "C",
    ".h": "C/C++",
    ".sh": "Shell",
    ".sql": "SQL",
}
DOC_EXTS = {".md", ".rst", ".txt", ".adoc"}
CONFIG_NAMES = {"dockerfile", "makefile", "procfile"}
CONFIG_EXTS = {".yml", ".yaml", ".json", ".toml", ".ini", ".cfg", ".conf", ".env", ".xml"}


def classify(path: Path) -> tuple[str, str]:
    suffix = path.suffix.lower()
    name = path.name.lower()
    if suffix in CODE_EXTS:
        return "code", CODE_EXTS[suffix]
    if suffix in DOC_EXTS:
        return "document", suffix.lstrip(".").upper()
    if suffix in CONFIG_EXTS or name in CONFIG_NAMES:
        return "config", suffix.lstrip(".").upper() or name
    return "other", suffix.lstrip(".").upper() or "unknown"


def should_skip(path: Path, root: Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return True
    return any(part in SKIP_DIRS for part in rel.parts)


def build_inventory(root: Path | str) -> Inventory:
    root = Path(root).resolve()
    items: list[InventoryItem] = []
    for path in sorted(root.rglob("*")):
        if should_skip(path, root) or not path.is_file():
            continue
        kind, language = classify(path)
        items.append(InventoryItem(str(path.relative_to(root)), kind, language, path.stat().st_size))
    counts = Counter(item.kind for item in items)
    for key in ["code", "document", "config", "other"]:
        counts.setdefault(key, 0)
    return Inventory(str(root), items, dict(counts))
