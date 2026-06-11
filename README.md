# system-mapper

`system-mapper` is a divide-and-conquer reasoning system for helping weak or low-context LLMs understand large software systems.

Large codebases are too big, noisy, and contradictory for small models to understand in one pass. `system-mapper` breaks the work into bounded inspection packets, gathers deterministic evidence from code/docs/config, asks small LLM workers to produce evidence-backed claims, validates those claims, and recursively merges them into a living system map.

The goal is not to generate a perfect architecture diagram. The goal is to slowly build a trustworthy, updateable model of what the system does, what is known, what is uncertain, what conflicts, and what should be inspected next.

## Core Loop

```text
packet -> LLM worker -> evidence-backed claims -> validation -> claim store -> recursive merge -> next investigation
```

## Key Concepts

| Concept     | Meaning                                                                               |
| ----------- | ------------------------------------------------------------------------------------- |
| Evidence    | What the tool directly observed from files, docs, config, diffs, or runtime artifacts |
| Claim       | A statement derived from evidence                                                     |
| Hypothesis  | A possible interpretation that is not fully proven                                    |
| Unknown     | Something the system knows it does not know                                           |
| Conflict    | Two or more claims that cannot all be true                                            |
| Stale claim | A previously accepted claim whose evidence changed                                    |
| Confidence  | How safe the claim is to rely on                                                      |
| Next action | The next slice, file, question, or human check required                               |

## Current Capabilities

- `inventory`: classify files as code, document, config, or other
- `slice`: produce a bounded component summary with evidence, claims, edges, unknowns (including Python/JS/TS/PHP/Go/Ruby/Rust code-entry and dependency/call extraction plus Python/JS/TS/PHP/Java/C# route/interface extraction)
- `update`: compare a previous JSON summary with a git diff and report changes, stale claims, and added Python/Express route interfaces
- `merge`: recursively merge lower-level summaries while preserving claims and conflicts
- `graph`: emit dependency/data-flow edges as JSONL, Mermaid, or DOT
- `cluster`: group graph edges into connected subsystem communities
- `subsystem-summaries`: emit subsystem-level summaries (probable name, entrypoints, data stores, external systems, routes, unknowns, claims to review) from clustered edges
- `architecture-brief`: produce a human-readable architecture brief from graph edges
- `packet`: package a bounded low-context AI work packet as JSON
- `plan`: choose bounded next slices with token limits and ordering strategies
- `next`: write the next missing packet, summary, and edge artifacts
- `prompt`: emit reusable low-context AI prompt contracts
- `worker run`: run a weak LLM over a packet and produce evidence-backed claims
- `validate`: validate worker output against the packet's evidence
- `claim import/list/manage`: store, query, and merge validated claims
- `quality`: score map artifacts with measurable anti-garbage checks

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

## Quick start: map something useful

```bash
# Plan the investigation
uv run system-mapper plan /path/to/repo --json > .system-map/slice-plan.json

# Run the investigation loop
uv run system-mapper next /path/to/repo --output-layout flat

# Run a weak LLM worker on the next packet
uv run system-mapper worker run .system-map/packets/next.json --model qwen3-4b

# Validate the worker's claims
uv run system-mapper validate .system-map/workers/next.worker.json .system-map/packets/next.json

# Import accepted claims into the claim store
uv run system-mapper claim import .system-map/workers/next.validated.json

# Fail the map if it looks like uncited/vague generated garbage
uv run system-mapper quality .system-map/workers/next.validated.json \
  --evidence-source .system-map/packets/next.json \
  --min-score 0.8 \
  --fail-on-garbage

# Choose the next slice based on investigation value
uv run system-mapper next /path/to/repo --strategy uncertainty-aware
```

## Intended workflow

```text
1. Inventory code + documents + operational artefacts.
2. Select bounded slices: file, folder, module, subsystem.
3. Summarise each slice with evidence, unknowns, confidence, and graph edges.
4. Run a weak LLM worker over the packet to produce evidence-backed claims.
5. Validate the worker's claims against the packet evidence.
6. Import accepted claims into the durable claim store.
7. Merge claims upward into higher-level maps, preserving conflicts.
8. Choose the next best slice to inspect based on uncertainty, staleness, centrality.
9. When code/docs change, update stale claims and reinspect affected slices.
```

## Development

```bash
uv run pytest -q
```
