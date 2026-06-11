# Dogfood run results

This page shows a real `system-mapper` run against the `system-mapper` repository itself. The point is not to present the generated output as perfect truth. The point is to show what the tool actually produces across its main capabilities, where it helps, and where the quality gate warns that the output still needs review.

Run location used for this pass:

```text
/tmp/system-mapper-dogfood-full-1781194217
```

Repository state used for this pass:

```text
system-mapper repository, after the single-README usage rewrite
```

The repeated `uv` warning about `VIRTUAL_ENV` being ignored is omitted from most excerpts below because it is environment noise, not `system-mapper` output.

## 1. Inventory

Command:

```bash
uv run system-mapper inventory . --json > "$ROOT/inventory.json"
```

Summary of the JSON result:

```text
{'items': 25}
```

What this demonstrates:

- `inventory` walks the target root and classifies discovered files.
- This is a low-risk first check before planning slices.
- In this repository, it found 25 inventory items.

## 2. Planning bounded slices

Command:

```bash
uv run system-mapper plan . \
  --output-root "$ROOT" \
  --output-layout flat \
  --json > "$ROOT/plan.json"
```

Excerpt:

```text
slice_count 2
README 44638 15
src/system_mapper 25919 7
```

What this demonstrates:

- `plan` split the repository into two bounded slices under the default 45,000-token budget.
- The first slice combined README/test/CLI-adjacent material.
- The second slice focused on source modules under `src/system_mapper`.
- The output includes estimated token counts, selected paths, rationale, and output locations for packet, summary, and edge files.

## 3. Advancing the map with `next`

Commands:

```bash
uv run system-mapper next . --output-root "$ROOT" --output-layout flat > "$ROOT/next1.json"
uv run system-mapper next . --output-root "$ROOT" --output-layout flat > "$ROOT/next2.json"
uv run system-mapper next . --output-root "$ROOT" --output-layout flat > "$ROOT/next3.json"
```

Excerpt:

```text
next1.json advanced README None
next2.json advanced src/system_mapper None
next3.json no_change None all planned slices already have packet, summary, and edge artifacts
components ['readme.json', 'src-system-mapper.json']
```

What this demonstrates:

- `next` writes the next missing summary, edge file, and packet.
- Re-running it advances through planned slices.
- Once all planned artifacts exist, it reports `no_change` instead of rewriting them.

## 4. Manual component mapping: `slice`, `graph`, and `packet`

Commands:

```bash
uv run system-mapper slice . \
  src/system_mapper/cli.py src/system_mapper/runner.py \
  --component cli-runner \
  --json > "$ROOT/manual/cli-runner.summary.json"

uv run system-mapper graph . \
  src/system_mapper/cli.py src/system_mapper/runner.py \
  --component cli-runner \
  > "$ROOT/manual/cli-runner.edges.jsonl"

uv run system-mapper graph . \
  src/system_mapper/cli.py src/system_mapper/runner.py \
  --component cli-runner \
  --format mermaid > "$ROOT/manual/cli-runner.mmd"

uv run system-mapper graph . \
  src/system_mapper/cli.py src/system_mapper/runner.py \
  --component cli-runner \
  --format dot > "$ROOT/manual/cli-runner.dot"

uv run system-mapper packet . \
  src/system_mapper/cli.py src/system_mapper/runner.py \
  --component cli-runner \
  > "$ROOT/manual/cli-runner.packet.json"
```

Excerpt from the manual summary:

```text
summary keys ['business_rules', 'claims', 'component', 'confidence', 'conflicts', 'edges', 'entry_points', 'evidence', 'evidence_ledger', 'human_steps', 'inputs', 'outputs']
claims 7 unknowns 1 edges 28
first edge {'confidence': 'medium', 'kind': 'call', 'source': 'src/system_mapper/cli.py', 'source_line': 42, 'target': 'src/system_mapper/cli.py:emit'}
```

What this demonstrates:

- `slice` creates a component summary with claims, evidence, unknowns, entry points, and confidence.
- `graph` emits machine-readable JSONL by default and can render Mermaid or DOT for visual review.
- `packet` packages a bounded handoff for a low-context worker.
- Source-line citations are present for at least some call edges.

## 5. Graph clustering, subsystem summaries, and architecture brief

Commands:

```bash
uv run system-mapper cluster "$ROOT/edges/src-system-mapper.jsonl" --json > "$ROOT/cluster.json"
uv run system-mapper subsystem-summaries "$ROOT/edges/src-system-mapper.jsonl" --json > "$ROOT/subsystems.json"
uv run system-mapper architecture-brief "$ROOT/edges/src-system-mapper.jsonl" > "$ROOT/architecture-brief.txt"
```

Excerpt:

```text
clusters 2 nodes None edges None
subsystems 2 ['src', 'src']
['Likely entry point: src/system_mapper/runner.py', '', 'Top file-to-file relationships:', '  src/system_mapper/planner.py → src/system_mapper/inventory.py  (weight=1, kinds=internal)', '  src/system_mapper/planner.py → src/system_mapper/summarizer.py  (weight=1, kinds=internal)', '  src/system_mapper/quality.py → src/system_mapper/claims.py  (weight=1, kinds=internal)', '  src/system_mapper/runner.py → src/system_mapper/packet.py  (weight=1, kinds=internal)', '  src/system_mapper/runner.py → src/system_mapper/planner.py  (weight=1, kinds=internal)']
```

What this demonstrates:

- `cluster` groups connected graph records.
- `subsystem-summaries` attempts to name and describe those connected groups.
- `architecture-brief` produces a readable orientation summary from graph edges.

What needs improvement:

- The subsystem names here were both `src`, which is not very useful. That is a good dogfood signal: subsystem naming needs better heuristics.
- The cluster excerpt printed `nodes None edges None` because the quick summarizer script looked for top-level `node_count` / `edge_count` keys that were not present in that shape. The cluster file itself still contained detailed cluster records. This suggests the output shape should be easier to skim.

## 6. Worker prompt generation without calling an LLM

Command:

```bash
uv run system-mapper worker run \
  "$ROOT/packets/src-system-mapper.json" \
  --output "$ROOT/workers/src.worker.json"
```

Excerpt:

```text
prompt_generated src/system_mapper 129516 0
```

What this demonstrates:

- Without `--llm-command`, `worker run` does not call a model.
- It writes a prompt bundle and packet for external processing.
- The generated prompt was 129,516 characters for this slice.
- The initial `claims` array is empty because no LLM worker was called.

What needs improvement:

- For small/local LLMs, a 129k-character prompt is too large. The planner kept estimated tokens under budget, but actual prompt size is still a practical issue. This is a useful signal for future packet compression or smaller slice planning.

## 7. Validation and claim-store import

For this dogfood run, a tiny synthetic worker output was created using a real evidence ID from the generated packet. This tests the validation and claim-store path without pretending an LLM produced the claim.

Commands:

```bash
uv run system-mapper validate \
  "$ROOT/workers/synthetic.worker.json" \
  "$ROOT/packets/src-system-mapper.json" \
  --output "$ROOT/workers/synthetic.validated.json"

uv run system-mapper claim import \
  "$ROOT/workers/synthetic.validated.json" \
  --claim-store "$ROOT/claims.json" \
  --component src-system-mapper

uv run system-mapper claim list --claim-store "$ROOT/claims.json"
uv run system-mapper claim stats --claim-store "$ROOT/claims.json"
uv run system-mapper claim conflicts --claim-store "$ROOT/claims.json"
```

Excerpt:

```text
accepted 1
import {'duplicate': 0, 'new': 1, 'updated': 0}
stats {'accepted': 1, 'conflicted': 0, 'hypothesis': 0, 'needs_review': 0, 'rejected': 0, 'stale': 0, 'total': 1}
```

What this demonstrates:

- `validate` can accept claims that cite valid packet evidence IDs.
- `claim import` writes accepted claims into a JSON claim store.
- `claim stats` gives a compact status summary.
- This path is useful even when the worker is a human or external process rather than an automatically called model.

## 8. Quality gate

Commands:

```bash
uv run system-mapper quality "$ROOT/components/src-system-mapper.json" --min-score 0.8 > "$ROOT/quality.src.json"

uv run system-mapper quality \
  "$ROOT/workers/synthetic.validated.json" \
  --evidence-source "$ROOT/packets/src-system-mapper.json" \
  --min-score 0.8 > "$ROOT/quality.synthetic.json"
```

Excerpt:

```text
quality.src.json False 0.776 ['anti_garbage_score 0.776 is below 0.800', 'high_confidence_support is below 1.0; high-confidence claims need multiple valid citations and precise wording']
quality.synthetic.json False 0.778 ['anti_garbage_score 0.778 is below 0.800', 'high_confidence_support is below 1.0; high-confidence claims need multiple valid citations and precise wording']
```

What this demonstrates:

- The quality gate is doing useful work: it refused to bless generated outputs just because they were structured JSON.
- The failures focused on high-confidence support and anti-garbage score.
- Generated maps should be treated as orientation until reviewed or improved enough to pass.

What needs improvement:

- The synthetic validated output had a medium-confidence claim, but the quality gate still reported high-confidence support problems after merging evidence from the packet. The quality scoring may need clearer reporting about which claims triggered the high-confidence-support failure.

## 9. Merge, update, and benchmark evaluation

Commands:

```bash
uv run system-mapper merge \
  "$ROOT/components/readme.json" \
  "$ROOT/components/src-system-mapper.json" \
  --component system-mapper \
  --claim-store "$ROOT/claims.json" \
  --json > "$ROOT/merged.system-mapper.json"

git diff HEAD~1..HEAD -- README.md > "$ROOT/readme-change.diff"

uv run system-mapper update \
  "$ROOT/components/readme.json" \
  "$ROOT/readme-change.diff" \
  --json > "$ROOT/update.readme.json"

uv run system-mapper eval-create-benchmark --output "$ROOT/eval/sample.json"

uv run system-mapper eval \
  "$ROOT/eval/sample.json" \
  --mode mapped \
  --system-map "$ROOT/merged.system-mapper.json" \
  > "$ROOT/eval/report.json"
```

Excerpt:

```text
merged component system-mapper claims 75 conflicts 0
update keys ['behaviour_changes', 'changed_files', 'changelog_entry', 'component', 'downstream_to_reinspect', 'edge_changes', 'interface_changes', 'possibly_stale_sources', 'stale_claims', 'unknowns']
benchmark report keys ['improvement', 'mapped_correct', 'mapped_results', 'raw_correct', 'raw_results', 'total_questions']
```

What this demonstrates:

- `merge` combines lower-level summaries into an upward map and can use a claim store.
- `update` compares a previous summary with a diff and reports stale or changed areas.
- `eval-create-benchmark` and `eval` provide a benchmark harness shape for comparing mapped vs raw usefulness.

What needs improvement:

- The benchmark used here was only the generated sample benchmark, not a serious project-specific evaluation. It proves the command path works, not that the map is useful for real questions.

## 10. Built-in prompt contracts

Commands:

```bash
uv run system-mapper prompt worker > "$ROOT/prompt.worker.txt"
uv run system-mapper prompt slice --component cli-runner > "$ROOT/prompt.slice.txt"
```

Excerpt:

```text
prompt.worker.txt 2125 You are a low-context system-mapping worker.
prompt.slice.txt 1457 You are mapping a large legacy/living system using divide and conquer.
```

What this demonstrates:

- `prompt worker` exposes the strict worker contract.
- `prompt slice` exposes a reusable slice-analysis prompt.
- These are useful when a human or orchestration system wants to call an LLM outside `system-mapper worker run`.

## Overall findings from this dogfood pass

Useful now:

- `next` gives a simple repeatable mapping loop.
- Manual `slice` / `graph` / `packet` commands work for targeted components.
- Graph and architecture brief outputs are good orientation aids.
- Worker prompt generation, validation, and claim-store import form a usable low-context worker pipeline.
- The quality gate catches overconfident or weakly supported generated output.

Needs improvement:

- Subsystem names can be too generic (`src`, `src`).
- Worker packets can become too large for weak/local LLMs even when planned slices fit the rough token budget.
- Quality-gate failures need more actionable pinpointing of the exact offending claims.
- Benchmark evaluation needs real project-specific questions to prove usefulness.
- Cluster output should be easier to skim from the top-level JSON shape.

Recommended next documentation/product scenarios to add later:

1. **Small local LLM mode** — compress packets or choose smaller slices before `worker run`.
2. **Subsystem discovery mode** — graph a folder, cluster it, then inspect each cluster.
3. **Quality-driven improvement loop** — fail low-quality maps, inspect reported weakness, rerun a smaller slice.
4. **Change review mode** — use `update` on a diff, then run `next --strategy uncertainty-aware` with a claim store.
5. **Evaluation mode** — write real benchmark questions for a target repo and compare raw vs mapped answers.
