# system-mapper

`system-mapper` helps a low-context human or small LLM understand a repository one bounded slice at a time.

It does **not** try to produce a perfect architecture document in one pass. It plans small slices, extracts deterministic evidence from files, writes machine-readable summaries and graph edges, packages work for an LLM worker, validates claims against evidence IDs, and keeps uncertainty visible.

Core loop:

```text
plan a slice -> write summary/edges/packet -> worker makes cited claims -> validate -> import claims -> inspect next slice
```

## Quick start: map a repo for the first time

From this repository checkout, run `system-mapper` against the repository or folder you want to understand:

```bash
uv run system-mapper next /path/to/target --output-layout flat
```

This writes the first missing slice into `/path/to/target/.system-map/` by default and prints the exact artifact paths:

```json
{
  "outcome": "advanced",
  "artifacts": {
    "summary": ".system-map/components/<slice>.json",
    "edges": ".system-map/edges/<slice>.jsonl",
    "packet": ".system-map/packets/<slice>.json"
  }
}
```

Read the files in this order:

1. **summary** — what was inspected, observed evidence, claims, unknowns, and confidence.
2. **edges** — JSONL dependency, call, route, data-store, trigger, and external-system edges with source lines where available.
3. **packet** — the handoff bundle for a low-context LLM or human reviewer.

Then run the same command again:

```bash
uv run system-mapper next /path/to/target --output-layout flat
```

Each run advances to the next missing planned slice. When all currently planned slices exist, `next` returns `outcome: "no_change"`.

## What I learned by dogfooding it on this repo

Running the tool against its own source produced two planned slices:

- `README` — docs, tests, CLI, claims, and clustering code.
- `src/system_mapper` — planner, runner, summarizer, packet, quality, worker, merge, update, inventory, and models code.

The useful path was:

```bash
rm -rf /tmp/system-mapper-dogfood
mkdir -p /tmp/system-mapper-dogfood

uv run system-mapper plan . \
  --output-root /tmp/system-mapper-dogfood \
  --output-layout flat \
  --json > /tmp/system-mapper-dogfood/plan.json

uv run system-mapper next . \
  --output-root /tmp/system-mapper-dogfood \
  --output-layout flat

uv run system-mapper next . \
  --output-root /tmp/system-mapper-dogfood \
  --output-layout flat

uv run system-mapper architecture-brief \
  /tmp/system-mapper-dogfood/edges/src-system-mapper.jsonl
```

The architecture brief identified `src/system_mapper/runner.py` as the likely entry point for the automated slice loop, with core direct relationships to `packet.py`, `planner.py`, `summarizer.py`, and `claims.py`. That matched the source: the `next` command calls `run_next_slice`, which plans work, writes a packet, writes a summary, and writes edges.

The dogfood run also exposed an important limitation: a generated component summary scored `0.783` against the default `0.8` quality threshold. The quality gate flagged vague language and weak high-confidence support. Treat generated maps as orientation until the quality gate passes or a human reviews the claims.

## Scenario: inspect the plan before writing artifacts

Use `plan` when you want to see the slices before generating summaries:

```bash
uv run system-mapper plan /path/to/target --json
```

Useful options:

```bash
uv run system-mapper plan /path/to/target \
  --strategy breadth-first \
  --token-limit 45000 \
  --output-root .system-map \
  --output-layout flat \
  --json
```

Strategies available to `plan`:

- `breadth-first` — shallow system shape first. This is the default.
- `depth-first` — go deeper in one area before spreading out.
- `chronological` — older/newer file ordering based on file metadata.
- `dependency-aware` — prioritise files that appear more connected.

`next` supports the same strategies plus `uncertainty-aware`, which can use a claim store to prefer areas with unresolved uncertainty.

## Scenario: manually map one component

Use the manual commands when you already know which files belong together.

```bash
uv run system-mapper slice /path/to/target \
  src/app.py docs/app.md config/app.yml \
  --component app \
  --json > .system-map/components/app.json

uv run system-mapper graph /path/to/target \
  src/app.py docs/app.md config/app.yml \
  --component app \
  > .system-map/edges/app.jsonl

uv run system-mapper packet /path/to/target \
  src/app.py docs/app.md config/app.yml \
  --component app \
  > .system-map/packets/app.json
```

If noisy files are polluting a slice, `slice` supports exclusions:

```bash
uv run system-mapper slice /path/to/target src docs \
  --exclude 'dist/**' \
  --exclude 'node_modules/**' \
  --json
```

## Scenario: get a quick graph view

JSONL is the safest graph format for downstream tooling:

```bash
uv run system-mapper graph /path/to/target src --component src > .system-map/edges/src.jsonl
```

For visual review, render Mermaid or DOT:

```bash
uv run system-mapper graph /path/to/target src --component src --format mermaid
uv run system-mapper graph /path/to/target src --component src --format dot
```

To group connected graph nodes and get a subsystem-level view:

```bash
uv run system-mapper cluster .system-map/edges/src.jsonl --json
uv run system-mapper subsystem-summaries .system-map/edges/src.jsonl --json
uv run system-mapper architecture-brief .system-map/edges/src.jsonl
```

## Scenario: use a weak/local LLM worker

First generate a packet:

```bash
uv run system-mapper next /path/to/target --output-layout flat
```

If you omit `--llm-command`, `worker run` writes a prompt bundle for external processing instead of calling a model:

```bash
uv run system-mapper worker run \
  .system-map/packets/<slice>.json \
  --output .system-map/workers/<slice>.worker.json
```

To call a local command, pass the command that should read the prompt from stdin and write JSON to stdout:

```bash
uv run system-mapper worker run \
  .system-map/packets/<slice>.json \
  --llm-command 'ollama run qwen3:4b' \
  --output .system-map/workers/<slice>.worker.json
```

Then validate and import the claims:

```bash
uv run system-mapper validate \
  .system-map/workers/<slice>.worker.json \
  .system-map/packets/<slice>.json \
  --output .system-map/workers/<slice>.validated.json

uv run system-mapper claim import \
  .system-map/workers/<slice>.validated.json \
  --claim-store .system-map/claims.json
```

Inspect the store:

```bash
uv run system-mapper claim list --claim-store .system-map/claims.json
uv run system-mapper claim stats --claim-store .system-map/claims.json
uv run system-mapper claim conflicts --claim-store .system-map/claims.json
```

## Scenario: reject bad generated maps

Run the quality gate before trusting generated output:

```bash
uv run system-mapper quality .system-map/components/<slice>.json --min-score 0.8
```

For worker or validated output, include the original packet so evidence IDs can be checked:

```bash
uv run system-mapper quality \
  .system-map/workers/<slice>.validated.json \
  --evidence-source .system-map/packets/<slice>.json \
  --min-score 0.8 \
  --fail-on-garbage
```

A passing score does not prove the map is true. It only means the artifact is reviewable: claims cite evidence, citations resolve, high-confidence claims have stronger support, and uncertainty is visible.

## Scenario: update a previous summary after a code change

Use `update` when you have an old summary and a git diff:

```bash
git diff > /tmp/change.diff
uv run system-mapper update .system-map/components/<slice>.json /tmp/change.diff --json
```

Or pipe the diff through stdin:

```bash
git diff | uv run system-mapper update .system-map/components/<slice>.json - --json
```

`update` reports behaviour changes, possibly stale sources, and detected interface/route edge changes for supported patterns.

## Scenario: merge lower-level summaries

After mapping several slices, merge them upward:

```bash
uv run system-mapper merge \
  .system-map/components/api.json \
  .system-map/components/jobs.json \
  .system-map/components/docs.json \
  --component system \
  --claim-store .system-map/claims.json \
  --json > .system-map/system.json
```

Merge preserves conflicts and can enrich the result from the claim store.

## Command reference

Run `uv run system-mapper <command> --help` for exact arguments.

| Command | What it does |
| --- | --- |
| `inventory` | Classifies files as code, documents, config, or other. |
| `plan` | Plans bounded slices with token estimates and output locations. |
| `next` | Writes the next missing packet, component summary, and edge JSONL. |
| `slice` | Summarises selected files as one component. |
| `graph` | Emits dependency/data-flow/interface edges as JSONL, Mermaid, or DOT. |
| `cluster` | Clusters graph JSONL into connected subsystem/community groups. |
| `subsystem-summaries` | Produces subsystem summaries from graph clusters. |
| `architecture-brief` | Produces a short human-readable brief from graph edges. |
| `packet` | Builds a bounded low-context AI work packet. |
| `prompt` | Prints built-in prompt contracts for `slice`, `update`, or `worker`. |
| `worker run` | Generates a worker prompt bundle or calls an external LLM command. |
| `validate` | Validates worker claims against packet evidence IDs. |
| `claim import/list/stats/conflicts` | Manages the JSON claim store. |
| `quality` | Scores artifacts for evidence coverage and anti-garbage signals. |
| `update` | Compares a previous summary with a diff and reports stale/changed claims. |
| `merge` | Merges component summaries into a higher-level map. |
| `eval-create-benchmark` | Writes a sample benchmark question file. |
| `eval` | Evaluates map usefulness against benchmark questions. |

## Output layout and paths

By default, artifacts go under `.system-map/` inside the target root passed to `system-mapper` unless you pass `--output-root`. If `--output-root` is relative, it is resolved inside the target root. If it is absolute, that exact directory is used.

Use `--output-layout flat` when you want predictable top-level artifact folders:

```text
.system-map/
  components/<slice>.json
  edges/<slice>.jsonl
  packets/<slice>.json
  claims.json
```

Use `--output-layout 1-level` or `--output-layout 2-level` when mapping larger repositories where nested paths are easier to browse.

## Development

```bash
uv run pytest -q
```
