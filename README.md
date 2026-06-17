# system-mapper

`system-mapper` helps a low-context human or small/cheap LLM understand a repository one bounded slice at a time.

It does **not** try to produce a perfect architecture document in one pass. It plans small slices, extracts deterministic evidence from files, writes machine-readable summaries and graph edges, packages work for an LLM worker, validates claims against evidence IDs, and keeps uncertainty visible.

Current product course: learn aggressively from adjacent tools such as Understand-Anything and codebase-memory-mcp, aiming to deliver at least 80% of their useful explanatory frontend and graph-backend capabilities while preserving `system-mapper`'s own differentiation: weak/cheap LLM decomposition, both frontend and backend surfaces, and adaptation to new code changes. Deterministic work should stay deterministic; use an LLM when it answers the interpretation question more easily, but keep generated claims cited, validated, or explicitly uncertain. See [Competitive learning course](docs/competitive-learning-course.md).

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

To discard generated mapping artifacts for a project and start over, remove only the in-project `.system-map/` folder:

```bash
uv run system-mapper reset /path/to/target
```

`reset` intentionally targets `/path/to/target/.system-map/`; external output roots are not reset by this command.

For a simple repeat-until-done loop, capture the JSON output and stop when `no_change` appears:

```bash
TARGET=/path/to/target
while true; do
  uv run system-mapper next "$TARGET" --output-layout flat > /tmp/system-mapper-next.json
  cat /tmp/system-mapper-next.json
  python - <<'PY'
import json
from pathlib import Path
outcome = json.loads(Path('/tmp/system-mapper-next.json').read_text()).get('outcome')
raise SystemExit(0 if outcome == 'no_change' else 1)
PY
  if [ $? -eq 0 ]; then
    break
  fi
done
```

That loop is intentionally dumb: it only advances deterministic artifacts. Review summaries, run workers, validate claims, and run the quality gate as separate steps.

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

To ask a mapped repository a compact structural question without re-reading the whole codebase, query generated summaries and expand one graph hop around the matching components:

```bash
uv run system-mapper map-query /path/to/target "where is login session created?" --json
```

`map-query` searches `.system-map/components/*.json`, pulls in connected `.system-map/edges/*.jsonl` records, and emits an `answer_context` block with claims, evidence IDs, and related graph edges for a low-context human or LLM.

To persist architectural decisions alongside the map, use the ADR store:

```bash
uv run system-mapper adr add \
  --store /path/to/target/.system-map/architecture-decisions.json \
  --title "Keep map artifacts in repo" \
  --status accepted \
  --context "Team members and agents should skip rediscovery." \
  --decision "Commit compact generated map artifacts." \
  --consequences "Future workers can reuse previous understanding."
uv run system-mapper adr list --store /path/to/target/.system-map/architecture-decisions.json
```

## Scenario: use a weak/local LLM worker

First generate a packet:

```bash
uv run system-mapper next /path/to/target --output-layout flat
```

If you omit `--llm-command`, `worker run` writes a prompt bundle for external processing instead of calling a model. The bundle includes `_prompt_metrics` with rough character/token counts, a stable-contract vs per-slice packet size split, a cache-prefix hint, and a local-worker risk recommendation, so small-model handoffs can be narrowed before a prompt is wasted:

```bash
uv run system-mapper worker run \
  .system-map/packets/<slice>.json \
  --output .system-map/workers/<slice>.worker.json
```

To call a local command, pass the command that should read the prompt from stdin and write JSON to stdout. Add `--max-prompt-tokens` when using small/local models so oversized packets fail before the model is invoked:

```bash
uv run system-mapper worker run \
  .system-map/packets/<slice>.json \
  --llm-command 'ollama run qwen3:4b' \
  --max-prompt-tokens 8000 \
  --output .system-map/workers/<slice>.worker.json
```

If the generated prompt exceeds the budget, rerun `next` or `packet` with a narrower slice before spending a local worker call. The generated worker bundle includes `_prompt_metrics.largest_packet_sections` and a `_prompt_metrics.narrowing_hint` so a low-context operator can see whether `summary`, `evidence_ledger`, `edge_records`, or another packet section is dominating the prompt.

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

A passing score does not prove the map is true. It only means the artifact is reviewable: claims cite evidence, citations resolve, high-confidence claims have stronger support, and uncertainty is visible. When the gate fails, the JSON report includes `details` buckets such as `uncited_claims`, `claims_with_missing_evidence`, `unsupported_high_confidence_claims`, and `vague_claims` so a low-context reviewer can jump to the exact claims to fix instead of interpreting only aggregate scores.

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

## Dogfood run: system-mapper mapping itself

The README above is intentionally focused on the common usage path. For a fuller worked example, see [Dogfood run results](docs/dogfood-run.md). It shows real command output from running `system-mapper` against this repository, including inventory, slice planning, `next`, manual slice/graph/packet generation, graph clustering, architecture briefs, worker prompt generation, validation, claim import, quality checks, merge, update, prompts, and benchmark evaluation.

The key lesson from the current run: the tool is useful for orientation and evidence packaging, but its own quality gate flagged generated summaries below the default threshold. Treat generated maps as reviewable evidence bundles, not final truth.

## Agent skill

Agents and LLM coding assistants can load [`skills/system-mapper/SKILL.md`](skills/system-mapper/SKILL.md) for a compact operating guide covering the `next` loop, worker/validation flow, quality gate, merge/update workflows, and dogfooding expectations.

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
