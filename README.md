# system-mapper

`system-mapper` is a small CLI/toolkit for building **evidence-backed living system maps** from large codebases, documentation sets, configuration, and incoming code changes.

It is designed around the approach discussed for weak / low-context AI:

- inspect bounded slices, not the whole system at once;
- treat code, docs, config, and operational artefacts as first-class evidence;
- separate evidence from interpretation;
- preserve unknowns, stale sources, and conflicts;
- emit machine-readable summaries and edges that can be recursively merged;
- update incrementally as new code/docs are merged.

## Current MVP capabilities

- `inventory`: classify files as code, document, config, or other, while skipping dependency/build directories.
- `slice`: produce a bounded component summary with evidence, entry points, detected Python and JavaScript/TypeScript internal dependencies, external/data-store/trigger edges, human/manual hints, risks, unknowns, and confidence scores.
- `update`: compare a previous JSON summary with a git diff and report changed files, likely behaviour changes, edge changes, possibly stale docs, downstream areas to reinspect, and a changelog entry.
- `graph`: emit dependency/data-flow edges from a bounded slice as JSONL records for recursive merge, clustering, or downstream map tooling.
- `packet`: package a bounded slice summary, evidence, edges, unknowns, next actions, and the low-context AI prompt contract as JSON.
- `plan`: choose bounded next slices with a default 45,000-token limit, selectable ordering strategy, and planned output locations.
- `prompt`: emit reusable low-context AI prompt contracts for slice analysis and living-system updates.

This version uses deterministic heuristics only. It is intentionally suitable as a substrate for low-power AI agents: the CLI gathers stable evidence and produces structured context for an agent to review or merge upward.

## Install / run

```bash
uv run system-mapper --help
```

Or directly:

```bash
uv run python -m system_mapper.cli --help
```

## Examples

Inventory a repository:

```bash
uv run system-mapper inventory /path/to/repo --json > .system-map/inventory.json
```

Summarise a bounded slice from code + docs + config:

```bash
uv run system-mapper slice /path/to/repo \
  src/billing.py docs/billing.md config/schedule.yml \
  --component billing/export \
  --json > .system-map/components/billing-export.json
```

Analyse a merge/change diff against an existing summary:

```bash
git diff origin/main...HEAD > /tmp/change.diff
uv run system-mapper update .system-map/components/billing-export.json /tmp/change.diff --json
```

Emit slice edges as JSONL for graph merge/clustering tools:

```bash
uv run system-mapper graph /path/to/repo \
  src/billing.py docs/billing.md config/schedule.yml \
  --component billing/export > .system-map/edges/billing-export.jsonl
```

Package a bounded low-context AI work packet:

```bash
uv run system-mapper packet /path/to/repo \
  src/billing.py docs/billing.md config/schedule.yml \
  --component billing/export > .system-map/packets/billing-export.json
```

Plan next bounded slices from a local checkout, keeping each slice under the default 45,000 estimated tokens:

```bash
uv run system-mapper plan /path/to/repo --json > .system-map/slice-plan.json
uv run system-mapper plan /path/to/repo \
  --strategy chronological \
  --output-layout 1-level \
  --output-root .system-map \
  --json
```

Strategy options are `breadth-first`, `depth-first`, `chronological`, and `dependency-aware`; output layouts are `flat`, `1-level`, and `2-level`. Defaults are `breadth-first` and `2-level` so an initial map gets a broad system shape without dumping every artifact into one flat folder. Use `dependency-aware` when a follow-up worker should prioritise edge-rich files with internal dependencies, external systems, data stores, or triggers before quieter supporting artefacts.

Emit a prompt contract for a low-context AI worker:

```bash
uv run system-mapper prompt slice --component billing/export
uv run system-mapper prompt update --component billing/export
```

## Intended workflow

```text
1. Inventory code + documents + operational artefacts.
2. Select bounded slices: file, folder, module, subsystem.
3. Summarise each slice with evidence, unknowns, confidence, and graph edges.
4. Let a low-context AI merge only lower-level summaries into higher-level maps.
5. When code/docs change, analyse the diff against previous summaries.
6. Reinspect changed slices and propagate stale/changed claims upward.
7. Keep unresolved conflicts instead of smoothing them over.
```

## Summary contract

Each component summary is intended to answer:

- component name and inspected scope;
- observed evidence by source type and content revision;
- purpose, entry points, inputs, outputs;
- data stores, external systems, triggers;
- business rules and human/manual steps;
- risks, unknowns, confidence;
- suggested next files/components to inspect.

## Development

```bash
uv run pytest -q
```

## Roadmap

- richer language-specific parsers;
- recursive summary merging;
- conflict detection between code and documentation summaries;
- GitHub Actions workflow for PR/diff-driven map updates;
- optional LLM prompt generation for weak-agent review loops;
- graph export formats such as Mermaid, DOT, and JSONL.
