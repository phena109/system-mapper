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
- `slice`: produce a bounded component summary with evidence, a line-addressable `evidence_ledger`, durable typed `claims`, entry points, detected Python call edges, Python web-route decorator edges, JavaScript/TypeScript exported function/class/arrow-function entry points, Python, JavaScript/TypeScript, and Go internal dependencies, Go declarations and same-file call edges, PHP and C-like symbols/calls/includes, PHP route edges, external/data-store/trigger edges, source-line evidence records for deterministic graph edges, human/manual hints, risks, unknowns, and confidence scores.
- `update`: compare a previous JSON summary with a git diff and report changed files, likely behaviour changes, added Python route interfaces, edge changes, stale claim IDs whose evidence sources changed, possibly stale docs, downstream areas to reinspect, and a changelog entry.
- `merge`: recursively merge lower-level JSON summaries into an upward component/system summary while preserving claims, evidence ledger records, unknowns, and explicit conflicts.
- `graph`: emit dependency/data-flow edges from a bounded slice as JSONL records (including source line citations when detected) for recursive merge, clustering, or downstream map tooling, or as Mermaid / Graphviz DOT diagrams for quick visual review.
- `cluster`: group graph JSONL edge records into connected subsystem/community summaries with edge kinds, participating components, hub nodes, and evidence source citations.
- `architecture-brief`: produce a human-readable architecture brief from graph JSONL edges — file-to-file relationships ranked by weight, likely entry point, architectural layers (entry/core/leaf), external dependencies, data stores, routes, and triggers. This closes the last gap in the pipeline: `code → edges → clusters → architecture brief`.
- `packet`: package a bounded slice summary, evidence, edges, unknowns, next actions, and the low-context AI prompt contract as JSON.
- `plan`: choose bounded next slices with a default 45,000-token limit, selectable ordering strategy, planned output locations, and a rationale for why each slice is useful for a low-context worker.
- `next`: write the next missing packet, summary, and edge artifacts so a minimal cron loop can safely advance until it reaches `no_change`.
- `prompt`: emit reusable low-context AI prompt contracts for slice analysis and living-system updates.

This version uses deterministic heuristics only. It is intentionally suitable as a substrate for low-power AI agents: the CLI gathers stable evidence and produces structured context for an agent to review or merge upward.

## Start here

If you are new to the project, read these first:

1. [`docs/START_HERE.md`](docs/START_HERE.md) — a five-minute first run and the recommended workflow.
2. [`docs/interpreting-output.md`](docs/interpreting-output.md) — how to judge strong claims, weak claims, unknowns, confidence, and graph edges.
3. [`docs/templates.md`](docs/templates.md) — copyable component notes, interview prompts, and review checklists.

## Install / run

From this repository checkout:

```bash
uv run system-mapper --help
```

Or directly:

```bash
uv run python -m system_mapper.cli --help
```

## Quick start: map something useful

If you just want to start mapping a local project, use `next`. It is the easiest entry point because it chooses the next bounded slice and writes all three core artifacts for that slice.

```bash
# From the system-mapper checkout
uv run system-mapper next /path/to/repo --output-layout flat
```

Look at the returned JSON:

- `outcome: "advanced"` means it wrote a new slice's artifacts.
- `artifacts.summary` is the component summary to read first.
- `artifacts.edges` is the machine-readable edge list.
- `artifacts.packet` is the low-context AI handoff packet.
- `outcome: "no_change"` means every currently planned slice already has artifacts.

For a small repeated run, keep the loop dumb and let the mapper's `.system-map/` artifacts record progress:

```bash
while true; do
  uv run system-mapper next /path/to/repo --output-layout flat
  sleep 600
done
```

Stop the loop once `next` returns `no_change`, or leave it under cron if the target repo keeps changing.

## Examples

Inventory a repository manually:

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

Merge lower-level summaries upward while keeping claims and conflicts reviewable:

```bash
uv run system-mapper merge \
  .system-map/components/billing-code.json \
  .system-map/components/billing-docs.json \
  --component billing \
  --json > .system-map/components/billing.json
```

Analyse a merge/change diff against an existing summary:

```bash
git diff origin/main...HEAD > /tmp/change.diff
uv run system-mapper update .system-map/components/billing-export.json /tmp/change.diff --json
```

Emit slice edges as JSONL for graph merge/clustering tools, cluster previously emitted graph edges into subsystem/community summaries, or render as Mermaid / Graphviz DOT diagrams for quick human review:

```bash
uv run system-mapper graph /path/to/repo \
  src/billing.py docs/billing.md config/schedule.yml \
  --component billing/export > .system-map/edges/billing-export.jsonl
uv run system-mapper cluster .system-map/edges/billing-export.jsonl --json
uv run system-mapper architecture-brief .system-map/edges/billing-export.jsonl
uv run system-mapper architecture-brief .system-map/edges/billing-export.jsonl --json > .system-map/architecture-brief.json
uv run system-mapper graph /path/to/repo \\
  src/billing.py docs/billing.md config/schedule.yml \\
  --component billing/export \\
  --format mermaid > .system-map/edges/billing-export.mmd
uv run system-mapper graph /path/to/repo \
  src/billing.py docs/billing.md config/schedule.yml \
  --component billing/export \
  --format dot > .system-map/edges/billing-export.dot
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

Strategy options are `breadth-first`, `depth-first`, `chronological`, and `dependency-aware`; output layouts are `flat`, `1-level`, and `2-level`. Defaults are `breadth-first` and `2-level` so an initial map gets a broad system shape without dumping every artifact into one flat folder. Use `dependency-aware` when a follow-up worker should prioritise edge-rich files with internal dependencies, external systems, data stores, or triggers before quieter supporting artefacts. Each planned slice includes a short machine-readable `rationale` so a low-context worker can see whether it was selected for breadth, recency, path order, or edge/unknown density.

Advance one missing slice artifact set for a very small repeatable loop:

```bash
uv run system-mapper next /path/to/repo --output-layout flat
```

Repeated `next` calls write the next missing packet, summary, and JSONL edge file under `.system-map/`. Once every planned slice has artifacts, the command returns `outcome: no_change` instead of re-emitting the same work forever.

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
4. Cluster edges into subsystem communities.
5. Generate an architecture brief for human review (file-to-file relationships, entry point, layers, external deps).
6. Let a low-context AI merge only lower-level summaries into higher-level maps.
7. When code/docs change, analyse the diff against previous summaries.
8. Reinspect changed slices and propagate stale/changed claims upward.
9. Keep unresolved conflicts instead of smoothing them over.
```

## Summary contract

Each component summary is intended to answer:

- component name and inspected scope;
- observed evidence by source type and content revision;
- line-addressable `evidence_ledger` records with deterministic IDs;
- durable typed `claims` (`purpose`, `data_contract`, `trigger`, `business_rule`, `owner`, `risk`, `unknown`, etc.) that cite ledger IDs;
- merge-time `conflicts` that preserve contradictory lower-level claims instead of smoothing them over;
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
- richer graph clustering and recursive subsystem summaries.
