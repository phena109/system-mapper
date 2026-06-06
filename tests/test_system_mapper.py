from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from system_mapper.inventory import build_inventory
from system_mapper.packet import build_work_packet
from system_mapper.planner import DEFAULT_TOKEN_LIMIT, build_slice_plan
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

    internal_targets = {edge.target for edge in summary.edges if edge.kind == "internal"}
    assert "src/billing/helpers.py" in internal_targets
    assert "src/billing/settings.py" in internal_targets
    assert "json" not in internal_targets


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

    internal_targets = {edge.target for edge in summary.edges if edge.kind == "internal"}
    assert "src/routes/helpers.ts" in internal_targets
    assert "src/routes/shared/index.ts" in internal_targets
    assert "src/routes/legacy.js" in internal_targets
    assert "express" not in internal_targets


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
