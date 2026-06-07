# Start here

This guide is the short, opinionated path for a first-time `system-mapper` user. It assumes you have a local checkout of some repository or documentation folder that you want to understand without reading everything at once.

## What this tool is for

`system-mapper` helps create an evidence-backed map of a living system: code, documentation, configuration, operational hints, human/manual processes, dependencies, unknowns, and changes over time.

It is not a magic architecture oracle. The intended use is:

1. let deterministic commands gather bounded evidence;
2. hand small packets to a human or low-context AI worker;
3. keep observed facts, interpretation, and unknowns separate;
4. merge small summaries upward into a larger system map;
5. revisit claims when code/docs/config change.

## Five-minute first run

### Fast path: let `next` choose the slice

From this repository, run the command against the project you want to inspect:

```bash
uv run system-mapper next /path/to/target --output-layout flat
```

If the command returns `outcome: "advanced"`, open the files listed under `artifacts`:

1. Read `summary` first. This is the bounded component summary.
2. Skim `edges` next. This is JSONL dependency/flow evidence.
3. Give `packet` to a human or low-context AI worker if you want interpretation or follow-up analysis.

Run the same command again to advance to the next missing slice. When it returns `outcome: "no_change"`, the current planned slices already have artifacts.

A minimal repeated loop can be this simple:

```bash
while true; do
  uv run system-mapper next /path/to/target --output-layout flat
  sleep 600
done
```

The loop only repeats. The mapper records progress in `.system-map/`.

### Manual path: inspect the plan yourself

If you want to choose slices manually, start with inventory and plan:

```bash
mkdir -p .system-map/{inventory,plans,components,edges,packets}
uv run system-mapper inventory /path/to/target --json > .system-map/inventory/root.json
uv run system-mapper plan /path/to/target --json > .system-map/plans/first-slices.json
```

Open `.system-map/plans/first-slices.json` and pick the first planned slice. It will contain:

- `paths`: the bounded files to inspect together;
- `estimated_tokens`: a rough context-budget estimate;
- `output_locations`: suggested files for packet, component summary, and edge output;
- `rationale`: why this slice was selected.

Then run the slice, graph, and packet commands for those paths. Example:

```bash
uv run system-mapper slice /path/to/target src/app.py docs/overview.md \
  --component app/overview \
  --json > .system-map/components/app-overview.json

uv run system-mapper graph /path/to/target src/app.py docs/overview.md \
  --component app/overview \
  > .system-map/edges/app-overview.jsonl

uv run system-mapper packet /path/to/target src/app.py docs/overview.md \
  --component app/overview \
  > .system-map/packets/app-overview.json
```

If the paths in your slice differ, use those instead. Prefer the planned `output_locations` when you want stable filenames.

## How to read the first output

Start with the component summary JSON:

- `scope` says exactly what was inspected. Claims outside this scope are suspect.
- `evidence` records source files, detected symbols, notes, and content revisions.
- `evidence_ledger` gives deterministic evidence IDs with file and line spans; durable `claims` cite those IDs.
- `conflicts` preserves contradictory lower-level claims after `merge` instead of pretending they agree.
- `entry_points`, `inputs`, `outputs`, `edges`, and `external_systems` are observed hints, not a complete architecture proof.
- `unknowns` are useful work items, not failures.
- `confidence` tells you which parts deserve human review before being treated as reliable.

Then inspect the edge records:

```bash
uv run system-mapper graph /path/to/target src/app.py docs/overview.md \
  --component app/overview \
  --format mermaid
```

Mermaid is for a quick visual check. JSONL is the safer machine-merge format.

Finally, open the work packet. This is the handoff artifact for a low-context worker. It contains the summary, edges, unknowns, next actions, and a prompt contract that tells the worker not to overclaim.

## What to do next

After one slice works:

1. process the next few planned slices;
2. name components consistently (`billing/export`, `docs/onboarding`, `ops/scheduler`);
3. preserve unknowns instead of filling them with guesses;
4. merge only evidence-backed lower-level summaries into higher-level maps with `system-mapper merge`;
5. rerun `update` when a diff may make old summaries or claim IDs stale.

For upward merging:

```bash
uv run system-mapper merge \
  .system-map/components/app-code.json \
  .system-map/components/app-docs.json \
  --component app \
  --json > .system-map/components/app.json
```

For change review:

```bash
git diff origin/main...HEAD > /tmp/change.diff
uv run system-mapper update .system-map/components/app-overview.json /tmp/change.diff --json
```

## Good first success criteria

A first mapping pass is useful when it gives you:

- a small set of inspected files with exact scope;
- at least one summary that separates evidence from interpretation;
- edge records with source files and, where detected, source lines;
- unknowns that point to the next inspection or human question;
- no confident claims that exceed the inspected scope.

Do not aim for a beautiful full-system map on day one. Aim for trustworthy small maps that can be recombined.
