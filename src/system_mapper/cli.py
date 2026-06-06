from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .graph_formats import render_dot, render_mermaid
from .inventory import build_inventory
from .packet import build_work_packet
from .planner import DEFAULT_TOKEN_LIMIT, build_slice_plan
from .prompts import build_prompt
from .summarizer import summarize_component
from .update import update_summary_from_diff


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


def cmd_inventory(args: argparse.Namespace) -> None:
    emit(build_inventory(args.root), args.json)


def cmd_slice(args: argparse.Namespace) -> None:
    emit(summarize_component(args.root, args.paths, args.component), args.json)


def cmd_update(args: argparse.Namespace) -> None:
    previous = json.loads(Path(args.previous_summary).read_text(encoding="utf-8"))
    diff = Path(args.diff).read_text(encoding="utf-8") if args.diff != "-" else sys.stdin.read()
    emit(update_summary_from_diff(previous, diff), args.json)


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


def cmd_prompt(args: argparse.Namespace) -> None:
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="system-mapper", description="Build and maintain evidence-backed living system maps.")
    sub = parser.add_subparsers(required=True)

    inv = sub.add_parser("inventory", help="Inventory code, documents, configs, and other files.")
    inv.add_argument("root")
    inv.add_argument("--json", action="store_true")
    inv.set_defaults(func=cmd_inventory)

    sl = sub.add_parser("slice", help="Summarise a bounded component from selected files.")
    sl.add_argument("root")
    sl.add_argument("paths", nargs="+")
    sl.add_argument("--component")
    sl.add_argument("--json", action="store_true")
    sl.set_defaults(func=cmd_slice)

    up = sub.add_parser("update", help="Analyse a diff against a previous JSON summary.")
    up.add_argument("previous_summary")
    up.add_argument("diff", help="Diff file path, or - for stdin")
    up.add_argument("--json", action="store_true")
    up.set_defaults(func=cmd_update)

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

    packet = sub.add_parser("packet", help="Emit a bounded low-context AI work packet as JSON.")
    packet.add_argument("root")
    packet.add_argument("paths", nargs="+")
    packet.add_argument("--component")
    packet.set_defaults(func=cmd_packet)

    plan = sub.add_parser("plan", help="Plan bounded next slices and output locations.")
    plan.add_argument("root")
    plan.add_argument(
        "--strategy",
        choices=["breadth-first", "depth-first", "chronological", "dependency-aware"],
        default="breadth-first",
        help="Next-slice ordering. Default: breadth-first for whole-system shape first; dependency-aware prioritises edge-rich files.",
    )
    plan.add_argument(
        "--token-limit",
        type=int,
        default=DEFAULT_TOKEN_LIMIT,
        help="Maximum estimated tokens per planned slice. Default: 45000.",
    )
    plan.add_argument("--output-root", default=".system-map")
    plan.add_argument(
        "--output-layout",
        choices=["flat", "1-level", "2-level"],
        default="2-level",
        help="Output path grouping. Default: 2-level to avoid one huge flat folder.",
    )
    plan.add_argument("--json", action="store_true")
    plan.set_defaults(func=cmd_plan)

    prompt = sub.add_parser("prompt", help="Emit reusable low-context AI prompts for system mapping.")
    prompt.add_argument("kind", choices=["slice", "update"])
    prompt.add_argument("--component")
    prompt.set_defaults(func=cmd_prompt)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
