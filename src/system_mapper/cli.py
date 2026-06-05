from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .inventory import build_inventory
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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
