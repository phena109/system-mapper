from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path

from .inventory import classify
from .models import ComponentSummary, Edge, Evidence

URL_RE = re.compile(r"https?://[^\s'\"),}]+")
FUNC_RE = re.compile(
    r"^\s*(?:async\s+)?def\s+([A-Za-z_][\w]*)"
    r"|^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_][\w]*)"
    r"|^\s*(?:export\s+)?class\s+([A-Za-z_][\w]*)"
    r"|^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:function\b|\([^)]*\)\s*=>|[A-Za-z_$][\w$]*\s*=>)",
    re.M,
)
TABLE_ASSIGN_RE = re.compile(r"\b[A-Za-z_]*TABLE[A-Za-z_]*\s*=\s*[\"']([A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)?)[\"']", re.I)
SQL_TABLE_RE = re.compile(r"\b(?:from|join)\s+([A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)?)", re.I)
JS_IMPORT_RE = re.compile(
    r"(?:import\s+(?:[^'\"]+?\s+from\s+)?|export\s+[^'\"]+?\s+from\s+|require\s*\(|import\s*\()"
    r"[\"']([^\"']+)[\"']"
)
JS_METHOD_DEF_RE = re.compile(r"^\s*([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{")
JS_CALL_RE = re.compile(r"(?:\bnew\s+|\.\s*)?([A-Za-z_$][\w$]*)\s*\(")
JS_ROUTE_METHOD_RE = re.compile(
    r"\b(?:app|router)\s*\.\s*(get|post|put|patch|delete|options|head)\s*\(\s*[\"']([^\"']+)[\"']",
    re.I,
)
JS_ROUTE_CHAIN_RE = re.compile(
    r"\b(?:app|router)\s*\.\s*route\s*\(\s*[\"']([^\"']+)[\"']\s*\)\s*\.\s*(get|post|put|patch|delete|options|head)\s*\(",
    re.I,
)
C_LIKE_EXTS = {".php", ".c", ".h", ".cpp", ".hpp", ".cc", ".cxx", ".java", ".cs", ".go"}
PHP_SYMBOL_RE = re.compile(
    r"^\s*(?:abstract\s+|final\s+)?(?:class|interface|trait)\s+([A-Za-z_][\w]*)"
    r"|^\s*(?:(?:public|protected|private|static|abstract|final)\s+)*function\s+([A-Za-z_][\w]*)\s*\(",
    re.M,
)
C_LIKE_SYMBOL_RE = re.compile(
    r"^\s*(?:public|private|protected|static|final|abstract|async|extern|inline|virtual|const|unsigned|signed|long|short|struct\s+|enum\s+|class\s+)*"
    r"(?:[A-Za-z_][\w:<>,*&\[\]\s]+\s+)+([A-Za-z_][\w]*)\s*\([^;{}]*\)\s*(?:\{|=>)"
    r"|^\s*(?:public\s+)?(?:class|interface|struct|enum)\s+([A-Za-z_][\w]*)",
    re.M,
)
C_LIKE_CALL_RE = re.compile(r"(?:\bnew\s+|::\s*|->\s*|\.\s*)?([A-Za-z_][\w]*)\s*\(")
PHP_INCLUDE_RE = re.compile(r"\b(?:require|require_once|include|include_once)\b\s*(?:\(?\s*)?(.+?);", re.I)
STRING_RE = re.compile(r"[\"']([^\"']+)[\"']")
PHP_ROUTE_RE = re.compile(r"\b(?:Route|router|app)\s*(?:::|->)\s*(get|post|put|patch|delete|options|head)\s*\(\s*[\"']([^\"']+)[\"']", re.I)
C_INCLUDE_RE = re.compile(r"^\s*#\s*include\s+[\"<]([^\">]+)[\">]", re.M)
CRON_RE = re.compile(r"(?:\d+|\*)\s+(?:\d+|\*)\s+(?:\d+|\*)\s+(?:\d+|\*)\s+(?:\d+|\*)")
MANUAL_RE = re.compile(r"\b(manual|human|admin|operator|retry|runbook|ask|approval)\b", re.I)
BUSINESS_RE = re.compile(r"\b(rule|must|cannot|should|policy|approval|required|limit|threshold)\b", re.I)
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "route"}


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


def _c_like_symbols(text: str, suffix: str) -> list[str]:
    pattern = PHP_SYMBOL_RE if suffix == ".php" else C_LIKE_SYMBOL_RE
    found: list[str] = []
    for match in pattern.finditer(text):
        symbol = next((g for g in match.groups() if g), None)
        if symbol and symbol not in found:
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
    return [table for table, _line_number in _tables_with_lines(text)]


def _urls_with_lines(text: str) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for url in URL_RE.findall(line):
            found.append((url, line_number))
    return found


def _tables_with_lines(text: str) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for table in TABLE_ASSIGN_RE.findall(line):
            found.append((table, line_number))
        stripped = line.strip().lower()
        if stripped.startswith(("from ", "import ", "from.")) or " import " in stripped:
            continue
        if any(keyword in stripped for keyword in ("select ", " update ", " delete ", " insert ", " join ")):
            found.extend((table, line_number) for table in SQL_TABLE_RE.findall(line))
    return found


def _first_match_line(text: str, pattern: re.Pattern[str]) -> int | None:
    for line_number, line in enumerate(text.splitlines(), start=1):
        if pattern.search(line):
            return line_number
    return None


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


def _python_call_edges(path: Path, root: Path, text: str) -> list[Edge]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []

    try:
        rel = str(path.relative_to(root))
    except ValueError:
        rel = str(path)
    defined = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    targets: list[Edge] = []
    seen: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = ""
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
        if name in defined and name not in seen:
            targets.append(Edge("call", rel, f"{rel}:{name}", "medium", getattr(node, "lineno", None)))
            seen.add(name)
    return targets


def _literal_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _literal_string_list(node: ast.AST) -> list[str]:
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return [value for element in node.elts if (value := _literal_string(element))]
    value = _literal_string(node)
    return [value] if value else []


def _route_methods(decorator: ast.Call, decorator_method: str) -> list[str]:
    if decorator_method != "route":
        return [decorator_method.upper()]
    for keyword in decorator.keywords:
        if keyword.arg == "methods":
            methods = [method.upper() for method in _literal_string_list(keyword.value)]
            return methods or ["GET"]
    return ["GET"]


def _python_route_edges(path: Path, root: Path, text: str) -> list[Edge]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []

    try:
        rel = str(path.relative_to(root))
    except ValueError:
        rel = str(path)

    routes: list[Edge] = []
    seen: set[tuple[str, int | None]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                continue
            method = decorator.func.attr.lower()
            if method not in HTTP_METHODS or not decorator.args:
                continue
            route_path = _literal_string(decorator.args[0])
            if not route_path:
                continue
            for route_method in _route_methods(decorator, method):
                target = f"{route_method} {route_path}"
                key = (target, getattr(decorator, "lineno", None))
                if key in seen:
                    continue
                routes.append(Edge("route", rel, target, "high", getattr(decorator, "lineno", None)))
                seen.add(key)
    return routes


def _python_internal_dependencies(root: Path, path: Path, text: str) -> list[tuple[str, int | None]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []

    targets: list[tuple[str, int | None]] = []
    seen: set[str] = set()

    def add(module: str | None, line_number: int | None) -> bool:
        if not module:
            return False
        target = _module_to_repo_path(root, module)
        if not target:
            return False
        if target not in seen:
            targets.append((target, line_number))
            seen.add(target)
        return True

    def add_import_from(node: ast.ImportFrom) -> None:
        base_module = _relative_import_module(path, root, node.module, node.level) if node.level else node.module
        found_imported_submodule = False
        for alias in node.names:
            if alias.name == "*" or not base_module:
                continue
            found_imported_submodule = add(f"{base_module}.{alias.name}", getattr(node, "lineno", None)) or found_imported_submodule
        if not found_imported_submodule:
            add(base_module, getattr(node, "lineno", None))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                add(alias.name, getattr(node, "lineno", None))
        elif isinstance(node, ast.ImportFrom):
            add_import_from(node)
    return targets


def _relative_js_target(root: Path, path: Path, specifier: str) -> str | None:
    if not specifier.startswith(("./", "../")):
        return None
    base = (path.parent / specifier).resolve()
    candidates: list[Path] = []
    if base.suffix:
        candidates.append(base)
    else:
        for suffix in (".ts", ".tsx", ".js", ".jsx"):
            candidates.append(base.with_suffix(suffix))
        for suffix in (".ts", ".tsx", ".js", ".jsx"):
            candidates.append(base / f"index{suffix}")
    for candidate in candidates:
        if candidate.is_file() and candidate.is_relative_to(root):
            return str(candidate.relative_to(root))
    return None


def _javascript_internal_dependencies(root: Path, path: Path, text: str) -> list[tuple[str, int | None]]:
    targets: list[tuple[str, int | None]] = []
    seen: set[str] = set()
    for line_number, line in enumerate(text.splitlines(), start=1):
        for specifier in JS_IMPORT_RE.findall(line):
            target = _relative_js_target(root, path, specifier)
            if target and target not in seen:
                targets.append((target, line_number))
                seen.add(target)
    return targets


def _javascript_defined_symbols(text: str) -> set[str]:
    symbols = set(_symbols(text))
    for line in text.splitlines():
        match = JS_METHOD_DEF_RE.match(line)
        if match and match.group(1) not in {"if", "for", "while", "switch", "catch", "function"}:
            symbols.add(match.group(1))
    return symbols


def _javascript_call_edges(path: Path, root: Path, text: str) -> list[Edge]:
    try:
        rel = str(path.relative_to(root))
    except ValueError:
        rel = str(path)
    defined = _javascript_defined_symbols(text)
    targets: list[Edge] = []
    seen: set[str] = set()
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        for name in JS_CALL_RE.findall(line):
            if name not in defined or name in seen:
                continue
            if stripped.startswith((f"function {name}", f"async function {name}")):
                continue
            if re.match(rf"^(?:export\s+)?(?:const|let|var)\s+{re.escape(name)}\b", stripped):
                continue
            method_definition = JS_METHOD_DEF_RE.match(stripped)
            if method_definition and method_definition.group(1) == name:
                continue
            targets.append(Edge("call", rel, f"{rel}:{name}", "medium", line_number))
            seen.add(name)
    return targets


def _javascript_route_edges(path: Path, root: Path, text: str) -> list[Edge]:
    try:
        rel = str(path.relative_to(root))
    except ValueError:
        rel = str(path)
    routes: list[Edge] = []
    seen: set[tuple[str, int]] = set()
    for line_number, line in enumerate(text.splitlines(), start=1):
        for method, route_path in JS_ROUTE_METHOD_RE.findall(line):
            target = f"{method.upper()} {route_path}"
            key = (target, line_number)
            if key not in seen:
                routes.append(Edge("route", rel, target, "medium", line_number))
                seen.add(key)
        for route_path, method in JS_ROUTE_CHAIN_RE.findall(line):
            target = f"{method.upper()} {route_path}"
            key = (target, line_number)
            if key not in seen:
                routes.append(Edge("route", rel, target, "medium", line_number))
                seen.add(key)
    return routes


def _resolve_relative_candidate(root: Path, path: Path, specifier: str, suffixes: tuple[str, ...]) -> str | None:
    base = (path.parent / specifier).resolve()
    candidates: list[Path] = [base] if base.suffix else []
    if not candidates:
        candidates.extend(base.with_suffix(suffix) for suffix in suffixes)
        candidates.extend(base / f"index{suffix}" for suffix in suffixes)
    for candidate in candidates:
        if candidate.is_file() and candidate.is_relative_to(root):
            return str(candidate.relative_to(root))
    return None


def _php_include_specifier(expression: str) -> str | None:
    strings = STRING_RE.findall(expression)
    if not strings:
        return None
    # Handles common forms like __DIR__ . '/Auth/Token.php' by using the
    # include path fragment rather than the __DIR__ sentinel itself.
    specifier = strings[-1]
    if "__DIR__" in expression:
        specifier = specifier.lstrip("/")
    return specifier


def _c_like_internal_dependencies(root: Path, path: Path, text: str) -> list[tuple[str, int | None]]:
    suffix = path.suffix.lower()
    targets: list[tuple[str, int | None]] = []
    seen: set[str] = set()

    def add(target: str | None, line_number: int | None) -> None:
        if target and target not in seen:
            targets.append((target, line_number))
            seen.add(target)

    if suffix == ".php":
        for line_number, line in enumerate(text.splitlines(), start=1):
            for expression in PHP_INCLUDE_RE.findall(line):
                specifier = _php_include_specifier(expression)
                if specifier:
                    add(_resolve_relative_candidate(root, path, specifier, (".php",)), line_number)
        return targets

    if suffix in {".c", ".h", ".cpp", ".hpp", ".cc", ".cxx"}:
        for line_number, line in enumerate(text.splitlines(), start=1):
            for specifier in C_INCLUDE_RE.findall(line):
                add(_resolve_relative_candidate(root, path, specifier, (".h", ".hpp", ".c", ".cpp", ".cc", ".cxx")), line_number)
    return targets


def _c_like_call_edges(path: Path, root: Path, text: str) -> list[Edge]:
    try:
        rel = str(path.relative_to(root))
    except ValueError:
        rel = str(path)
    targets: list[Edge] = []
    seen: set[tuple[str, int]] = set()
    skip_names = {
        "if",
        "for",
        "while",
        "switch",
        "catch",
        "return",
        "function",
        "class",
        "include",
        "include_once",
        "require",
        "require_once",
        "strtolower",
        "array",
        "echo",
    }
    declaration_prefixes = ("function ", "public function ", "protected function ", "private function ", "class ")
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith(declaration_prefixes) or stripped.startswith("#include"):
            continue
        for name in C_LIKE_CALL_RE.findall(line):
            if name in skip_names:
                continue
            key = (name, line_number)
            if key in seen:
                continue
            targets.append(Edge("call", rel, f"{rel}:{name}", "medium", line_number))
            seen.add(key)
    return targets


def _php_route_edges(path: Path, root: Path, text: str) -> list[Edge]:
    try:
        rel = str(path.relative_to(root))
    except ValueError:
        rel = str(path)
    routes: list[Edge] = []
    seen: set[tuple[str, int]] = set()
    for line_number, line in enumerate(text.splitlines(), start=1):
        for method, route_path in PHP_ROUTE_RE.findall(line):
            target = f"{method.upper()} {route_path}"
            key = (target, line_number)
            if key not in seen:
                routes.append(Edge("route", rel, target, "medium", line_number))
                seen.add(key)
    return routes


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
        elif kind == "code" and path.suffix.lower() in C_LIKE_EXTS:
            symbols = _c_like_symbols(text, path.suffix.lower())
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
        cron_line = _first_match_line(text, CRON_RE)
        if cron_line is not None:
            edges.append(Edge("trigger", rel, "cron schedule", "medium", cron_line))
        for url, line_number in _urls_with_lines(text):
            edges.append(Edge("external", rel, url, "high" if kind == "code" else "medium", line_number))
        for table, line_number in _tables_with_lines(text):
            if table.lower() not in {"table", "from", "join", "def", "function"}:
                edges.append(Edge("data_store", rel, table, "medium", line_number))
        if kind == "code" and path.suffix == ".py":
            edges.extend(_python_call_edges(path, root, text))
            edges.extend(_python_route_edges(path, root, text))
            for target, line_number in _python_internal_dependencies(root, path, text):
                edges.append(Edge("internal", rel, target, "high", line_number))
        if kind == "code" and path.suffix.lower() in {".js", ".jsx", ".ts", ".tsx"}:
            edges.extend(_javascript_call_edges(path, root, text))
            edges.extend(_javascript_route_edges(path, root, text))
            for target, line_number in _javascript_internal_dependencies(root, path, text):
                edges.append(Edge("internal", rel, target, "high", line_number))
        if kind == "code" and path.suffix.lower() in C_LIKE_EXTS:
            edges.extend(_c_like_call_edges(path, root, text))
            if path.suffix.lower() == ".php":
                edges.extend(_php_route_edges(path, root, text))
            for target, line_number in _c_like_internal_dependencies(root, path, text):
                edges.append(Edge("internal", rel, target, "high", line_number))
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
