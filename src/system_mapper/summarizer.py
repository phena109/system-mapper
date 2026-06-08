from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path

from .inventory import classify
from .models import Claim, ComponentSummary, Edge, Evidence, EvidenceRecord

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
GO_SYMBOL_RE = re.compile(
    r"^\s*type\s+([A-Za-z_][\w]*)\s+(?:struct|interface|func|map|chan|\[|[A-Za-z_])"
    r"|^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_][\w]*)\s*\(",
    re.M,
)
GO_IMPORT_RE = re.compile(r"^\s*(?:[A-Za-z_][\w]*\s+)?[\"']([^\"']+)[\"']", re.M)
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
OWNER_RE = re.compile(r"\bowner\s*:\s*([^\n#]+)", re.I)
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
    if suffix == ".php":
        pattern = PHP_SYMBOL_RE
    elif suffix == ".go":
        pattern = GO_SYMBOL_RE
    else:
        pattern = C_LIKE_SYMBOL_RE
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


def _line_with(text: str, pattern: re.Pattern[str]) -> tuple[int, str] | None:
    for line_number, line in enumerate(text.splitlines(), start=1):
        if pattern.search(line):
            return line_number, line.strip()[:240]
    return None


def _line_excerpt(text: str, line_number: int) -> str:
    lines = text.splitlines() or [""]
    if line_number < 1 or line_number > len(lines):
        return ""
    return lines[line_number - 1].strip()[:240]


def _stable_id(prefix: str, *parts: object) -> str:
    joined = "\0".join(str(part) for part in parts)
    return f"{prefix}-{hashlib.sha256(joined.encode('utf-8')).hexdigest()[:12]}"


def _claim_id(component: str, claim_type: str, text: str, evidence_refs: list[str]) -> str:
    return _stable_id("claim", component, claim_type, text, ",".join(evidence_refs))


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


def _go_module_path(root: Path) -> str | None:
    go_mod = root / "go.mod"
    if not go_mod.is_file():
        return None
    for line in _safe_read(go_mod).splitlines():
        stripped = line.strip()
        if stripped.startswith("module "):
            return stripped.split(None, 1)[1].strip()
    return None


def _resolve_go_import(root: Path, import_path: str) -> str | None:
    module = _go_module_path(root)
    if not module or not import_path.startswith(f"{module}/"):
        return None
    rel_dir = import_path[len(module) + 1 :]
    candidate_dir = (root / rel_dir).resolve()
    if not candidate_dir.is_dir() or not candidate_dir.is_relative_to(root):
        return None
    go_files = sorted(path for path in candidate_dir.glob("*.go") if not path.name.endswith("_test.go"))
    if go_files:
        return str(go_files[0].relative_to(root))
    return None


def _go_internal_dependencies(root: Path, text: str) -> list[tuple[str, int | None]]:
    targets: list[tuple[str, int | None]] = []
    seen: set[str] = set()
    for line_number, line in enumerate(text.splitlines(), start=1):
        for import_path in GO_IMPORT_RE.findall(line):
            target = _resolve_go_import(root, import_path)
            if target and target not in seen:
                targets.append((target, line_number))
                seen.add(target)
    return targets


def _go_call_edges(rel: str, text: str) -> list[Edge]:
    defined = set(_c_like_symbols(text, ".go"))
    targets: list[Edge] = []
    seen: set[str] = set()
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith(("func ", "type ", "package ", "import ")):
            continue
        for name in C_LIKE_CALL_RE.findall(line):
            if name not in defined or name in seen:
                continue
            targets.append(Edge("call", rel, f"{rel}:{name}", "medium", line_number))
            seen.add(name)
    return targets


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

    if suffix == ".go":
        return _go_internal_dependencies(root, text)

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
    if path.suffix.lower() == ".go":
        return _go_call_edges(rel, text)
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


def summarize_component(root: Path, paths: list[str], component: str, exclude_patterns: list[str] | None = None, exclude_list: list[str] | None = None) -> ComponentSummary:
    # --- Filtering Logic Start ---
    if exclude_patterns or exclude_list:
        print("Applying exclusion filters...") # Debugging aid for implementation tracking
        # Convert glob patterns to regex-safe strings if necessary, but for now, treat as literal match targets
        # In a real implementation, glob patterns should be expanded to regexes matching path components.
        # For this MVP, we check if the full relative path matches any pattern/list item.
        filtered_paths = []
        for p in paths:
            path_str = str(p) # Assuming 'paths' contains Path objects or strings that resolve to them
            is_excluded = False
            if exclude_patterns and any(re.search(pattern, path_str) for pattern in exclude_patterns):
                is_excluded = True
            if not is_excluded and exclude_list and any(path_str == item for item in exclude_list):
                is_excluded = True

            if not is_excluded:
                filtered_paths.append(p)
        
        # Replace the original paths with filtered ones for processing downstream
        paths = filtered_paths
    # --- Filtering Logic End ---

    evidence: list[Evidence] = []
    evidence_ledger: list[EvidenceRecord] = []
    claims: list[Claim] = []
    edge_evidence_refs: dict[tuple[str, str, str, int | None], str] = {}
    edges: list[Edge] = []
    entry_points: list[str] = []
    inputs: list[str] = []
    outputs: list[str] = []
    business_rules: list[str] = []
    human_steps: list[str] = []
    suggested_next: list[str] = []

    def add_evidence_record(rel: str, line_number: int | None, kind: str, excerpt: str, freshness: str) -> str:
        line_start = max(line_number or 1, 1)
        source_key = f"{rel}:{line_start}:{kind}:{excerpt}"
        record_id = _stable_id("ev", source_key, freshness)
        if not any(record.id == record_id for record in evidence_ledger):
            evidence_ledger.append(
                EvidenceRecord(
                    id=record_id,
                    source=rel,
                    line_start=line_start,
                    line_end=line_start,
                    kind=kind,
                    excerpt=excerpt,
                    freshness=freshness,
                )
            )
        return record_id

    def add_claim(claim_type: str, text: str, confidence: str, evidence_refs: list[str]) -> None:
        refs = [ref for ref in evidence_refs if ref]
        if not refs and evidence_ledger:
            refs = [evidence_ledger[0].id]
        if not refs:
            return
        claim = Claim(_claim_id(name, claim_type, text, refs), claim_type, text, confidence, refs, state="active")
        if not any(existing.id == claim.id for existing in claims):
            claims.append(claim)

    def add_edge(edge: Edge, evidence_ref: str | None = None) -> None:
        edges.append(edge)
        if evidence_ref:
            edge_evidence_refs[(edge.kind, edge.source, edge.target, edge.source_line)] = evidence_ref

    def add_sourced_edge(edge: Edge, freshness: str) -> None:
        if edge.source_line is None:
            add_edge(edge)
            return
        edge_ref = add_evidence_record(
            edge.source,
            edge.source_line,
            f"{edge.kind}_edge",
            _line_excerpt(text, edge.source_line),
            freshness,
        )
        add_edge(edge, edge_ref)

    for path, rel in zip(resolved, rel_scope):
        text = _safe_read(path)
        kind, _language = classify(path)
        freshness = _content_revision(text)
        file_ref = add_evidence_record(rel, 1, kind, _line_excerpt(text, 1) or rel, freshness)
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
            add_claim("purpose", f"{rel} exposes code entry points: " + ", ".join(symbols[:3]), "medium", [file_ref])
        manual_match = _line_with(text, MANUAL_RE)
        manual = manual_match[1] if manual_match else ""
        if manual:
            human_steps.append(f"{rel}: {manual}")
            note_parts.append(manual)
            manual_ref = add_evidence_record(rel, manual_match[0], "human_step", manual, freshness) if manual_match else file_ref
            add_claim("human_step", f"Manual or operator process observed in {rel}: {manual}", "medium", [manual_ref])
        business_match = _line_with(text, BUSINESS_RE)
        business = business_match[1] if business_match else ""
        if business:
            business_rules.append(f"{rel}: {business}")
            business_ref = add_evidence_record(rel, business_match[0], "business_rule", business, freshness) if business_match else file_ref
            add_claim("business_rule", f"Business rule in {rel}: {business}", "medium", [business_ref])
        owner_match = _line_with(text, OWNER_RE)
        if owner_match:
            owner_text = OWNER_RE.search(owner_match[1])
            owner = owner_text.group(1).strip(" .#") if owner_text else owner_match[1]
            owner_ref = add_evidence_record(rel, owner_match[0], "owner", owner_match[1], freshness)
            add_claim("owner", f"Owner for {name}: {owner}", "medium", [owner_ref])
        cron_line = _first_match_line(text, CRON_RE)
        if cron_line is not None:
            trigger_ref = add_evidence_record(rel, cron_line, "trigger", _line_excerpt(text, cron_line), freshness)
            add_edge(Edge("trigger", rel, "cron schedule", "medium", cron_line), trigger_ref)
            add_claim("trigger", f"{rel} declares a cron trigger", "medium", [trigger_ref])
        for url, line_number in _urls_with_lines(text):
            url_ref = add_evidence_record(rel, line_number, "external", _line_excerpt(text, line_number), freshness)
            add_edge(Edge("external", rel, url, "high" if kind == "code" else "medium", line_number), url_ref)
            add_claim("external_dependency", f"{name} references external system {url}", "high" if kind == "code" else "medium", [url_ref])
        for table, line_number in _tables_with_lines(text):
            if table.lower() not in {"table", "from", "join", "def", "function"}:
                table_ref = add_evidence_record(rel, line_number, "data_contract", _line_excerpt(text, line_number), freshness)
                add_edge(Edge("data_store", rel, table, "medium", line_number), table_ref)
                add_claim("data_contract", f"{name} reads or writes data store/table {table}", "medium", [table_ref])
        if kind == "code" and path.suffix == ".py":
            for edge in _python_call_edges(path, root, text):
                add_sourced_edge(edge, freshness)
            for edge in _python_route_edges(path, root, text):
                add_sourced_edge(edge, freshness)
            for target, line_number in _python_internal_dependencies(root, path, text):
                add_sourced_edge(Edge("internal", rel, target, "high", line_number), freshness)
        if kind == "code" and path.suffix.lower() in {".js", ".jsx", ".ts", ".tsx"}:
            for edge in _javascript_call_edges(path, root, text):
                add_sourced_edge(edge, freshness)
            for edge in _javascript_route_edges(path, root, text):
                add_sourced_edge(edge, freshness)
            for target, line_number in _javascript_internal_dependencies(root, path, text):
                add_sourced_edge(Edge("internal", rel, target, "high", line_number), freshness)
        if kind == "code" and path.suffix.lower() in C_LIKE_EXTS:
            for edge in _c_like_call_edges(path, root, text):
                add_sourced_edge(edge, freshness)
            if path.suffix.lower() == ".php":
                for edge in _php_route_edges(path, root, text):
                    add_sourced_edge(edge, freshness)
            for target, line_number in _c_like_internal_dependencies(root, path, text):
                add_sourced_edge(Edge("internal", rel, target, "high", line_number), freshness)
        if kind == "config":
            inputs.append(f"configuration: {rel}")
        if kind == "document":
            suggested_next.append(f"Check code/config that implements claims in {rel}")
        evidence.append(Evidence(rel, kind, symbols, "; ".join(note_parts), freshness))

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

    first_ref = evidence_ledger[0].id if evidence_ledger else ""
    add_claim("purpose", purpose, confidence["purpose"], [first_ref])
    for risk in risks:
        risk_refs = [
            edge_evidence_refs[key]
            for key in edge_evidence_refs
            if key[0] in {"external", "data_store", "trigger"}
        ] or [first_ref]
        add_claim("risk", risk, "medium", risk_refs[:5])
    for unknown in unknowns:
        add_claim("unknown", unknown, "low", [first_ref])

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
        claims=claims,
        evidence_ledger=evidence_ledger,
    )
