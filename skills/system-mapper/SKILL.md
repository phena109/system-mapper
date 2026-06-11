---
name: system-mapper
description: Use when an LLM or agent needs to map, understand, update, or quality-check a repository using the system-mapper CLI. Covers the scenario-first workflow, next-loop usage, worker packets, validation, claims, quality gates, and dogfooding expectations.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [system-mapping, code-understanding, low-context-agents, documentation, quality]
    related_skills: []
---

# system-mapper Agent Skill

## Overview

Use `system-mapper` to help low-context humans or LLM workers understand a repository one bounded slice at a time. Do not ask a model to read an entire large repo and invent a polished architecture story. Instead, let the CLI produce bounded evidence packages, graph edges, worker packets, and quality reports.

Core loop:

```text
plan a slice -> write summary/edges/packet -> worker makes cited claims -> validate -> import claims -> inspect next slice
```

The generated map is an evidence bundle, not final truth. Treat claims as reviewable only when they cite evidence IDs and pass the quality gate.

## When to Use

Use this skill when:

- You need to understand an unfamiliar repository or subsystem.
- You need to give a weak/local/low-context LLM a bounded work packet.
- You want source-backed claims instead of a one-shot architecture summary.
- You need to update a previous map after a code or docs diff.
- You need to dogfood `system-mapper` on itself or another target repo.
- You need to reject vague or unsupported generated summaries.

Do not use it when:

- The user only asks for a simple file search or a single known fact.
- You cannot access the repository locally.
- You need runtime/prod facts that are not represented in code/docs/config; preserve those as unknowns or ask a human.

## First Run: Map a Repo with `next`

From the `system-mapper` checkout, run against the target repo or folder:

```bash
uv run system-mapper next /path/to/target --output-layout flat
```

Open the artifact paths printed in the JSON result:

1. `summary` — inspected scope, evidence, claims, unknowns, confidence.
2. `edges` — dependency/call/route/data-store/trigger/external-system JSONL edges.
3. `packet` — bounded handoff for a low-context worker.

Run the same command again to advance to the next planned slice. Stop when the result has:

```json
{"outcome": "no_change"}
```

## Repeat-Until-Done Loop

Use this for deterministic artifact generation only. Do not treat it as an autonomous reasoning loop by itself.

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

After generating artifacts, review summaries, run workers if needed, validate claims, and run quality checks separately.

## Inspect the Plan First

Use `plan` before writing artifacts when you want to understand or review the proposed slices:

```bash
uv run system-mapper plan /path/to/target \
  --strategy breadth-first \
  --token-limit 45000 \
  --output-root .system-map \
  --output-layout flat \
  --json
```

Strategies:

- `breadth-first` — default; shallow system shape first.
- `depth-first` — go deeper in one area.
- `chronological` — order by file metadata.
- `dependency-aware` — prioritise connected files.
- `uncertainty-aware` — available on `next`; uses claim/uncertainty signals.

## Manual Component Mapping

Use manual commands when you already know the file set for a component:

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

If noisy files pollute the summary, use exclusions:

```bash
uv run system-mapper slice /path/to/target src docs \
  --exclude 'dist/**' \
  --exclude 'node_modules/**' \
  --json
```

## Graph and Subsystem Review

Generate graph edges:

```bash
uv run system-mapper graph /path/to/target src --component src > .system-map/edges/src.jsonl
```

Render visual formats:

```bash
uv run system-mapper graph /path/to/target src --component src --format mermaid
uv run system-mapper graph /path/to/target src --component src --format dot
```

Cluster and summarize graph relationships:

```bash
uv run system-mapper cluster .system-map/edges/src.jsonl --json
uv run system-mapper subsystem-summaries .system-map/edges/src.jsonl --json
uv run system-mapper architecture-brief .system-map/edges/src.jsonl
```

Watch for weak subsystem names such as `src`; treat them as signals to improve naming or inspect clusters manually.

## Worker, Validation, and Claim Store

Generate a worker prompt bundle without calling a model:

```bash
uv run system-mapper worker run \
  .system-map/packets/<slice>.json \
  --output .system-map/workers/<slice>.worker.json
```

Call a local model command only if it can read the prompt from stdin and write JSON to stdout:

```bash
uv run system-mapper worker run \
  .system-map/packets/<slice>.json \
  --llm-command 'ollama run qwen3:4b' \
  --output .system-map/workers/<slice>.worker.json
```

Validate and import claims:

```bash
uv run system-mapper validate \
  .system-map/workers/<slice>.worker.json \
  .system-map/packets/<slice>.json \
  --output .system-map/workers/<slice>.validated.json

uv run system-mapper claim import \
  .system-map/workers/<slice>.validated.json \
  --claim-store .system-map/claims.json
```

Inspect the claim store:

```bash
uv run system-mapper claim list --claim-store .system-map/claims.json
uv run system-mapper claim stats --claim-store .system-map/claims.json
uv run system-mapper claim conflicts --claim-store .system-map/claims.json
```

## Quality Gate

Always run quality checks before relying on generated output:

```bash
uv run system-mapper quality .system-map/components/<slice>.json --min-score 0.8
```

For worker or validation output, include the original packet so evidence IDs resolve:

```bash
uv run system-mapper quality \
  .system-map/workers/<slice>.validated.json \
  --evidence-source .system-map/packets/<slice>.json \
  --min-score 0.8 \
  --fail-on-garbage
```

A passing quality score does not prove truth. It only says the artifact is structured enough to review. A failing score means do not rely on it without human inspection or a smaller/better slice.

## Change and Merge Workflows

Update a previous summary against a diff:

```bash
git diff | uv run system-mapper update .system-map/components/<slice>.json - --json
```

Merge lower-level summaries upward:

```bash
uv run system-mapper merge \
  .system-map/components/api.json \
  .system-map/components/jobs.json \
  .system-map/components/docs.json \
  --component system \
  --claim-store .system-map/claims.json \
  --json > .system-map/system.json
```

Use `update` to find stale areas and `merge` to preserve lower-level conflicts instead of hiding them.

## Dogfooding Expectations

When improving `system-mapper` itself:

1. Run `system-mapper` against this repository.
2. Record the run date, target commit hash, and artifact root in `docs/dogfood-run.md`.
3. Show excerpts from actual outputs, not invented examples.
4. Include failures and limitations, especially quality-gate failures.
5. Compare generated findings against source code before documenting them.
6. Turn dogfood pain into product backlog or docs improvements.

## Common Pitfalls

1. **Trusting generated summaries as final truth.** They are evidence bundles. Run `quality` and review citations.
2. **Skipping the packet/validation path for LLM claims.** Worker claims must cite packet evidence IDs and pass `validate`.
3. **Letting packets get too large for local models.** If worker prompts are huge, choose smaller slices or manually map narrower components.
4. **Assuming graph clusters have good names.** Cluster output may need manual naming review.
5. **Using sample eval as proof of usefulness.** `eval-create-benchmark` proves the command path, not real benchmark quality. Write project-specific questions.
6. **Forgetting output-root semantics.** Relative `--output-root` is resolved inside the target root; absolute paths are used as-is.

## Verification Checklist

- [ ] `uv run system-mapper next <target> --output-layout flat` produced summary, edges, and packet paths.
- [ ] Important claims cite evidence IDs or source lines.
- [ ] `quality` was run before relying on output.
- [ ] LLM worker output was validated against its packet.
- [ ] Accepted claims were imported only after validation.
- [ ] Unknowns and conflicts were preserved, not rewritten into confident prose.
- [ ] Dogfood docs include date, commit hash, artifact root, and real output excerpts.
