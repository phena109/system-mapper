# Competitive Learning Course

`system-mapper` should learn aggressively from adjacent code-understanding projects without drifting into a clone of any one of them. The current reference projects are:

- [Understand-Anything](https://github.com/Egonex-AI/Understand-Anything) — LLM-assisted repo/docs understanding with an explanatory graph/dashboard.
- [codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp) — fast local codebase graph/index exposed through MCP tools.

## Positioning decision

The important differentiation is not merely "codebase to graph". That capability is becoming table stakes.

`system-mapper` should be a **full-stack adaptive code-understanding system powered by weak/cheap LLM decomposition**:

1. **Weak/cheap LLM first** — decompose work so small/local/low-context models can produce useful, cited claims. Use strong or external LLMs when they cheaply answer a question, but do not require them for deterministic extraction, indexing, graph construction, validation, or quality scoring.
2. **Both backend and frontend** — provide durable machine-readable artifacts and agent-facing commands, while also exposing human-readable navigation, explanation, confidence, stale-claim, and change-review surfaces.
3. **Adaptive to new changes** — treat maps as living artifacts. Diffs, changed files, stale evidence, claim freshness, and uncertainty-aware remapping are core product responsibilities, not optional polish.

Other differences are secondary unless they strengthen one of these three pillars.

## What to learn from Understand-Anything

Understand-Anything is strongest as an explanatory/onboarding surface. It shows how a graph can teach instead of merely impressing.

Learn from its general approach:

- Make graph outputs immediately explorable by humans, not only machine-readable.
- Include plain-English summaries, layers, tours, and guided reading order.
- Support code, docs, and knowledge-base material, not only source files.
- Let LLMs add semantic explanations where static analysis alone is too thin.
- Review generated graphs for usefulness, not only schema validity.
- Treat localization and audience-specific explanations as product surfaces.

80% target for `system-mapper`:

- A user should be able to generate a map, open a human-facing view or report, and understand the main subsystems, important evidence, unknowns, and next reading path without manually reading JSON.
- LLM-written explanations should remain cited and validated rather than becoming free-form architecture prose.

Do not copy blindly:

- Avoid one-shot polished understanding that hides uncertainty.
- Avoid making expensive model calls for data that deterministic scanners can extract.
- Avoid making visualization the product if the underlying map cannot adapt and verify itself.

## What to learn from codebase-memory-mcp

codebase-memory-mcp is strongest as a fast local backend for agent code discovery.

Learn from its general approach:

- Prefer deterministic local extraction for structure, names, relationships, routes, and files.
- Make graph/query operations fast enough that agents use them instead of repeated broad reads.
- Expose agent-facing tools for search, trace/path, architecture overview, code snippet retrieval, and change detection.
- Persist graph state so every session does not restart from zero.
- Keep backend processing local and dependency-light where practical.
- Separate structural backend from LLM reasoning: the agent can translate questions into tool calls.

80% target for `system-mapper`:

- A user or agent should be able to query existing map artifacts for components, edges, evidence, snippets, stale claims, and likely next inspection targets without rerunning a full map.
- Deterministic backend commands should handle the non-LLM parts: inventory, planning, graph extraction, update detection, validation, quality scoring, claim storage, and merge.

Do not copy blindly:

- Do not optimize only for query speed while losing explanatory usefulness.
- Do not become only an MCP backend; system-mapper also needs human-facing front surfaces.
- Do not treat semantic search as a substitute for claim lifecycle and verification.

## LLM policy

Use the simplest tool that answers the question correctly:

1. **No LLM** for deterministic facts: file inventory, parsing, graph edges, diff detection, evidence ID validation, quality scoring, and artifact merging.
2. **Weak/cheap LLM** for bounded interpretation: turning a packet into cited claims, naming subsystems, explaining relationships, identifying likely unknowns, proposing next inspection questions.
3. **Stronger/easier LLM** when the answer is cheaper and clearer with model reasoning: summarizing competitor design lessons, comparing approaches, drafting explanations, or resolving ambiguous product direction.

Every LLM-produced project claim should either cite evidence, be marked as an assumption, or be routed to human review.

## Product course

Future improvements should bias toward delivering at least 80% of the useful capabilities of both reference projects while preserving system-mapper's differentiation:

- **Frontend 80%:** readable reports, graph navigation, subsystem tours, stale/unknown/confidence display, and change-review views.
- **Backend 80%:** persistent/queryable map artifacts, graph search and trace, architecture overview, snippets/evidence retrieval, change detection, and agent/MCP-friendly command surfaces.
- **Weak-agent advantage:** packet compression, slice control, evidence ledgers, validation, claim lifecycle, and quality-driven reruns.
- **Adaptation advantage:** map update after diffs, changed-file prioritization, stale claim detection, uncertainty-aware remapping, and scheduled dogfood improvement.

## Backlog themes

Use these as themes for bounded implementation tasks:

1. **Human-facing map browser/report** — HTML or static report over existing `.system-map` artifacts.
2. **Queryable backend** — commands or server surface for searching components, edges, evidence, claims, and snippets.
3. **Change adaptation** — stronger `update`, stale-claim lifecycle states, changed-file to affected-component routing, and remapping recommendations.
4. **Weak LLM efficiency** — smaller packets, prompt-size budgets, cacheable prompt prefixes, and cheap-model-friendly output schemas.
5. **Learning loop** — compare reference project capabilities periodically, translate useful ideas into tests/backlog items, and reject ideas that dilute the three positioning pillars.

## Non-goals

- Becoming a generic chatbot.
- Becoming only a graph visualizer.
- Becoming only a fast code index.
- Requiring expensive LLMs for ordinary mapping.
- Treating generated summaries as truth without evidence and quality checks.
