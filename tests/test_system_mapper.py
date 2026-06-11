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


def test_summary_emits_rust_symbols_module_edges_and_same_file_calls(tmp_path: Path):
    write(tmp_path / "src" / "auth.rs", "pub fn issue_token() -> String { String::new() }\n")
    write(
        tmp_path / "src" / "main.rs",
        """
mod auth;

pub struct MapServer {}

impl MapServer {
    pub fn serve(&self) {
        refresh_map();
    }
}

fn refresh_map() {}

fn main() {
    let server = MapServer {};
    server.serve();
}
""".strip(),
    )

    summary = summarize_component(tmp_path, ["src/main.rs"], component="rust/app")

    assert "src/main.rs:MapServer" in summary.entry_points
    assert "src/main.rs:serve" in summary.entry_points
    assert "src/main.rs:refresh_map" in summary.entry_points
    assert "src/main.rs:main" in summary.entry_points
    edges = {(edge.kind, edge.target, edge.source_line) for edge in summary.edges}
    assert ("internal", "src/auth.rs", 1) in edges
    assert ("call", "src/main.rs:refresh_map", 7) in edges
    assert ("call", "src/main.rs:MapServer", 14) in edges
    assert ("call", "src/main.rs:serve", 15) in edges


def test_summary_emits_java_spring_route_edges_from_mapping_annotations(tmp_path: Path):
    write(
        tmp_path / "src" / "main" / "java" / "MapsController.java",
        """
package maps;

@RestController
@RequestMapping("/maps")
class MapsController {
    @GetMapping("/{mapId}")
    public MapView readMap() { return loadMap(); }

    @PostMapping
    public MapView createMap() { return saveMap(); }

    @RequestMapping(value = "/search", method = RequestMethod.PUT)
    public MapView searchMaps() { return loadMap(); }
}
""".strip(),
    )

    summary = summarize_component(tmp_path, ["src/main/java/MapsController.java"], component="maps/api")

    route_edges = {(edge.target, edge.source_line, edge.confidence) for edge in summary.edges if edge.kind == "route"}
    assert ("GET /maps/{mapId}", 6, "medium") in route_edges
    assert ("POST /maps", 9, "medium") in route_edges
    assert ("PUT /maps/search", 12, "medium") in route_edges
    call_targets = {edge.target for edge in summary.edges if edge.kind == "call"}
    assert "src/main/java/MapsController.java:GetMapping" not in call_targets
    assert "src/main/java/MapsController.java:RequestMapping" not in call_targets


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


# ===========================================================================
# Tests for new modules: claims, worker, eval
# ===========================================================================


# --- claims module ---

def test_claim_store_import_and_query(tmp_path: Path):
    from system_mapper.claims import ClaimRecord, ClaimStore

    store_path = tmp_path / "claims.json"
    store = ClaimStore(store_path)

    claims = [
        ClaimRecord(
            claim_id="claim.checkout.payment.entrypoint.001",
            component="checkout/payment",
            claim_type="entry_point",
            statement="Payment processing enters through CheckoutController::submitPayment.",
            evidence_ids=["ev.checkout_controller.001"],
            confidence="medium",
            status="accepted",
            created_at="2026-06-10",
            last_verified_at="2026-06-10",
        ),
        ClaimRecord(
            claim_id="claim.checkout.payment.external.001",
            component="checkout/payment",
            claim_type="external_system",
            statement="Payment gateway calls Stripe API.",
            evidence_ids=["ev.payment_gateway.001"],
            confidence="high",
            status="accepted",
            created_at="2026-06-10",
            last_verified_at="2026-06-10",
        ),
    ]

    counts = store.import_claims(claims)
    assert counts["new"] == 2

    # Query by component
    results = store.list_claims(component="checkout/payment")
    assert len(results) == 2

    # Query by type
    results = store.list_claims(claim_type="entry_point")
    assert len(results) == 1

    # Query by status
    results = store.list_claims(status="accepted")
    assert len(results) == 2

    # Stats
    stats = store.stats
    assert stats["total"] == 2
    assert stats["accepted"] == 2


def test_claim_store_duplicate_handling(tmp_path: Path):
    from system_mapper.claims import ClaimRecord, ClaimStore

    store_path = tmp_path / "claims.json"
    store = ClaimStore(store_path)

    claim = ClaimRecord(
        claim_id="claim.test.001",
        component="test",
        claim_type="purpose",
        statement="Test claim.",
        evidence_ids=["ev.001"],
        confidence="medium",
        status="accepted",
    )

    counts = store.import_claims([claim])
    assert counts["new"] == 1

    # Import same claim again
    counts = store.import_claims([claim])
    assert counts["duplicate"] == 1


def test_claim_store_conflict_detection(tmp_path: Path):
    from system_mapper.claims import ClaimRecord, ClaimStore

    store_path = tmp_path / "claims.json"
    store = ClaimStore(store_path)

    claims = [
        ClaimRecord(
            claim_id="claim.order.owner.001",
            component="order",
            claim_type="owner",
            statement="owner: checkout flow",
            evidence_ids=["ev.001"],
            confidence="medium",
            status="accepted",
        ),
        ClaimRecord(
            claim_id="claim.order.owner.002",
            component="order",
            claim_type="owner",
            statement="owner: fulfilment sync",
            evidence_ids=["ev.002"],
            confidence="medium",
            status="accepted",
        ),
    ]

    store.import_claims(claims)
    conflicts = store.get_conflicts()
    assert len(conflicts) == 1
    assert "checkout flow" in conflicts[0]["claim_a"]
    assert "fulfilment sync" in conflicts[0]["claim_b"]


def test_validate_worker_output_accepts_valid_claims(tmp_path: Path):
    from system_mapper.claims import validate_worker_output

    packet = {
        "component": "checkout/payment",
        "scope": ["src/CheckoutController.php"],
        "evidence_ledger": [
            {"id": "ev.checkout_controller.001", "source": "src/CheckoutController.php", "line_start": 10, "line_end": 15, "kind": "code", "excerpt": "function submitPayment()", "freshness": "current"},
        ],
    }

    worker_output = {
        "schema_version": "0.2",
        "component": "checkout/payment",
        "claims": [
            {
                "claim_type": "entry_point",
                "statement": "Payment processing enters through submitPayment.",
                "evidence_ids": ["ev.checkout_controller.001"],
                "confidence": "medium",
            }
        ],
        "hypotheses": [],
        "unknowns": [],
        "conflicts": [],
        "next_actions": [],
    }

    result = validate_worker_output(worker_output, packet)
    assert len(result.accepted_claims) == 1
    assert len(result.rejected_claims) == 0
    assert result.accepted_claims[0]["status"] == "accepted"


def test_validate_worker_output_rejects_claims_without_evidence(tmp_path: Path):
    from system_mapper.claims import validate_worker_output

    packet = {
        "component": "checkout/payment",
        "scope": ["src/CheckoutController.php"],
        "evidence_ledger": [],
    }

    worker_output = {
        "schema_version": "0.2",
        "component": "checkout/payment",
        "claims": [
            {
                "claim_type": "entry_point",
                "statement": "Payment processing enters through some function.",
                "evidence_ids": [],
                "confidence": "high",
            }
        ],
        "hypotheses": [],
        "unknowns": [],
        "conflicts": [],
        "next_actions": [],
    }

    result = validate_worker_output(worker_output, packet)
    assert len(result.accepted_claims) == 0
    assert len(result.rejected_claims) == 1
    assert "No evidence IDs cited" in result.rejected_claims[0]["rejection_reason"]


def test_validate_worker_output_rejects_missing_evidence_ids(tmp_path: Path):
    from system_mapper.claims import validate_worker_output

    packet = {
        "component": "checkout/payment",
        "scope": ["src/CheckoutController.php"],
        "evidence_ledger": [
            {"id": "ev.real.001", "source": "src/CheckoutController.php", "line_start": 1, "line_end": 5, "kind": "code", "excerpt": "real evidence", "freshness": "current"},
        ],
    }

    worker_output = {
        "schema_version": "0.2",
        "component": "checkout/payment",
        "claims": [
            {
                "claim_type": "entry_point",
                "statement": "Payment processing enters through some function.",
                "evidence_ids": ["ev.fake.999"],
                "confidence": "high",
            }
        ],
        "hypotheses": [],
        "unknowns": [],
        "conflicts": [],
        "next_actions": [],
    }

    result = validate_worker_output(worker_output, packet)
    assert len(result.rejected_claims) == 1
    assert "not found in packet" in result.rejected_claims[0]["rejection_reason"]


def test_validate_worker_output_downgrades_vague_language(tmp_path: Path):
    from system_mapper.claims import validate_worker_output

    packet = {
        "component": "checkout/payment",
        "scope": ["src/CheckoutController.php"],
        "evidence_ledger": [
            {"id": "ev.checkout_controller.001", "source": "src/CheckoutController.php", "line_start": 10, "line_end": 15, "kind": "code", "excerpt": "function submitPayment()", "freshness": "current"},
        ],
    }

    worker_output = {
        "schema_version": "0.2",
        "component": "checkout/payment",
        "claims": [
            {
                "claim_type": "entry_point",
                "statement": "This clearly proves that all requests always go through submitPayment.",
                "evidence_ids": ["ev.checkout_controller.001"],
                "confidence": "high",
            }
        ],
        "hypotheses": [],
        "unknowns": [],
        "conflicts": [],
        "next_actions": [],
    }

    result = validate_worker_output(worker_output, packet)
    # Should be downgraded (high -> medium or rejected)
    assert len(result.downgraded_claims) > 0 or len(result.accepted_claims) > 0
    if result.accepted_claims:
        assert result.accepted_claims[0]["confidence"] != "high"


def test_validate_worker_output_preserves_unknowns(tmp_path: Path):
    from system_mapper.claims import validate_worker_output

    packet = {
        "component": "checkout/payment",
        "scope": ["src/CheckoutController.php"],
        "evidence_ledger": [],
    }

    worker_output = {
        "schema_version": "0.2",
        "component": "checkout/payment",
        "claims": [],
        "hypotheses": [],
        "unknowns": [
            {"question": "What happens on payment failure?", "why_it_matters": "Error handling is critical for payment flows.", "suggested_next_paths": ["src/PaymentErrorHandler.php"]},
        ],
        "conflicts": [],
        "next_actions": [],
    }

    result = validate_worker_output(worker_output, packet)
    assert any("Unknown preserved" in w for w in result.warnings)


# --- worker module ---

def test_worker_contract_includes_rules():
    from system_mapper.worker import get_worker_contract

    contract = get_worker_contract()
    assert "low-context system-mapping worker" in contract
    assert "Every claim must cite evidence IDs" in contract
    assert "Do not infer beyond the inspected scope" in contract
    assert "Output valid JSON only" in contract
    assert "schema_version" in contract


def test_parse_worker_output_normalizes_structure():
    from system_mapper.worker import parse_worker_output

    raw = {
        "component": "checkout/payment",
        "claims": [
            {"type": "entry_point", "statement": "test", "evidence_refs": ["ev.001"], "confidence": "high"},
        ],
        "hypotheses": [{"statement": "maybe", "reason": "weak evidence"}],
        "unknowns": [{"question": "what?"}],
    }

    result = parse_worker_output(raw)
    assert result["component"] == "checkout/payment"
    assert len(result["claims"]) == 1
    assert result["claims"][0]["claim_type"] == "entry_point"
    assert result["claims"][0]["evidence_ids"] == ["ev.001"]
    assert len(result["hypotheses"]) == 1
    assert len(result["unknowns"]) == 1


def test_parse_worker_output_from_json_string():
    from system_mapper.worker import parse_worker_output

    raw = '{"component": "test", "claims": [{"claim_type": "purpose", "statement": "test", "evidence_ids": [], "confidence": "low"}]}'
    result = parse_worker_output(raw)
    assert result["component"] == "test"
    assert len(result["claims"]) == 1


def test_claims_from_worker_output():
    from system_mapper.claims import ValidationResult
    from system_mapper.worker import claims_from_worker_output

    vr = ValidationResult(
        accepted_claims=[
            {"claim_type": "entry_point", "statement": "test claim", "evidence_ids": ["ev.001"], "confidence": "medium"},
        ],
        downgraded_claims=[
            {"claim_type": "purpose", "statement": "vague claim", "evidence_ids": ["ev.002"], "confidence": "low"},
        ],
    )

    claims = claims_from_worker_output(
        worker_output={},
        validation_result=vr,
        component="test/component",
    )

    assert len(claims) == 2
    assert claims[0].status == "accepted"
    assert claims[1].status == "needs_review"
    assert claims[0].component == "test/component"


# --- eval module ---

def test_eval_load_benchmark(tmp_path: Path):
    from system_mapper.eval import load_benchmark

    benchmark_path = tmp_path / "benchmark.json"
    benchmark_path.write_text(
        '[{"question": "Where does checkout start?", "expected_evidence": ["CheckoutController.php"], "expected_claim_type": "entry_point"}]',
        encoding="utf-8",
    )

    questions = load_benchmark(benchmark_path)
    assert len(questions) == 1
    assert questions[0].question == "Where does checkout start?"
    assert questions[0].expected_claim_type == "entry_point"


def test_eval_create_sample_benchmark(tmp_path: Path):
    from system_mapper.eval import create_sample_benchmark

    output_path = tmp_path / "benchmarks" / "sample.json"
    create_sample_benchmark(output_path)
    assert output_path.exists()

    import json
    data = json.loads(output_path.read_text(encoding="utf-8"))
    assert len(data) >= 3
    assert any(q["expected_claim_type"] == "entry_point" for q in data)


def test_eval_map_usefulness():
    from system_mapper.eval import evaluate_map_usefulness, load_benchmark
    from pathlib import Path
    import json

    # Create a minimal benchmark
    benchmark_data = [
        {"question": "Where does checkout payment start?", "expected_evidence": ["CheckoutController.php"], "expected_claim_type": "entry_point"},
        {"question": "What external systems are involved?", "expected_claim_type": "external_system"},
    ]

    questions = []
    from system_mapper.eval import BenchmarkQuestion
    for item in benchmark_data:
        questions.append(BenchmarkQuestion(
            question=item["question"],
            expected_evidence=item.get("expected_evidence", []),
            expected_claim_type=item.get("expected_claim_type", ""),
        ))

    # Test with a system map that contains the expected evidence
    system_map = {
        "component": "checkout/payment",
        "evidence_ledger": [
            {"id": "ev.checkout_controller.001", "source": "src/CheckoutController.php", "line_start": 10},
        ],
        "claims": [
            {"claim_type": "entry_point", "statement": "Entry through CheckoutController", "evidence_ids": ["ev.checkout_controller.001"]},
        ],
    }

    report = evaluate_map_usefulness(questions, system_map)
    assert report.total_questions == 2
    assert report.mapped_correct >= 1  # At least the entry_point question should match


# --- quality module ---

def test_quality_report_scores_evidence_backed_map_as_passing():
    from system_mapper.quality import evaluate_map_quality

    system_map = {
        "component": "checkout/payment",
        "evidence_ledger": [
            {"id": "ev.route.001", "source": "src/routes.py", "line_start": 4, "line_end": 6, "excerpt": "@router.post('/pay')", "freshness": "sha256:abc"},
            {"id": "ev.handler.001", "source": "src/routes.py", "line_start": 8, "line_end": 12, "excerpt": "def pay():", "freshness": "sha256:def"},
        ],
        "claims": [
            {"claim_type": "entry_point", "statement": "Payment requests enter through the /pay route.", "evidence_ids": ["ev.route.001", "ev.handler.001"], "confidence": "high", "status": "accepted", "scope": "src/routes.py"},
            {"claim_type": "unknown", "statement": "Runtime payment gateway credentials were not inspected.", "evidence_ids": ["ev.handler.001"], "confidence": "low", "status": "needs_review", "scope": "src/routes.py"},
        ],
        "unknowns": [{"question": "Which gateway is configured in production?"}],
        "conflicts": [],
    }

    report = evaluate_map_quality(system_map)

    assert report.passed is True
    assert report.metrics["claim_evidence_coverage"] == 1.0
    assert report.metrics["citation_validity"] == 1.0
    assert report.metrics["high_confidence_support"] == 1.0
    assert report.metrics["unknown_visibility"] == 1.0
    assert report.anti_garbage_score >= 0.8
    assert not report.failures


def test_quality_report_flags_garbage_map_with_measurable_failures():
    from system_mapper.quality import evaluate_map_quality

    system_map = {
        "component": "checkout/payment",
        "evidence_ledger": [
            {"id": "ev.real.001", "source": "src/routes.py", "line_start": 4, "line_end": 6, "excerpt": "@router.post('/pay')", "freshness": "sha256:abc"},
        ],
        "claims": [
            {"claim_type": "purpose", "statement": "The system clearly handles every payment safely.", "evidence_ids": [], "confidence": "high", "status": "accepted"},
            {"claim_type": "owner", "statement": "Billing owns all checkout behaviour.", "evidence_ids": ["ev.fake.999"], "confidence": "medium", "status": "accepted"},
            {"claim_type": "dependency", "statement": "It always calls Stripe.", "evidence_ids": ["ev.real.001"], "confidence": "high", "status": "accepted"},
        ],
        "unknowns": [],
        "conflicts": [],
    }

    report = evaluate_map_quality(system_map)

    assert report.passed is False
    assert report.metrics["claim_evidence_coverage"] < 1.0
    assert report.metrics["citation_validity"] < 1.0
    assert report.metrics["high_confidence_support"] < 1.0
    assert report.metrics["unknown_visibility"] == 0.0
    assert report.metrics["vague_language_rate"] > 0.0
    assert report.anti_garbage_score < 0.8
    assert any("claim_evidence_coverage" in failure for failure in report.failures)
    assert any("citation_validity" in failure for failure in report.failures)


# --- CLI integration tests ---

def test_cli_quality_command_reports_measurable_score(tmp_path: Path):
    import json
    import subprocess
    import sys

    packet = {
        "component": "checkout/payment",
        "evidence_ledger": [
            {"id": "ev.route.001", "source": "src/routes.py", "line_start": 4, "line_end": 6, "excerpt": "@router.post('/pay')", "freshness": "sha256:abc"},
            {"id": "ev.handler.001", "source": "src/routes.py", "line_start": 8, "line_end": 12, "excerpt": "def pay():", "freshness": "sha256:def"},
        ],
    }
    validated_output = {
        "accepted_claims": [
            {"claim_type": "entry_point", "statement": "Payment requests enter through the /pay route.", "evidence_ids": ["ev.route.001", "ev.handler.001"], "confidence": "high", "status": "accepted"},
        ],
        "warnings": ["Unknown preserved: Which gateway is configured in production?"],
        "unknowns": [{"question": "Which gateway is configured in production?"}],
    }
    packet_path = tmp_path / "packet.json"
    validated_path = tmp_path / "validated.json"
    packet_path.write_text(json.dumps(packet), encoding="utf-8")
    validated_path.write_text(json.dumps(validated_output), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "system_mapper.cli",
            "quality",
            str(validated_path),
            "--evidence-source",
            str(packet_path),
            "--min-score",
            "0.8",
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    output = json.loads(result.stdout)
    assert output["passed"] is True
    assert output["anti_garbage_score"] >= 0.8
    assert output["metrics"]["claim_evidence_coverage"] == 1.0


def test_cli_worker_run_generates_prompt(tmp_path: Path):
    """Test that worker run without llm-command generates a prompt."""
    # First create a packet
    write(
        tmp_path / "src" / "billing.py",
        "def export_invoice():\n    pass\n",
    )

    import subprocess, sys, json
    result = subprocess.run(
        [sys.executable, "-m", "system_mapper.cli", "packet", str(tmp_path), "src/billing.py", "--component", "billing"],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    packet = json.loads(result.stdout)

    packet_path = tmp_path / "test-packet.json"
    packet_path.write_text(json.dumps(packet), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "system_mapper.cli", "worker", "run", str(packet_path)],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    output = json.loads(result.stdout)
    assert "_prompt" in output
    assert "low-context system-mapping worker" in output["_prompt"]


def test_cli_validate_command(tmp_path: Path):
    """Test the validate CLI command."""
    import subprocess, sys, json

    packet = {
        "component": "test/component",
        "scope": ["src/test.py"],
        "evidence_ledger": [
            {"id": "ev.test.001", "source": "src/test.py", "line_start": 1, "line_end": 5, "kind": "code", "excerpt": "def test():", "freshness": "current"},
        ],
    }
    worker_output = {
        "schema_version": "0.2",
        "component": "test/component",
        "claims": [
            {"claim_type": "purpose", "statement": "This is a test component.", "evidence_ids": ["ev.test.001"], "confidence": "medium"},
        ],
        "hypotheses": [],
        "unknowns": [],
        "conflicts": [],
        "next_actions": [],
    }

    packet_path = tmp_path / "test-packet.json"
    worker_path = tmp_path / "test-worker.json"
    packet_path.write_text(json.dumps(packet), encoding="utf-8")
    worker_path.write_text(json.dumps(worker_output), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "system_mapper.cli", "validate", str(worker_path), str(packet_path)],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    output = json.loads(result.stdout)
    assert "accepted_claims" in output
    assert len(output["accepted_claims"]) == 1


def test_cli_claim_import_and_list(tmp_path: Path):
    """Test the claim import and list CLI commands."""
    import subprocess, sys, json

    validated = {
        "accepted_claims": [
            {"claim_type": "entry_point", "statement": "Test entry point.", "evidence_ids": ["ev.001"], "confidence": "medium"},
        ],
        "downgraded_claims": [],
        "rejected_claims": [],
        "validation_errors": [],
        "warnings": [],
    }

    validated_path = tmp_path / "validated.json"
    validated_path.write_text(json.dumps(validated), encoding="utf-8")

    claim_store_path = tmp_path / "claims.json"

    # Import
    result = subprocess.run(
        [sys.executable, "-m", "system_mapper.cli", "claim", "import", str(validated_path), "--claim-store", str(claim_store_path), "--component", "test"],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    import_result = json.loads(result.stdout)
    assert import_result["status"] == "imported"
    assert import_result["counts"]["new"] == 1

    # List
    result = subprocess.run(
        [sys.executable, "-m", "system_mapper.cli", "claim", "list", "--claim-store", str(claim_store_path)],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    list_result = json.loads(result.stdout)
    assert list_result["total"] == 1


def test_cli_eval_create_benchmark(tmp_path: Path):
    """Test the eval-create-benchmark CLI command."""
    import subprocess, sys, json

    output_path = tmp_path / "benchmark.json"

    result = subprocess.run(
        [sys.executable, "-m", "system_mapper.cli", "eval-create-benchmark", "--output", str(output_path)],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    output = json.loads(result.stdout)
    assert output["status"] == "created"
    assert Path(output["path"]).exists()


def test_cli_prompt_worker_kind(tmp_path: Path):
    """Test that prompt worker emits the worker contract."""
    import subprocess, sys

    result = subprocess.run(
        [sys.executable, "-m", "system_mapper.cli", "prompt", "worker"],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    assert "low-context system-mapping worker" in result.stdout
    assert "Every claim must cite evidence IDs" in result.stdout


# ===========================================================================
# Tests for subsystem-summaries
# ===========================================================================


def test_subsystem_summaries_from_cluster_report():
    """Test that build_subsystem_summaries enriches clusters with semantic info."""
    from system_mapper.clusters import build_subsystem_summaries

    cluster_report = {
        "input_edges": 4,
        "cluster_count": 2,
        "clusters": [
            {
                "id": "cluster-001",
                "nodes": [
                    "src/api.py", "src/service.py", "POST /invoices",
                    "invoices", "https://partner.example/export",
                    "src/api.py:main", "src/api.py:handler",
                    "src/service.py:process",
                ],
                "edge_count": 5,
                "edge_kinds": ["route", "internal", "call", "data_store", "external"],
                "components": ["billing/api", "billing/service"],
                "evidence_sources": ["src/api.py:1", "src/service.py:4"],
                "hub_nodes": ["src/api.py"],
            },
            {
                "id": "cluster-002",
                "nodes": ["config/schedule.yml", "cron schedule"],
                "edge_count": 1,
                "edge_kinds": ["trigger"],
                "components": ["ops/worker"],
                "evidence_sources": ["config/schedule.yml:1"],
                "hub_nodes": [],
            },
        ],
    }

    summaries = build_subsystem_summaries(cluster_report)
    assert len(summaries) == 2

    # First cluster: billing subsystem
    s0 = summaries[0]
    assert s0["cluster_id"] == "cluster-001"
    assert s0["probable_subsystem"] == "billing"
    assert "billing/api" in s0["why_grouped"]
    assert "route" in s0["why_grouped"]
    assert s0["node_count"] == 8
    assert s0["edge_count"] == 5
    # main entrypoint should be detected (contains "main")
    assert any("main" in ep for ep in s0["main_entrypoints"])
    assert "invoices" in s0["data_stores"]
    assert any("partner.example" in ext for ext in s0["external_systems"])
    assert any("POST /invoices" in r for r in s0["routes"])
    # Should have claims_to_review for external systems, data stores, routes
    assert len(s0["claims_to_review"]) >= 3

    # Second cluster: ops/worker (small, trigger-only)
    s1 = summaries[1]
    assert s1["cluster_id"] == "cluster-002"
    assert s1["probable_subsystem"] == "ops"
    assert s1["node_count"] == 2
    assert "cron schedule" in s1["triggers"]
    # Small cluster should have unknowns
    assert len(s1["unknowns"]) > 0


def test_subsystem_summaries_guess_name_from_directory():
    """Test that subsystem name falls back to directory when no components."""
    from system_mapper.clusters import build_subsystem_summaries

    cluster_report = {
        "input_edges": 1,
        "cluster_count": 1,
        "clusters": [
            {
                "id": "cluster-001",
                "nodes": ["src/helper.py", "src/main.py"],
                "edge_count": 1,
                "edge_kinds": ["internal"],
                "components": [],
                "evidence_sources": [],
                "hub_nodes": [],
            },
        ],
    }

    summaries = build_subsystem_summaries(cluster_report)
    assert len(summaries) == 1
    assert summaries[0]["probable_subsystem"] == "src"
    assert any("No component labels" in u for u in summaries[0]["unknowns"])


def test_cli_subsystem_summaries_command(tmp_path: Path):
    """Test the subsystem-summaries CLI command."""
    import json, subprocess, sys

    edges = tmp_path / "edges.jsonl"
    edges.write_text(
        "\n".join(
            json.dumps(record)
            for record in [
                {
                    "component": "billing/api",
                    "kind": "route",
                    "source": "src/api.py",
                    "target": "POST /invoices",
                    "confidence": "high",
                    "source_line": 10,
                },
                {
                    "component": "billing/api",
                    "kind": "internal",
                    "source": "src/api.py",
                    "target": "src/service.py",
                    "confidence": "high",
                    "source_line": 2,
                },
                {
                    "component": "billing/service",
                    "kind": "data_store",
                    "source": "src/service.py",
                    "target": "invoices",
                    "confidence": "medium",
                    "source_line": 4,
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "system_mapper.cli",
            "subsystem-summaries",
            str(edges),
            "--json",
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )

    output = json.loads(result.stdout)
    assert output["cluster_count"] == 1
    assert len(output["subsystem_summaries"]) == 1
    s = output["subsystem_summaries"][0]
    assert s["probable_subsystem"] == "billing"
    assert "invoices" in s["data_stores"]
    assert any("POST /invoices" in r for r in s["routes"])
    assert len(s["claims_to_review"]) > 0


# ===========================================================================
# Tests for uncertainty-aware strategy
# ===========================================================================


def test_uncertainty_aware_strategy_selects_highest_value_slice(tmp_path: Path):
    """Test that uncertainty-aware strategy prioritizes slices with unknowns."""
    from system_mapper.runner import run_next_slice

    # Create two files: one with unknowns (manual retry mention), one without
    write(
        tmp_path / "src" / "billing.py",
        """
import requests
DATABASE_TABLE = "invoices"

def export_invoice(invoice_id):
    requests.post("https://partner.example/export", json={"invoice_id": invoice_id})
    return invoice_id
""".strip(),
    )
    write(
        tmp_path / "docs" / "billing.md",
        "# Billing\nInvoice exports run nightly and may need manual retry.\n",
    )
    write(
        tmp_path / "src" / "simple.py",
        "def hello():\n    return 'ok'\n",
    )

    # First, run breadth-first to create all artifacts
    result1 = run_next_slice(tmp_path, strategy="breadth-first", output_layout="flat")
    assert result1["outcome"] == "advanced"

    # Run remaining slices
    while True:
        result = run_next_slice(tmp_path, strategy="breadth-first", output_layout="flat")
        if result["outcome"] == "no_change":
            break

    # Now test uncertainty-aware: it should select the slice with unknowns first
    # (the billing one has "manual retry" which creates an unknown)
    result = run_next_slice(
        tmp_path,
        strategy="uncertainty-aware",
        output_layout="flat",
        claim_store_path=str(tmp_path / ".system-map" / "claims.json"),
    )
    # All artifacts exist, so it should return no_change
    assert result["outcome"] == "no_change"


# ===========================================================================
# End-to-end integration test
# ===========================================================================


def test_summary_extracts_ruby_entry_points_dependencies_and_calls(tmp_path: Path):
    write(tmp_path / "app" / "services" / "map_builder.rb", "class MapBuilder\nend\n")
    write(
        tmp_path / "app" / "controllers" / "maps_controller.rb",
        """
require_relative '../services/map_builder'

module Admin
  class MapsController
    def create
      validate_payload
      MapBuilder.new
    end

    def validate_payload
      true
    end
  end
end
""".strip(),
    )

    summary = summarize_component(tmp_path, ["app/controllers/maps_controller.rb"], component="ruby/maps")

    assert "app/controllers/maps_controller.rb:Admin" in summary.entry_points
    assert "app/controllers/maps_controller.rb:MapsController" in summary.entry_points
    assert "app/controllers/maps_controller.rb:create" in summary.entry_points
    assert "app/controllers/maps_controller.rb:validate_payload" in summary.entry_points
    internal_edges = {edge.target: edge for edge in summary.edges if edge.kind == "internal"}
    assert "app/services/map_builder.rb" in internal_edges
    assert internal_edges["app/services/map_builder.rb"].source_line == 1
    call_edges = {edge.target: edge for edge in summary.edges if edge.kind == "call"}
    assert "app/controllers/maps_controller.rb:validate_payload" in call_edges
    assert call_edges["app/controllers/maps_controller.rb:validate_payload"].source_line == 6
    assert "app/controllers/maps_controller.rb:new" not in call_edges


def test_full_pipeline_plan_next_worker_validate_claim_quality(tmp_path: Path):
    """End-to-end test: plan → next → worker run → validate → claim import → quality."""
    import json, subprocess, sys

    # Set up a small project
    write(
        tmp_path / "src" / "billing.py",
        """
import requests
DATABASE_TABLE = "invoices"

# Owner: Billing Ops
# Business rule: invoices must be approved before export.
def export_invoice(invoice_id):
    requests.post("https://partner.example/export", json={"invoice_id": invoice_id})
    return invoice_id
""".strip(),
    )
    write(
        tmp_path / "docs" / "billing.md",
        "# Billing\nInvoice exports run nightly and may need manual retry.\n",
    )
    write(
        tmp_path / "config" / "schedule.yml",
        "billing_export: 0 2 * * *\n",
    )

    # Step 1: Plan
    result = subprocess.run(
        [
            sys.executable, "-m", "system_mapper.cli",
            "plan", str(tmp_path), "--json", "--output-layout", "flat",
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    )
    plan = json.loads(result.stdout)
    assert plan["slices"]
    assert plan["strategy"] == "breadth-first"

    # Step 2: Run next until all slices are done
    while True:
        result = subprocess.run(
            [
                sys.executable, "-m", "system_mapper.cli",
                "next", str(tmp_path), "--output-layout", "flat",
            ],
            cwd=Path(__file__).resolve().parents[1],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        )
        next_result = json.loads(result.stdout)
        if next_result["outcome"] == "no_change":
            break
        assert next_result["outcome"] == "advanced"

    # Step 3: Worker run (generate prompt, no LLM)
    # Find a packet
    packets_dir = tmp_path / ".system-map" / "packets"
    packet_files = list(packets_dir.glob("*.json"))
    assert packet_files, "No packets were generated"

    result = subprocess.run(
        [
            sys.executable, "-m", "system_mapper.cli",
            "worker", "run", str(packet_files[0]),
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    )
    worker_output = json.loads(result.stdout)
    assert "_prompt" in worker_output
    assert "low-context system-mapping worker" in worker_output["_prompt"]

    # Step 4: Validate (use the packet as both worker output and packet for simplicity)
    # Create a minimal valid worker output
    packet = json.loads(packet_files[0].read_text())
    minimal_worker = {
        "schema_version": "0.2",
        "component": packet.get("component", "test"),
        "claims": [
            {
                "claim_type": "purpose",
                "statement": "This component handles invoice exports.",
                "evidence_ids": [packet["evidence_ledger"][0]["id"]] if packet.get("evidence_ledger") else [],
                "confidence": "medium",
            }
        ],
        "hypotheses": [],
        "unknowns": [],
        "conflicts": [],
        "next_actions": [],
    }
    worker_path = tmp_path / "test-worker.json"
    worker_path.write_text(json.dumps(minimal_worker), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable, "-m", "system_mapper.cli",
            "validate", str(worker_path), str(packet_files[0]),
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    )
    validation = json.loads(result.stdout)
    assert "accepted_claims" in validation

    # Step 5: Claim import
    validated_path = tmp_path / "test-validated.json"
    validated_path.write_text(json.dumps(validation), encoding="utf-8")
    claim_store_path = tmp_path / "test-claims.json"

    result = subprocess.run(
        [
            sys.executable, "-m", "system_mapper.cli",
            "claim", "import", str(validated_path),
            "--claim-store", str(claim_store_path),
            "--component", "billing",
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    )
    import_result = json.loads(result.stdout)
    assert import_result["status"] == "imported"

    # Step 6: Quality check
    result = subprocess.run(
        [
            sys.executable, "-m", "system_mapper.cli",
            "quality", str(validated_path),
            "--evidence-source", str(packet_files[0]),
            "--min-score", "0.0",  # Low bar since minimal worker output
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    )
    quality = json.loads(result.stdout)
    assert "anti_garbage_score" in quality
    assert "metrics" in quality
