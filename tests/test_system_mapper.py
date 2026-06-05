from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from system_mapper.inventory import build_inventory
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

    inventory = build_inventory(tmp_path)

    rel_paths = {item.path for item in inventory.items}
    assert "src/billing.py" in rel_paths
    assert "docs/billing.md" in rel_paths
    assert "config/schedule.yml" in rel_paths
    assert "node_modules/ignored.js" not in rel_paths
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
