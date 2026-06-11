from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .architecture_brief import build_architecture_brief
from .claims import ClaimStore, validate_worker_output
from .clusters import cluster_edge_file
from .eval import create_sample_benchmark, evaluate_map_usefulness, load_benchmark
from .graph_formats import render_dot, render_mermaid
from .inventory import build_inventory
from .merge import merge_component_summaries
from .packet import build_work_packet
from .planner import DEFAULT_TOKEN_LIMIT, build_slice_plan
from .prompts import build_prompt
from .quality import evaluate_map_quality
from .runner import run_next_slice
from .summarizer import summarize_component
from .update import update_summary_from_diff
from .worker import claims_from_worker_output, get_worker_contract, parse_worker_output, run_worker


def emit(payload, as_json: bool) -> None:
    data = payload.to_dict() if hasattr(payload, "to_dict") else payload
    if as_json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        if isinstance(data, dict):
            for key, value in data.items():
                print(f"{key}: {value}")
        else:
            print(data)


# ---------------------------------------------------------------------------
# Existing commands
# ---------------------------------------------------------------------------

def cmd_inventory(args: argparse.Namespace) -> None:
    emit(build_inventory(args.root), args.json)


def cmd_slice(args: argparse.Namespace) -> None:
    emit(summarize_component(args.root, args.paths, args.component), args.json)


def cmd_update(args: argparse.Namespace) -> None:
    previous = json.loads(Path(args.previous_summary).read_text(encoding="utf-8"))
    diff = Path(args.diff).read_text(encoding="utf-8") if args.diff != "-" else sys.stdin.read()
    emit(update_summary_from_diff(previous, diff), args.json)


def cmd_merge(args: argparse.Namespace) -> None:
    summaries = [json.loads(Path(path).read_text(encoding="utf-8")) for path in args.summary_files]
    emit(merge_component_summaries(summaries, args.component, claim_store_path=args.claim_store), args.json)


def cmd_graph(args: argparse.Namespace) -> None:
    summary = summarize_component(args.root, args.paths, args.component)
    if args.format == "mermaid":
        print(render_mermaid(summary), end="")
        return
    if args.format == "dot":
        print(render_dot(summary), end="")
        return
    for edge in summary.edges:
        print(
            json.dumps(
                {
                    "component": summary.component,
                    "kind": edge.kind,
                    "source": edge.source,
                    "target": edge.target,
                    "confidence": edge.confidence,
                    "source_line": edge.source_line,
                },
                sort_keys=True,
            )
        )


def cmd_cluster(args: argparse.Namespace) -> None:
    emit(cluster_edge_file(args.edge_jsonl), args.json)


def cmd_architecture_brief(args: argparse.Namespace) -> None:
    brief = build_architecture_brief(
        args.edge_jsonl,
        top_file_edges=args.top_file_edges,
        min_edge_weight=args.min_edge_weight,
    )
    if args.json:
        print(json.dumps(brief, indent=2, sort_keys=True))
    else:
        print(brief["text_brief"])


def cmd_prompt(args: argparse.Namespace) -> None:
    if args.kind == "worker":
        print(get_worker_contract())
    else:
        print(build_prompt(args.kind, args.component))


def cmd_packet(args: argparse.Namespace) -> None:
    print(json.dumps(build_work_packet(args.root, args.paths, args.component), indent=2, sort_keys=True))


def cmd_plan(args: argparse.Namespace) -> None:
    emit(
        build_slice_plan(
            args.root,
            strategy=args.strategy,
            token_limit=args.token_limit,
            output_root=args.output_root,
            output_layout=args.output_layout,
        ),
        args.json,
    )


def cmd_next(args: argparse.Namespace) -> None:
    emit(
        run_next_slice(
            args.root,
            strategy=args.strategy,
            token_limit=args.token_limit,
            output_root=args.output_root,
            output_layout=args.output_layout,
            claim_store_path=args.claim_store,
        ),
        True,
    )


# ---------------------------------------------------------------------------
# New commands: worker
# ---------------------------------------------------------------------------

def cmd_worker_run(args: argparse.Namespace) -> None:
    """Run a weak LLM worker over a packet."""
    result = run_worker(
        args.packet_path,
        model=args.model,
        output_path=args.output,
        llm_command=args.llm_command,
    )
    if not args.output:
        print(json.dumps(result, indent=2, sort_keys=True))


# ---------------------------------------------------------------------------
# New commands: validate
# ---------------------------------------------------------------------------

def cmd_validate(args: argparse.Namespace) -> None:
    """Validate a worker output against its packet."""
    worker_output = json.loads(Path(args.worker_output).read_text(encoding="utf-8"))
    packet = json.loads(Path(args.packet).read_text(encoding="utf-8"))
    result = validate_worker_output(worker_output, packet)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(
            json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))


# ---------------------------------------------------------------------------
# New commands: claim
# ---------------------------------------------------------------------------

def cmd_claim_import(args: argparse.Namespace) -> None:
    """Import validated worker output into the claim store."""
    validated = json.loads(Path(args.validated_output).read_text(encoding="utf-8"))
    claim_store_path = Path(args.claim_store)
    store = ClaimStore(claim_store_path)

    # Reconstruct validation result
    from .claims import ValidationResult
    vr = ValidationResult(
        accepted_claims=validated.get("accepted_claims", []),
        downgraded_claims=validated.get("downgraded_claims", []),
        rejected_claims=validated.get("rejected_claims", []),
        validation_errors=validated.get("validation_errors", []),
        warnings=validated.get("warnings", []),
    )

    # Determine component from the validated output or args
    component = args.component or ""
    if not component:
        # Try to extract from the first accepted claim
        for c in vr.accepted_claims:
            if isinstance(c, dict) and "component" in c:
                component = c["component"]
                break

    claims = claims_from_worker_output(
        worker_output={},
        validation_result=vr,
        component=component,
        source_worker=str(Path(args.validated_output)),
    )

    counts = store.import_claims(claims)
    print(json.dumps({
        "status": "imported",
        "claim_store": str(claim_store_path),
        "counts": counts,
        "store_stats": store.stats,
    }, indent=2, sort_keys=True))


def cmd_claim_list(args: argparse.Namespace) -> None:
    """List claims from the claim store."""
    store = ClaimStore(args.claim_store)
    claims = store.list_claims(
        component=args.component,
        claim_type=args.claim_type,
        status=args.status,
        min_confidence=args.min_confidence,
    )
    print(json.dumps({
        "claim_store": str(args.claim_store),
        "total": len(claims),
        "claims": [c.to_dict() for c in claims],
    }, indent=2, sort_keys=True))


def cmd_claim_conflicts(args: argparse.Namespace) -> None:
    """Show conflicts in the claim store."""
    store = ClaimStore(args.claim_store)
    conflicts = store.get_conflicts()
    print(json.dumps({
        "claim_store": str(args.claim_store),
        "conflict_count": len(conflicts),
        "conflicts": conflicts,
    }, indent=2, sort_keys=True))


def cmd_claim_stats(args: argparse.Namespace) -> None:
    """Show claim store statistics."""
    store = ClaimStore(args.claim_store)
    print(json.dumps({
        "claim_store": str(args.claim_store),
        "stats": store.stats,
    }, indent=2, sort_keys=True))


# ---------------------------------------------------------------------------
# New commands: eval
# ---------------------------------------------------------------------------

def cmd_eval(args: argparse.Namespace) -> None:
    """Evaluate map usefulness against benchmark questions."""
    questions = load_benchmark(args.benchmark)

    if args.mode == "mapped":
        system_map = json.loads(Path(args.system_map).read_text(encoding="utf-8"))
    else:
        # Raw mode: just use the files directly (simulated)
        system_map = {}

    report = evaluate_map_usefulness(questions, system_map)
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True))


def cmd_eval_create_benchmark(args: argparse.Namespace) -> None:
    """Create a sample benchmark file."""
    create_sample_benchmark(args.output)
    print(json.dumps({
        "status": "created",
        "path": str(args.output),
    }, indent=2, sort_keys=True))


# ---------------------------------------------------------------------------
# New command: quality
# ---------------------------------------------------------------------------

def cmd_quality(args: argparse.Namespace) -> None:
    """Score a system map for evidence-backed, non-garbage output."""
    system_map = json.loads(Path(args.system_map).read_text(encoding="utf-8"))
    if args.evidence_source:
        evidence_source = json.loads(Path(args.evidence_source).read_text(encoding="utf-8"))
        system_map = _merge_quality_evidence(system_map, evidence_source)
    report = evaluate_map_quality(system_map, min_score=args.min_score)
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    if args.fail_on_garbage and not report.passed:
        raise SystemExit(1)


def _merge_quality_evidence(system_map: dict, evidence_source: dict) -> dict:
    """Attach packet/summary evidence to worker or validation output for scoring."""
    merged = dict(system_map)
    for key in ("evidence_ledger", "evidence", "summary", "scope", "component", "unknowns", "conflicts"):
        if key in evidence_source and key not in merged:
            merged[key] = evidence_source[key]
    if "evidence_ledger" in evidence_source and "evidence_ledger" in system_map:
        merged["evidence_ledger"] = [
            *evidence_source.get("evidence_ledger", []),
            *system_map.get("evidence_ledger", []),
        ]
    return merged


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="system-mapper",
        description="Divide-and-conquer reasoning system for helping weak or low-context LLMs understand large software systems.",
    )
    sub = parser.add_subparsers(required=True)

    # --- inventory ---
    inv = sub.add_parser("inventory", help="Inventory code, documents, configs, and other files.")
    inv.add_argument("root")
    inv.add_argument("--json", action="store_true")
    inv.set_defaults(func=cmd_inventory)

    # --- slice ---
    sl = sub.add_parser("slice", help="Summarise a bounded component from selected files.")
    sl.add_argument("root")
    sl.add_argument("paths", nargs="+")
    sl.add_argument("--exclude", help="Glob pattern(s) for files/folders to exclude from processing.")
    sl.add_argument("--exclude_list", nargs="+", help="Specific list of paths/patterns to exclude.")
    sl.add_argument("--component")
    sl.add_argument("--json", action="store_true")
    sl.set_defaults(func=cmd_slice)

    # --- update ---
    up = sub.add_parser("update", help="Analyse a diff against a previous JSON summary.")
    up.add_argument("previous_summary")
    up.add_argument("diff", help="Diff file path, or - for stdin")
    up.add_argument("--json", action="store_true")
    up.set_defaults(func=cmd_update)

    # --- merge ---
    merge = sub.add_parser("merge", help="Merge lower-level JSON summaries into an upward system map while preserving claims and conflicts.")
    merge.add_argument("summary_files", nargs="+")
    merge.add_argument("--component")
    merge.add_argument("--claim-store", default=".system-map/claims.json", help="Path to claim store for enrichment.")
    merge.add_argument("--json", action="store_true")
    merge.set_defaults(func=cmd_merge)

    # --- graph ---
    graph = sub.add_parser("graph", help="Emit slice dependency/data-flow edges as JSONL or Mermaid records.")
    graph.add_argument("root")
    graph.add_argument("paths", nargs="+")
    graph.add_argument("--component")
    graph.add_argument(
        "--format",
        choices=["jsonl", "mermaid", "dot"],
        default="jsonl",
        help="Graph output format. Default: jsonl for machine merge; mermaid and dot give text diagrams for review.",
    )
    graph.set_defaults(func=cmd_graph)

    # --- cluster ---
    cluster = sub.add_parser("cluster", help="Cluster graph JSONL edges into connected subsystem/community summaries.")
    cluster.add_argument("edge_jsonl", help="Path to JSONL emitted by `system-mapper graph`.")
    cluster.add_argument("--json", action="store_true")
    cluster.set_defaults(func=cmd_cluster)

    # --- architecture-brief ---
    brief = sub.add_parser(
        "architecture-brief",
        help="Produce a human-readable architecture brief from graph JSONL edges.",
    )
    brief.add_argument("edge_jsonl", help="Path to JSONL emitted by `system-mapper graph`.")
    brief.add_argument("--json", action="store_true")
    brief.add_argument("--top-file-edges", type=int, default=20, help="Maximum number of file-to-file edges to show.")
    brief.add_argument("--min-edge-weight", type=int, default=1, help="Minimum edge weight to include.")
    brief.set_defaults(func=cmd_architecture_brief)

    # --- packet ---
    packet = sub.add_parser("packet", help="Emit a bounded low-context AI work packet as JSON.")
    packet.add_argument("root")
    packet.add_argument("paths", nargs="+")
    packet.add_argument("--component")
    packet.set_defaults(func=cmd_packet)

    # --- plan ---
    plan = sub.add_parser("plan", help="Plan bounded next slices and output locations.")
    plan.add_argument("root")
    plan.add_argument(
        "--strategy",
        choices=["breadth-first", "depth-first", "chronological", "dependency-aware"],
        default="breadth-first",
    )
    plan.add_argument("--token-limit", type=int, default=DEFAULT_TOKEN_LIMIT)
    plan.add_argument("--output-root", default=".system-map")
    plan.add_argument("--output-layout", choices=["flat", "1-level", "2-level"], default="2-level")
    plan.add_argument("--json", action="store_true")
    plan.set_defaults(func=cmd_plan)

    # --- next ---
    nxt = sub.add_parser("next", help="Write the next missing packet, summary, and edge artifacts.")
    nxt.add_argument("root")
    nxt.add_argument(
        "--strategy",
        choices=["breadth-first", "depth-first", "chronological", "dependency-aware", "uncertainty-aware"],
        default="breadth-first",
    )
    nxt.add_argument("--token-limit", type=int, default=DEFAULT_TOKEN_LIMIT)
    nxt.add_argument("--output-root", default=".system-map")
    nxt.add_argument("--output-layout", choices=["flat", "1-level", "2-level"], default="2-level")
    nxt.add_argument("--claim-store", default=".system-map/claims.json", help="Path to claim store for uncertainty-aware strategy.")
    nxt.set_defaults(func=cmd_next)

    # --- prompt ---
    prompt = sub.add_parser("prompt", help="Emit reusable low-context AI prompts for system mapping.")
    prompt.add_argument("kind", choices=["slice", "update", "worker"])
    prompt.add_argument("--component")
    prompt.set_defaults(func=cmd_prompt)

    # --- worker ---
    worker = sub.add_parser("worker", help="Run weak LLM workers over packets.")
    worker_sub = worker.add_subparsers(required=True)

    worker_run = worker_sub.add_parser("run", help="Run a weak LLM worker over a packet.")
    worker_run.add_argument("packet_path", help="Path to the packet JSON file.")
    worker_run.add_argument("--model", default="local", help="Model identifier (e.g. qwen3-4b).")
    worker_run.add_argument("--output", help="Output path for worker JSON.")
    worker_run.add_argument("--llm-command", help="LLM command to run (e.g. 'ollama run qwen3:4b'). If omitted, prints the prompt for external processing.")
    worker_run.set_defaults(func=cmd_worker_run)

    # --- validate ---
    val = sub.add_parser("validate", help="Validate worker output against packet evidence.")
    val.add_argument("worker_output", help="Path to worker output JSON.")
    val.add_argument("packet", help="Path to the original packet JSON.")
    val.add_argument("--output", help="Output path for validated JSON.")
    val.set_defaults(func=cmd_validate)

    # --- claim ---
    claim = sub.add_parser("claim", help="Manage the evidence-backed claim store.")
    claim_sub = claim.add_subparsers(required=True)

    claim_import = claim_sub.add_parser("import", help="Import validated worker output into the claim store.")
    claim_import.add_argument("validated_output", help="Path to validated worker output JSON.")
    claim_import.add_argument("--claim-store", default=".system-map/claims.json")
    claim_import.add_argument("--component", help="Component name (auto-detected if not provided).")
    claim_import.set_defaults(func=cmd_claim_import)

    claim_list = claim_sub.add_parser("list", help="List claims from the claim store.")
    claim_list.add_argument("--claim-store", default=".system-map/claims.json")
    claim_list.add_argument("--component", help="Filter by component.")
    claim_list.add_argument("--claim-type", help="Filter by claim type.")
    claim_list.add_argument("--status", help="Filter by status.")
    claim_list.add_argument("--min-confidence", choices=["low", "medium", "high"], help="Minimum confidence.")
    claim_list.set_defaults(func=cmd_claim_list)

    claim_conflicts = claim_sub.add_parser("conflicts", help="Show conflicts in the claim store.")
    claim_conflicts.add_argument("--claim-store", default=".system-map/claims.json")
    claim_conflicts.set_defaults(func=cmd_claim_conflicts)

    claim_stats = claim_sub.add_parser("stats", help="Show claim store statistics.")
    claim_stats.add_argument("--claim-store", default=".system-map/claims.json")
    claim_stats.set_defaults(func=cmd_claim_stats)

    # --- eval ---
    ev = sub.add_parser("eval", help="Evaluate map usefulness against benchmark questions.")
    ev.add_argument("benchmark", help="Path to benchmark questions JSON.")
    ev.add_argument(
        "--mode",
        choices=["raw", "mapped"],
        default="mapped",
        help="Evaluation mode: raw (no map) or mapped (with system map).",
    )
    ev.add_argument("--system-map", help="Path to system map JSON (required for mapped mode).")
    ev.set_defaults(func=cmd_eval)

    eval_create = sub.add_parser("eval-create-benchmark", help="Create a sample benchmark file.")
    eval_create.add_argument("--output", default=".system-map/benchmarks/sample.json")
    eval_create.set_defaults(func=cmd_eval_create_benchmark)

    # --- quality ---
    quality = sub.add_parser("quality", help="Score a system map with measurable anti-garbage checks.")
    quality.add_argument("system_map", help="Path to a component summary, merged map, packet, worker output, or validated JSON file.")
    quality.add_argument("--evidence-source", help="Optional packet/summary JSON providing the evidence ledger for worker or validated output.")
    quality.add_argument("--min-score", type=float, default=0.8, help="Minimum anti-garbage score required to pass.")
    quality.add_argument("--fail-on-garbage", action="store_true", help="Exit non-zero when the quality gate fails.")
    quality.set_defaults(func=cmd_quality)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
