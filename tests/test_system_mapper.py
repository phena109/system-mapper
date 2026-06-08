from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from system_mapper.inventory import build_inventory
from system_mapper.packet import build_work_packet
from system_mapper.planner import DEFAULT_TOKEN_LIMIT, build_slice_plan
from system_mapper.runner import run_next_slice
from system_mapper.summarizer import summarize_component
from system_mapper.update import update_summary_from_diff


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_inventory_discovers_code_docs_configs_and_ignores_dependencies(tmp_path: Path):
    write(tmp_path / "src" / "billing.py", "def export_invoice():\n    pass\n")
    write(tmp_path / "docs" / "billing.md", "# Billing\nExports run nightly.\n")
    write(tmp_path / "config" / "schedule.yml", "cron: nightly\n")
    write(tmp_path / "node_modules" / "ignored.js", "console.log('ignore')\n")
    write(tmp_path / ".pytest_cache" / "README.md", "# cache\n")

    inventory = build_inventory(tmp_path)

    rel_paths = {item.path for item in inventory.items}
    assert "src/billing.py" in rel_paths
    assert "docs/billing.md" in rel_paths
    assert "config/schedule.yml" in rel_paths
    assert "node_modules/ignored.js" not in rel_paths
    assert ".pytest_cache/README.md" not in rel_paths
    assert inventory.counts_by_kind["code"] == 1
    assert inventory.counts_by_kind["document"] == 1
    assert inventory.counts_by_kind["config"] == 1


def test_summary_separates_code_doc_config_evidence_unknowns_and_edges(tmp_path: Path):
    code = write(
        tmp_path / "src" / "billing.py",
        """
import requests
DATABASE_TABLE = "invoices"

def export_invoice(invoice_id):
    requests.post("https://partner.example/export", json={"invoice_id": invoice_id})
    return invoice_id
""".strip(),
    )
    doc = write(tmp_path / "docs" / "billing.md", "# Billing\nInvoice exports run nightly and may need manual retry.\n")
    config = write(tmp_path / "config" / "schedule.yml", "billing_export: 0 2 * * *\n")

    summary = summarize_component(tmp_path, [code, doc, config], component="billing/export")

    assert summary.component == "billing/export"
    assert any(ev.kind == "code" and "export_invoice" in ev.symbols for ev in summary.evidence)
    assert any(ev.kind == "document" and "manual" in ev.notes.lower() for ev in summary.evidence)
    assert any(edge.kind == "external" and "partner.example" in edge.target for edge in summary.edges)
    assert any(edge.kind == "data_store" and "invoices" in edge.target for edge in summary.edges)
    assert "Operational process needs human confirmation" in summary.unknowns
    assert summary.confidence["purpose"] in {"medium", "high"}


def test_summary_evidence_records_content_revision_for_freshness_checks(tmp_path: Path):
    write(tmp_path / "docs" / "billing.md", "# Billing\nInvoice exports run nightly.\n")

    summary = summarize_component(tmp_path, ["docs/billing.md"], component="billing/docs")

    assert summary.evidence[0].freshness == "sha256:1051cdbbc1c6"


def test_update_from_diff_marks_changed_behaviour_stale_docs_and_affected_edges(tmp_path: Path):
    previous = {
        "component": "billing/export",
        "purpose": "Exports invoices to partner.example.",
        "edges": [{"kind": "external", "source": "src/billing.py", "target": "https://partner.example/export"}],
        "last_updated_from": ["src/billing.py", "docs/billing.md"],
    }
    diff = """
diff --git a/src/billing.py b/src/billing.py
@@
-    requests.post("https://partner.example/export", json=payload)
+    requests.post("https://api.newpartner.example/v2/export", json=payload)
diff --git a/docs/billing.md b/docs/billing.md
@@
-Invoice exports use partner.example.
+Invoice exports use partner.example.
"""

    update = update_summary_from_diff(previous, diff)

    assert "src/billing.py" in update.changed_files
    assert any("api.newpartner.example" in change for change in update.behaviour_changes)
    assert any("docs/billing.md" in stale for stale in update.possibly_stale_sources)
    assert any("external" in edge for edge in update.edge_changes)


def test_update_from_diff_reports_added_python_route_interfaces():
    previous = {
        "component": "maps/api",
        "edges": [{"kind": "route", "source": "src/api.py", "target": "GET /maps/{map_id}"}],
        "last_updated_from": ["src/api.py", "docs/api.md"],
    }
    diff = """
diff --git a/src/api.py b/src/api.py
@@
 @router.get("/maps/{map_id}")
 def read_map(map_id: str):
     return {"map_id": map_id}
+
+@router.post("/maps")
+async def create_map():
+    return {}
"""

    update = update_summary_from_diff(previous, diff)

    assert "src/api.py" in update.changed_files
    assert "New route interface added: POST /maps" in update.interface_changes
    assert "route edge may now target POST /maps" in update.edge_changes
    assert not any("no obvious behaviour" in change for change in update.behaviour_changes)
    assert "New route interface added: POST /maps" in update.changelog_entry
    assert any("docs/api.md may be stale after code changes" in stale for stale in update.possibly_stale_sources)


def test_cli_inventory_and_slice_emit_json(tmp_path: Path):
    write(tmp_path / "src" / "app.py", "def main():\n    return 'ok'\n")
    result = subprocess.run(
        [sys.executable, "-m", "system_mapper.cli", "inventory", str(tmp_path), "--json"],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    payload = json.loads(result.stdout)
    assert payload["counts_by_kind"]["code"] == 1

    result = subprocess.run(
        [sys.executable, "-m", "system_mapper.cli", "slice", str(tmp_path), "src/app.py", "--component", "app", "--json"],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    payload = json.loads(result.stdout)
    assert payload["component"] == "app"
    assert payload["scope"] == ["src/app.py"]


def test_cli_prompt_outputs_low_context_ai_contract():
    result = subprocess.run(
        [sys.executable, "-m", "system_mapper.cli", "prompt", "slice", "--component", "billing/export"],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )

    assert "Analyse only the files, summaries, and artefacts provided" in result.stdout
    assert "billing/export" in result.stdout
    assert "Code evidence" in result.stdout
    assert "Do not assume documentation is current" in result.stdout
    assert "Machine-readable edges" in result.stdout


def test_summary_records_source_lines_for_detected_edges(tmp_path: Path):
    write(
        tmp_path / "src" / "billing.py",
        """
import requests
DATABASE_TABLE = "invoices"

def export_invoice(invoice_id):
    requests.post("https://partner.example/export", json={"invoice_id": invoice_id})
""".strip(),
    )

    summary = summarize_component(tmp_path, ["src/billing.py"], component="billing/export")

    external = next(edge for edge in summary.edges if edge.kind == "external")
    data_store = next(edge for edge in summary.edges if edge.kind == "data_store")
    assert external.source_line == 5
    assert data_store.source_line == 2


def test_cli_graph_emits_slice_edges_as_jsonl(tmp_path: Path):
    write(
        tmp_path / "src" / "billing.py",
        """
import requests
DATABASE_TABLE = "invoices"

def export_invoice(invoice_id):
    requests.post("https://partner.example/export", json={"invoice_id": invoice_id})
""".strip(),
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "system_mapper.cli",
            "graph",
            str(tmp_path),
            "src/billing.py",
            "--component",
            "billing/export",
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )

    records = [json.loads(line) for line in result.stdout.splitlines()]
    assert any(record["kind"] == "external" and record["target"] == "https://partner.example/export" for record in records)
    assert any(record["kind"] == "external" and record["source_line"] == 5 for record in records)
    assert any(record["kind"] == "data_store" and record["target"] == "invoices" for record in records)
    assert all(record["component"] == "billing/export" for record in records)
    assert all(record["source"] == "src/billing.py" for record in records)


def test_cli_graph_can_emit_mermaid_flowchart_for_visual_map_reviews(tmp_path: Path):
    write(tmp_path / "src" / "leaf.py", "def helper():\n    return 'ok'\n")
    write(
        tmp_path / "src" / "orchestrator.py",
        """
import src.leaf
DATABASE_TABLE = "system_maps"

def run_mapping():
    return src.leaf.helper()
""".strip(),
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "system_mapper.cli",
            "graph",
            str(tmp_path),
            "src/orchestrator.py",
            "--component",
            "system/orchestrator",
            "--format",
            "mermaid",
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )

    assert result.stdout.splitlines()[0] == "flowchart TD"
    assert 'src_orchestrator_py["src/orchestrator.py"]' in result.stdout
    assert 'src_leaf_py["src/leaf.py"]' in result.stdout
    assert 'src_orchestrator_py -->|internal / high| src_leaf_py' in result.stdout
    assert 'system_maps["system_maps"]' in result.stdout
    assert 'src_orchestrator_py -->|data_store / medium| system_maps' in result.stdout


def test_cli_graph_can_emit_dot_for_graphviz_system_map_reviews(tmp_path: Path):
    write(tmp_path / "src" / "leaf.py", "def helper():\n    return 'ok'\n")
    write(
        tmp_path / "src" / "orchestrator.py",
        """
import src.leaf
DATABASE_TABLE = "system_maps"

def run_mapping():
    return src.leaf.helper()
""".strip(),
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "system_mapper.cli",
            "graph",
            str(tmp_path),
            "src/orchestrator.py",
            "--component",
            "system/orchestrator",
            "--format",
            "dot",
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )

    assert result.stdout.splitlines()[0] == "digraph system_map {"
    assert '  "src/orchestrator.py" [label="src/orchestrator.py"];' in result.stdout
    assert '  "src/leaf.py" [label="src/leaf.py"];' in result.stdout
    assert '  "src/orchestrator.py" -> "src/leaf.py" [label="internal / high"];' in result.stdout
    assert '  "system_maps" [label="system_maps"];' in result.stdout
    assert '  "src/orchestrator.py" -> "system_maps" [label="data_store / medium"];' in result.stdout
    assert result.stdout.splitlines()[-1] == "}"


def test_graph_clusters_group_connected_edges_and_preserve_evidence_sources():
    from system_mapper.clusters import cluster_edge_records

    records = [
        {"component": "billing/api", "kind": "route", "source": "src/api.py", "target": "POST /invoices", "confidence": "high", "source_line": 10},
        {"component": "billing/api", "kind": "internal", "source": "src/api.py", "target": "src/service.py", "confidence": "high", "source_line": 2},
        {"component": "billing/service", "kind": "data_store", "source": "src/service.py", "target": "invoices", "confidence": "medium", "source_line": 4},
        {"component": "ops/worker", "kind": "trigger", "source": "config/schedule.yml", "target": "cron schedule", "confidence": "medium", "source_line": 1},
    ]

    report = cluster_edge_records(records)

    assert report["cluster_count"] == 2
    first = report["clusters"][0]
    assert first["id"] == "cluster-001"
    assert first["nodes"] == ["POST /invoices", "invoices", "src/api.py", "src/service.py"]
    assert first["edge_count"] == 3
    assert first["edge_kinds"] == ["data_store", "internal", "route"]
    assert first["components"] == ["billing/api", "billing/service"]
    assert first["evidence_sources"] == ["src/api.py:2", "src/api.py:10", "src/service.py:4"]
    assert "src/api.py" in first["hub_nodes"]
    assert report["clusters"][1]["nodes"] == ["config/schedule.yml", "cron schedule"]


def test_cli_cluster_reads_graph_jsonl_and_emits_component_communities(tmp_path: Path):
    edges = tmp_path / "edges.jsonl"
    edges.write_text(
        "\n".join(
            json.dumps(record)
            for record in [
                {"component": "auth/api", "kind": "route", "source": "src/auth/api.py", "target": "POST /login", "confidence": "high", "source_line": 3},
                {"component": "auth/api", "kind": "internal", "source": "src/auth/api.py", "target": "src/auth/service.py", "confidence": "high", "source_line": 1},
                {"component": "reports", "kind": "external", "source": "src/reports.py", "target": "https://warehouse.example", "confidence": "high", "source_line": 9},
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, "-m", "system_mapper.cli", "cluster", str(edges), "--json"],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )

    payload = json.loads(result.stdout)
    assert payload["input_edges"] == 3
    assert payload["cluster_count"] == 2
    assert payload["clusters"][0]["nodes"] == ["POST /login", "src/auth/api.py", "src/auth/service.py"]
    assert payload["clusters"][0]["components"] == ["auth/api"]
    assert payload["clusters"][1]["edge_kinds"] == ["external"]


def test_summary_does_not_treat_python_imports_as_data_stores(tmp_path: Path):
    write(
        tmp_path / "src" / "imports_only.py",
        """
from __future__ import annotations
from pathlib import Path
import json

def main():
    return Path('.')
""".strip(),
    )

    summary = summarize_component(tmp_path, ["src/imports_only.py"], component="imports-only")

    data_store_targets = [edge.target for edge in summary.edges if edge.kind == "data_store"]
    assert data_store_targets == []


def test_summary_emits_internal_edges_for_python_imports_with_repo_targets(tmp_path: Path):
    write(tmp_path / "src" / "billing" / "helpers.py", "def normalize_invoice():\n    return 'ok'\n")
    write(tmp_path / "src" / "billing" / "settings.py", "API_URL = 'https://partner.example'\n")
    write(
        tmp_path / "src" / "billing" / "export.py",
        """
from .helpers import normalize_invoice
import src.billing.settings
import json

def export_invoice():
    return normalize_invoice()
""".strip(),
    )

    summary = summarize_component(tmp_path, ["src/billing/export.py"], component="billing/export")

    internal_edges = {edge.target: edge for edge in summary.edges if edge.kind == "internal"}
    assert "src/billing/helpers.py" in internal_edges
    assert internal_edges["src/billing/helpers.py"].source_line == 1
    assert "src/billing/settings.py" in internal_edges
    assert internal_edges["src/billing/settings.py"].source_line == 2
    assert "json" not in internal_edges


def test_summary_records_evidence_ledger_entries_for_source_line_graph_edges(tmp_path: Path):
    write(tmp_path / "src" / "billing" / "helpers.py", "def normalize_invoice():\n    return 'ok'\n")
    write(
        tmp_path / "src" / "billing" / "api.py",
        """
from .helpers import normalize_invoice

def build_invoice():
    return normalize_invoice()

@router.post("/invoices")
def create_invoice():
    return build_invoice()
""".strip(),
    )

    summary = summarize_component(tmp_path, ["src/billing/api.py"], component="billing/api")
    ledger = {(record.kind, record.source, record.line_start, record.excerpt) for record in summary.evidence_ledger}

    assert ("internal_edge", "src/billing/api.py", 1, "from .helpers import normalize_invoice") in ledger
    assert ("route_edge", "src/billing/api.py", 6, '@router.post("/invoices")') in ledger
    assert ("call_edge", "src/billing/api.py", 8, "return build_invoice()") in ledger


def test_summary_emits_internal_edges_for_python_from_imported_submodules(tmp_path: Path):
    write(tmp_path / "src" / "billing" / "__init__.py", "")
    write(tmp_path / "src" / "billing" / "settings.py", "API_URL = 'https://partner.example'\n")
    write(tmp_path / "src" / "billing" / "helpers.py", "def normalize_invoice():\n    return 'ok'\n")
    write(
        tmp_path / "src" / "billing" / "export.py",
        """
from src.billing import settings
from . import helpers

def export_invoice():
    return helpers.normalize_invoice()
""".strip(),
    )

    summary = summarize_component(tmp_path, ["src/billing/export.py"], component="billing/export")

    internal_targets = {edge.target for edge in summary.edges if edge.kind == "internal"}
    assert "src/billing/settings.py" in internal_targets
    assert "src/billing/helpers.py" in internal_targets
    assert "src/billing/__init__.py" not in internal_targets


def test_summary_uses_python_ast_to_include_async_entry_points(tmp_path: Path):
    write(
        tmp_path / "src" / "worker.py",
        """
async def refresh_system_map():
    return 'updated'

class MapWorker:
    async def run_once(self):
        return await refresh_system_map()
""".strip(),
    )

    summary = summarize_component(tmp_path, ["src/worker.py"], component="worker")

    assert "src/worker.py:refresh_system_map" in summary.entry_points
    assert "src/worker.py:MapWorker" in summary.entry_points
    assert "src/worker.py:run_once" in summary.entry_points
    assert any("refresh_system_map" in ev.symbols for ev in summary.evidence)


def test_summary_emits_python_call_edges_for_local_functions_and_methods(tmp_path: Path):
    write(
        tmp_path / "src" / "mapper.py",
        """
def collect_evidence():
    return []

def build_map():
    evidence = collect_evidence()
    return MapBuilder().merge(evidence)

class MapBuilder:
    def merge(self, evidence):
        return evidence
""".strip(),
    )

    summary = summarize_component(tmp_path, ["src/mapper.py"], component="mapper")

    call_targets = {edge.target for edge in summary.edges if edge.kind == "call"}
    assert "src/mapper.py:collect_evidence" in call_targets
    assert "src/mapper.py:MapBuilder" in call_targets
    assert "src/mapper.py:merge" in call_targets
    assert "return" not in call_targets


def test_summary_emits_route_edges_for_python_web_decorators(tmp_path: Path):
    write(
        tmp_path / "src" / "api.py",
        """
from fastapi import APIRouter

router = APIRouter()

@router.get("/maps/{map_id}")
def read_map(map_id: str):
    return {"map_id": map_id}

@app.post("/maps")
def create_map():
    return {}
""".strip(),
    )

    summary = summarize_component(tmp_path, ["src/api.py"], component="api")

    route_edges = {(edge.target, edge.source_line, edge.confidence) for edge in summary.edges if edge.kind == "route"}
    assert ("GET /maps/{map_id}", 5, "high") in route_edges
    assert ("POST /maps", 9, "high") in route_edges


def test_summary_expands_python_route_methods_keyword_into_interface_edges(tmp_path: Path):
    write(
        tmp_path / "src" / "web.py",
        """
from flask import Flask

app = Flask(__name__)

@app.route("/maps", methods=["GET", "POST"])
def maps():
    return "ok"

@app.route("/health")
def health():
    return "ok"
""".strip(),
    )

    summary = summarize_component(tmp_path, ["src/web.py"], component="web")

    route_edges = {(edge.target, edge.source_line, edge.confidence) for edge in summary.edges if edge.kind == "route"}
    assert ("GET /maps", 5, "high") in route_edges
    assert ("POST /maps", 5, "high") in route_edges
    assert ("GET /health", 9, "high") in route_edges
    assert ("/maps", 5, "high") not in route_edges


def test_summary_uses_javascript_exports_and_arrow_functions_as_entry_points(tmp_path: Path):
    write(
        tmp_path / "src" / "routes" / "app.ts",
        """
export class RouteRegistry {}
export const buildRouteMap = () => ({})
const normalizeRoute = function () {
    return 'ok'
}
let refreshMap = async () => 'fresh'
""".strip(),
    )

    summary = summarize_component(tmp_path, ["src/routes/app.ts"], component="routes/app")

    assert "src/routes/app.ts:RouteRegistry" in summary.entry_points
    assert "src/routes/app.ts:buildRouteMap" in summary.entry_points
    assert "src/routes/app.ts:normalizeRoute" in summary.entry_points
    assert "src/routes/app.ts:refreshMap" in summary.entry_points


def test_summary_emits_javascript_call_edges_for_local_functions_and_constructors(tmp_path: Path):
    write(
        tmp_path / "src" / "routes" / "app.ts",
        """
function collectEvidence() {
    return []
}

const buildMap = () => {
    const evidence = collectEvidence()
    return new MapBuilder().merge(evidence)
}

class MapBuilder {
    merge(evidence) {
        return evidence
    }
}
""".strip(),
    )

    summary = summarize_component(tmp_path, ["src/routes/app.ts"], component="routes/app")

    call_edges = {edge.target: edge for edge in summary.edges if edge.kind == "call"}
    assert call_edges["src/routes/app.ts:collectEvidence"].source_line == 6
    assert call_edges["src/routes/app.ts:MapBuilder"].source_line == 7
    assert call_edges["src/routes/app.ts:merge"].source_line == 7
    assert "src/routes/app.ts:return" not in call_edges


def test_summary_uses_go_declarations_as_entry_points(tmp_path: Path):
    write(
        tmp_path / "cmd" / "mapper" / "main.go",
        """
package main

type MapServer struct {}

func NewMapServer() *MapServer {
    return &MapServer{}
}

func (s *MapServer) Serve() {}

func main() {
    NewMapServer().Serve()
}
""".strip(),
    )

    summary = summarize_component(tmp_path, ["cmd/mapper/main.go"], component="mapper")

    assert "cmd/mapper/main.go:MapServer" in summary.entry_points
    assert "cmd/mapper/main.go:NewMapServer" in summary.entry_points
    assert "cmd/mapper/main.go:Serve" in summary.entry_points
    assert "cmd/mapper/main.go:main" in summary.entry_points


def test_summary_emits_internal_edges_for_go_module_imports(tmp_path: Path):
    write(tmp_path / "go.mod", "module github.com/acme/maps\n")
    write(tmp_path / "internal" / "auth" / "token.go", "package auth\n\nfunc IssueToken() string { return \"token\" }\n")
    write(
        tmp_path / "cmd" / "api" / "main.go",
        """
package main

import (
    "fmt"
    "github.com/acme/maps/internal/auth"
)

func main() {
    fmt.Println(auth.IssueToken())
}
""".strip(),
    )

    summary = summarize_component(tmp_path, ["cmd/api/main.go"], component="api")

    internal_edges = {edge.target: edge for edge in summary.edges if edge.kind == "internal"}
    assert "internal/auth/token.go" in internal_edges
    assert internal_edges["internal/auth/token.go"].source_line == 5
    assert "fmt" not in internal_edges


def test_summary_emits_go_call_edges_only_for_same_file_calls(tmp_path: Path):
    write(
        tmp_path / "cmd" / "api" / "main.go",
        """
package main

import "fmt"

type Server struct{}

func NewServer() *Server {
    return &Server{}
}

func (s *Server) Serve() {
    fmt.Println("ready")
}

func main() {
    NewServer().Serve()
}
""".strip(),
    )

    summary = summarize_component(tmp_path, ["cmd/api/main.go"], component="api")

    call_targets = {edge.target for edge in summary.edges if edge.kind == "call"}
    assert call_targets == {"cmd/api/main.go:NewServer", "cmd/api/main.go:Serve"}


def test_summary_emits_php_symbols_calls_routes_and_internal_edges(tmp_path: Path):
    write(tmp_path / "src" / "Auth" / "Token.php", "<?php\nfunction issueToken() { return 'token'; }\n")
    write(
        tmp_path / "src" / "login.php",
        """
<?php
require_once __DIR__ . '/Auth/Token.php';

class LoginController {
    public function submitLogin() {
        Route::post('/login', [$this, 'submitLogin']);
        return issueToken();
    }
}

function normalizeEmail($email) {
    return strtolower($email);
}

normalizeEmail('USER@example.com');
""".strip(),
    )

    summary = summarize_component(tmp_path, ["src/login.php"], component="login")

    assert "src/login.php:LoginController" in summary.entry_points
    assert "src/login.php:submitLogin" in summary.entry_points
    assert "src/login.php:normalizeEmail" in summary.entry_points
    edges = {(edge.kind, edge.target, edge.source_line) for edge in summary.edges}
    assert ("internal", "src/Auth/Token.php", 2) in edges
    assert ("route", "POST /login", 6) in edges
    assert ("call", "src/login.php:issueToken", 7) in edges
    assert ("call", "src/login.php:normalizeEmail", 15) in edges


def test_summary_does_not_emit_javascript_call_edges_for_method_declarations(tmp_path: Path):
    write(
        tmp_path / "src" / "routes" / "builder.ts",
        """
class MapBuilder {
    merge(evidence) {
        return evidence
    }
}

const buildMap = () => new MapBuilder()
""".strip(),
    )

    summary = summarize_component(tmp_path, ["src/routes/builder.ts"], component="routes/builder")

    call_targets = {edge.target for edge in summary.edges if edge.kind == "call"}
    assert call_targets == {"src/routes/builder.ts:MapBuilder"}


def test_summary_emits_route_edges_for_javascript_express_style_handlers(tmp_path: Path):
    write(
        tmp_path / "src" / "routes" / "maps.ts",
        """
import express from 'express'

const router = express.Router()

router.get('/maps/:mapId', loadMap)
app.post("/maps", createMap)
router.route('/maps/:mapId').delete(deleteMap)
""".strip(),
    )

    summary = summarize_component(tmp_path, ["src/routes/maps.ts"], component="routes/maps")

    route_edges = {(edge.target, edge.source_line, edge.confidence) for edge in summary.edges if edge.kind == "route"}
    assert ("GET /maps/:mapId", 5, "medium") in route_edges
    assert ("POST /maps", 6, "medium") in route_edges
    assert ("DELETE /maps/:mapId", 7, "medium") in route_edges


def test_summary_emits_internal_edges_for_javascript_and_typescript_relative_imports(tmp_path: Path):
    write(tmp_path / "src" / "routes" / "helpers.ts", "export function normalizeRoute() { return 'ok' }\n")
    write(tmp_path / "src" / "routes" / "shared" / "index.ts", "export const shared = true\n")
    write(tmp_path / "src" / "routes" / "legacy.js", "module.exports = {}\n")
    write(
        tmp_path / "src" / "routes" / "app.ts",
        """
import { normalizeRoute } from './helpers'
export { shared } from './shared'
const legacy = require('./legacy')
import express from 'express'

export async function handler() {
    return normalizeRoute()
}
""".strip(),
    )

    summary = summarize_component(tmp_path, ["src/routes/app.ts"], component="routes/app")

    internal_edges = {edge.target: edge for edge in summary.edges if edge.kind == "internal"}
    assert "src/routes/helpers.ts" in internal_edges
    assert internal_edges["src/routes/helpers.ts"].source_line == 1
    assert "src/routes/shared/index.ts" in internal_edges
    assert internal_edges["src/routes/shared/index.ts"].source_line == 2
    assert "src/routes/legacy.js" in internal_edges
    assert internal_edges["src/routes/legacy.js"].source_line == 3
    assert "express" not in internal_edges


def test_cli_prompt_update_mentions_living_system_changes():
    result = subprocess.run(
        [sys.executable, "-m", "system_mapper.cli", "prompt", "update", "--component", "billing/export"],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )

    assert "This is a living system" in result.stdout
    assert "Identify affected components" in result.stdout
    assert "stale" in result.stdout.lower()


def test_work_packet_packages_summary_prompt_edges_and_next_actions(tmp_path: Path):
    write(
        tmp_path / "src" / "billing.py",
        """
import requests
DATABASE_TABLE = "invoices"

def export_invoice(invoice_id):
    requests.post("https://partner.example/export", json={"invoice_id": invoice_id})
""".strip(),
    )
    write(tmp_path / "docs" / "billing.md", "# Billing\nInvoice exports may need manual retry.\n")

    packet = build_work_packet(tmp_path, ["src/billing.py", "docs/billing.md"], component="billing/export")

    assert packet["component"] == "billing/export"
    assert packet["contract"] == "system-mapper.work-packet.v1"
    assert packet["scope"] == ["src/billing.py", "docs/billing.md"]
    assert "Analyse only the files" in packet["prompt"]
    assert any(edge["kind"] == "external" and "partner.example" in edge["target"] for edge in packet["edge_records"])
    assert any("Operational process" in unknown for unknown in packet["unknowns"])
    assert packet["next_actions"][0].startswith("Inspect or answer unknown")


def test_cli_packet_emits_bounded_low_context_json_contract(tmp_path: Path):
    write(tmp_path / "src" / "app.py", "def main():\n    return 'ok'\n")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "system_mapper.cli",
            "packet",
            str(tmp_path),
            "src/app.py",
            "--component",
            "app",
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )

    packet = json.loads(result.stdout)
    assert packet["contract"] == "system-mapper.work-packet.v1"
    assert packet["component"] == "app"
    assert packet["summary"]["scope"] == ["src/app.py"]
    assert "Target component: app" in packet["prompt"]


def test_slice_plan_defaults_to_45000_tokens_breadth_first_and_two_level_outputs(tmp_path: Path):
    write(tmp_path / "README.md", "# Repo\n")
    write(tmp_path / "src" / "system_mapper" / "app.py", "def main():\n    return 'ok'\n")
    write(tmp_path / "src" / "z_deep" / "module.py", "def z():\n    return 'z'\n")

    plan = build_slice_plan(tmp_path)

    assert plan.token_limit == DEFAULT_TOKEN_LIMIT == 45_000
    assert plan.strategy == "breadth-first"
    assert plan.output_layout == "2-level"
    assert plan.slices[0].paths[0] == "README.md"
    assert all(slice_.estimated_tokens <= 45_000 for slice_ in plan.slices)
    assert "packets" in plan.slices[0].output_locations["packet"]
    assert "components" in plan.slices[0].output_locations["summary"]
    assert "edges" in plan.slices[0].output_locations["edges"]


def test_slice_plan_splits_when_token_limit_is_exceeded(tmp_path: Path):
    write(tmp_path / "src" / "a.py", "a" * 24)
    write(tmp_path / "src" / "b.py", "b" * 24)

    plan = build_slice_plan(tmp_path, token_limit=6, output_layout="flat")

    assert [slice_.paths for slice_ in plan.slices] == [["src/a.py"], ["src/b.py"]]
    assert all(slice_.estimated_tokens == 6 for slice_ in plan.slices)
    assert plan.slices[0].output_locations["packet"] == ".system-map/packets/src-a.json"


def test_slice_plan_dependency_aware_prioritizes_edge_rich_files(tmp_path: Path):
    write(tmp_path / "src" / "leaf.py", "def helper():\n    return 'ok'\n")
    write(
        tmp_path / "src" / "orchestrator.py",
        """
import src.leaf
DATABASE_TABLE = "system_maps"

def run_mapping():
    return src.leaf.helper()
""".strip(),
    )
    write(tmp_path / "docs" / "overview.md", "# Overview\nManual review may be required.\n")

    plan = build_slice_plan(tmp_path, strategy="dependency-aware", token_limit=12, output_layout="flat")

    assert plan.strategy == "dependency-aware"
    assert plan.slices[0].paths == ["src/orchestrator.py"]
    assert "edge_count=2" in plan.slices[0].rationale
    assert "internal" in plan.slices[0].rationale
    assert "data_store" in plan.slices[0].rationale


def test_slice_plan_orders_php_first_among_c_like_languages(tmp_path: Path):
    write(tmp_path / "src" / "login.php", "<?php\nfunction login() {}\n")
    write(tmp_path / "src" / "auth.c", "void auth() {}\n")
    write(tmp_path / "src" / "Auth.java", "class Auth {}\n")
    write(tmp_path / "src" / "server.go", "package main\nfunc main() {}\n")

    plan = build_slice_plan(tmp_path, token_limit=1, output_layout="flat")

    assert [slice_.paths[0] for slice_ in plan.slices][:4] == [
        "src/login.php",
        "src/auth.c",
        "src/Auth.java",
        "src/server.go",
    ]


def test_run_next_slice_writes_artifacts_and_then_noops_when_all_slices_exist(tmp_path: Path):
    write(tmp_path / "src" / "login.php", "<?php\nfunction login() {}\n")

    first = run_next_slice(tmp_path, token_limit=45_000, output_layout="flat")

    assert first["outcome"] == "advanced"
    assert first["slice"]["paths"] == ["src/login.php"]
    assert (tmp_path / ".system-map" / "components" / "src-login.json").exists()
    assert (tmp_path / ".system-map" / "edges" / "src-login.jsonl").exists()
    assert (tmp_path / ".system-map" / "packets" / "src-login.json").exists()

    second = run_next_slice(tmp_path, token_limit=45_000, output_layout="flat")
    assert second["outcome"] == "no_change"
    assert second["reason"] == "all planned slices already have packet, summary, and edge artifacts"


def test_cli_plan_emits_json_with_strategy_and_output_layout(tmp_path: Path):
    write(tmp_path / "src" / "app.py", "def main():\n    return 'ok'\n")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "system_mapper.cli",
            "plan",
            str(tmp_path),
            "--token-limit",
            "45000",
            "--strategy",
            "depth-first",
            "--output-layout",
            "1-level",
            "--json",
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )

    payload = json.loads(result.stdout)
    assert payload["token_limit"] == 45_000
    assert payload["strategy"] == "depth-first"
    assert payload["output_layout"] == "1-level"
    assert payload["slices"][0]["paths"] == ["src/app.py"]
    assert payload["slices"][0]["output_locations"]["packet"].endswith(".json")


def test_summary_builds_durable_claims_with_evidence_ledger_refs(tmp_path: Path):
    write(
        tmp_path / "src" / "billing.py",
        """
INVOICE_TABLE = "invoices"
PARTNER_URL = "https://partner.example/export"

# Owner: Billing Ops
# Business rule: invoices must be approved before export.
def export_invoice(invoice_id):
    return PARTNER_URL
""".strip(),
    )
    write(
        tmp_path / "ops" / "billing.cron",
        "0 2 * * * python -m billing.export\n# Manual retry requires operator approval.\n",
    )

    summary = summarize_component(tmp_path, ["src/billing.py", "ops/billing.cron"], component="billing/export")
    payload = summary.to_dict()

    ledger_by_id = {record["id"]: record for record in payload["evidence_ledger"]}
    claims = payload["claims"]
    claim_types = {claim["type"] for claim in claims}

    assert {"purpose", "data_contract", "trigger", "business_rule", "owner", "risk", "unknown"} <= claim_types
    assert any(claim["type"] == "trigger" and "cron" in claim["text"] for claim in claims)
    assert any(claim["type"] == "data_contract" and "invoices" in claim["text"] for claim in claims)
    assert any(claim["type"] == "owner" and "Billing Ops" in claim["text"] for claim in claims)
    assert all(claim["evidence_refs"] for claim in claims)
    assert all(ref in ledger_by_id for claim in claims for ref in claim["evidence_refs"])
    assert all(record["source"] and record["line_start"] >= 1 and record["line_end"] >= record["line_start"] for record in ledger_by_id.values())
    assert all(record["freshness"].startswith("sha256:") for record in ledger_by_id.values())


def test_packet_exposes_claim_records_and_evidence_ledger_for_low_context_workers(tmp_path: Path):
    write(tmp_path / "src" / "billing.py", 'INVOICE_TABLE = "invoices"\ndef export_invoice():\n    return "ok"\n')

    packet = build_work_packet(tmp_path, ["src/billing.py"], component="billing/export")

    assert packet["contract"] == "system-mapper.work-packet.v1"
    assert packet["claim_records"] == packet["summary"]["claims"]
    assert packet["evidence_ledger"] == packet["summary"]["evidence_ledger"]
    assert packet["next_actions"][0].startswith("Inspect or answer unknown") or "claim" in packet["next_actions"][0].lower()


def test_update_marks_claims_stale_when_their_evidence_sources_change(tmp_path: Path):
    write(tmp_path / "src" / "billing.py", 'INVOICE_TABLE = "invoices"\ndef export_invoice():\n    return "ok"\n')
    previous = summarize_component(tmp_path, ["src/billing.py"], component="billing/export").to_dict()
    data_contract_claim = next(claim for claim in previous["claims"] if claim["type"] == "data_contract")
    diff = """
diff --git a/src/billing.py b/src/billing.py
--- a/src/billing.py
+++ b/src/billing.py
@@ -1,2 +1,2 @@
-INVOICE_TABLE = "invoices"
+INVOICE_TABLE = "archived_invoices"
 def export_invoice():
""".strip()

    update = update_summary_from_diff(previous, diff).to_dict()

    assert any(stale["claim_id"] == data_contract_claim["id"] for stale in update["stale_claims"])
    assert any("src/billing.py" in stale["reason"] for stale in update["stale_claims"])
    assert "Heuristic diff analysis cannot prove runtime behaviour; re-run component slice summaries for changed files" in update["unknowns"]


def test_merge_preserves_claims_evidence_and_conflicts_for_upward_summaries(tmp_path: Path):
    from system_mapper.merge import merge_component_summaries

    write(tmp_path / "billing" / "export.py", '# Owner: Billing Ops\ndef export_invoice():\n    return "ok"\n')
    write(tmp_path / "billing" / "docs.md", "Owner: Finance Ops\nManual retry requires approval.\n")
    code_summary = summarize_component(tmp_path, ["billing/export.py"], component="billing/code").to_dict()
    doc_summary = summarize_component(tmp_path, ["billing/docs.md"], component="billing/docs").to_dict()

    merged = merge_component_summaries([code_summary, doc_summary], component="billing").to_dict()

    assert merged["component"] == "billing"
    assert set(merged["scope"]) == {"billing/code", "billing/docs"}
    assert len(merged["claims"]) >= len(code_summary["claims"]) + len(doc_summary["claims"])
    assert {record["id"] for record in code_summary["evidence_ledger"]} <= {record["id"] for record in merged["evidence_ledger"]}
    assert any("owner" in conflict.lower() and "Billing Ops" in conflict and "Finance Ops" in conflict for conflict in merged["conflicts"])


def test_cli_merge_combines_summary_files_as_json(tmp_path: Path):
    write(tmp_path / "src" / "app.py", "def main():\n    return 'ok'\n")
    write(tmp_path / "docs" / "app.md", "Manual operation requires review.\n")
    code_summary = summarize_component(tmp_path, ["src/app.py"], component="app/code").to_dict()
    doc_summary = summarize_component(tmp_path, ["docs/app.md"], component="app/docs").to_dict()
    code_path = tmp_path / "code-summary.json"
    doc_path = tmp_path / "doc-summary.json"
    code_path.write_text(json.dumps(code_summary), encoding="utf-8")
    doc_path.write_text(json.dumps(doc_summary), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "system_mapper.cli",
            "merge",
            str(code_path),
            str(doc_path),
            "--component",
            "app",
            "--json",
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )

    merged = json.loads(result.stdout)
    assert merged["component"] == "app"
    assert merged["scope"] == ["app/code", "app/docs"]
    assert merged["claims"]
    assert merged["evidence_ledger"]
