# Autonomous improvement loop

`system-mapper` should improve by repeatedly combining three inputs:

1. **Self-reflection on this repository** — inspect current code, tests, CLI UX, docs, limitations, failure modes, and stale roadmap items.
2. **Internet research** — check current public practice around code intelligence, repository maps, knowledge graphs, RAG, static analysis, CI, and low-context agent workflows.
3. **Safe implementation** — choose one bounded improvement, implement it test-first when it changes behaviour, verify it, and push to the authorised branch according to the operator prompt.

This loop exists because the product is itself a living-system mapper: if it cannot continuously update its own understanding, it is not exercising the behaviour it promises.

## Original-intention guardrail

Rapid autonomous improvement is allowed only while preserving the original intention:

> Help low-power / low-context AI describe and maintain understanding of large living systems by divide-and-conquer over code, documentation, configuration, human/manual process artefacts, evidence, unknowns, and incremental changes.

Improvements should strengthen one or more of:

- bounded slice mapping;
- evidence-first summaries;
- source/document freshness;
- graph, dependency, interface, and data-flow maps;
- conflict and unknown preservation;
- low-context AI work packets;
- living-change update loops.

The project should not drift into a generic chatbot, generic RAG app, generic code search product, or unrelated developer assistant.

## Research signals from initial scan

The first research pass found these durable directions:

- **GraphRAG-style hierarchy**: Microsoft GraphRAG describes graph extraction, community hierarchy, and community summaries for private data. For this project, that supports moving from flat component summaries to graph/community summaries.
- **Incremental parsing**: Tree-sitter is designed for concrete syntax trees that can be updated efficiently as files change. For this project, that supports replacing regex-only extraction with parser-backed symbol/edge extraction where available.
- **Static analysis / CodeQL-style workflows**: GitHub CodeQL CLI documentation emphasises command-line scanning, custom queries, packs, and incremental analysis. For this project, that supports a plugin/query architecture rather than one monolithic heuristic scanner.
- **Prompt contracts and hallucination control**: GitHub's prompt-engineering guidance warns that LLMs can confidently produce false information. For this project, that supports evidence-first prompt templates, confidence levels, and conflict preservation.

## Improvement backlog

### Capability

1. **Parser-backed extraction**
   - Initial Python import dependency extraction now uses the stdlib AST to emit repository-local `internal` edges for bounded slices.
   - Python `from package import submodule` and relative `from . import helper` forms now resolve imported repository submodules instead of collapsing to only the package `__init__.py`.
   - Python entry-point extraction now uses the stdlib AST so async functions, classes, and methods are captured more reliably than regex-only scans.
   - Python bounded slices now emit deterministic `call` edges for local function, class-constructor, and method calls discovered with the stdlib AST, improving intra-file flow maps for low-context workers.
   - JavaScript/TypeScript bounded slices now emit repository-local `internal` edges for relative `import`, re-export, dynamic `import()`, and `require()` specifiers when they resolve to existing source files or index modules.
   - JavaScript/TypeScript entry-point extraction now recognises exported classes, exported functions, and common `const`/`let`/`var` function or arrow-function declarations so bounded slices expose TypeScript route/controller/helper entry points instead of only dependency edges.
   - JavaScript/TypeScript bounded slices now emit deterministic local `call` edges for calls to same-file functions, classes/constructors, and class-style methods, giving low-context workers a first intra-file flow map for frontend or Node slices.
   - Continue adding optional Tree-sitter or AST-based extractors for Python/JS/TS.
   - Continue extracting richer route declarations, decorators, SQL/table references, and config keys with source spans.
   - Initial edge source spans: external URL, data-store, cron-trigger, Python call, and Python/JavaScript/TypeScript internal dependency edges now carry deterministic `source_line` citations where detected so low-context workers can jump from map edges back to evidence.
   - Source-line graph edges now also receive dedicated evidence-ledger records (`*_edge` kinds) when a line citation is available, so edge claims can be audited through the same evidence-first contract as typed claims.
   - Python web-route decorators such as FastAPI/Flask-style `@router.get("/path")`, `@app.post("/path")`, and Flask-style `@app.route("/path", methods=[...])` now emit high-confidence `route` edges with source-line citations and HTTP methods, improving interface maps for API slices.
   - JavaScript/TypeScript Express-style route registrations such as `router.get('/maps/:mapId', handler)`, `app.post('/maps', handler)`, and `router.route('/maps/:mapId').delete(handler)` now emit deterministic `route` edges with source-line citations so Node/Express API slices expose interface maps too.
   - PHP-first C-like support now extracts PHP classes/functions/methods, PHP include/require internal dependency edges, PHP route-style calls, generic C-like call edges, and C/C++ quoted `#include` internal edges. Slice ordering gives PHP first priority among C-like languages because early target use cases are PHP-heavy.
   - Go bounded slices now extract `type` and `func` declarations (including methods) as entry points, emit deterministic same-file call edges for declared functions/methods, and resolve same-module imports from `go.mod` to repository-local `.go` files so Go services contribute explicit map edges without adding a parser dependency yet.

2. **Graph/community layer**
   - Store edges as JSONL.
   - Initial visual review support: `system-mapper graph --format mermaid` now renders bounded slice edges as a Mermaid flowchart while preserving JSONL as the machine-merge default.
   - Graphviz review support: `system-mapper graph --format dot` now renders the same bounded slice edges as DOT for local Graphviz tooling and documentation artefacts.
   - Generate component clusters from edges.
     - Initial implementation: `system-mapper cluster` groups graph JSONL edge records into deterministic connected subsystem/community summaries with nodes, edge kinds, participating components, hub nodes, and source-line evidence citations.
   - Emit subsystem-level summaries from lower-level summaries, preserving conflicts.

3. **Document freshness scoring**
   - Initial implementation: each evidence record now carries a deterministic truncated SHA-256 content revision, giving update workers a stable basis for source freshness/change checks.
   - Continue tracking source modified time and citation source where useful.
   - Mark docs stale when code/config cited by the doc changes.

4. **Prompt pack**
   - Maintain built-in prompts for slice analysis, summary merge, diff update, conflict review, and human-question generation.

5. **Change pipeline**
   - Given a git diff, map changed files to affected components.
   - Re-run slice summaries for only affected areas.
   - Produce changelog entries and stale-source warnings.
   - Initial interface-change support: `update` now detects added Python FastAPI/Flask-style route decorators in diffs and reports route edge changes so low-context workers can reinspect API-facing maps.

### Efficiency

1. **Content hashing**
   - Skip unchanged files and summaries.
   - Cache extraction results by file hash.

2. **Bounded context packaging**
   - Generate small AI work packets: selected files, previous summary, edge neighbourhood, related docs, and exact output contract.
   - Initial implementation: `system-mapper packet` emits a deterministic slice summary, prompt contract, edges, unknowns, and next actions as JSON for low-context worker handoff.

3. **Dependency-aware traversal**
   - Initial implementation: `system-mapper plan --strategy dependency-aware` prioritises edge-rich files from deterministic slice summaries before quieter supporting artefacts.
   - Planned slices now include a `rationale` field that records strategy, estimated tokens, and, for dependency-aware plans, edge/unknown density so low-context workers can understand why the slice was selected.
   - Continue suggesting next files by edge fan-out, unknowns, and stale evidence rather than folder order.

4. **CI/local smoke checks**
   - Keep deterministic CLI checks cheap so scheduled runs can execute often.

5. **Minimal repeatable runner**
   - Initial implementation: `system-mapper next` writes the next missing packet, component summary, and edge JSONL artifact under `.system-map/`, then returns `no_change` once all planned slices already have artifacts. This gives Hermes cron or a shell loop a tiny safe primitive that advances map artifacts without embedding scheduling or LLM calls in the package.

## Scheduled operator boundaries

A recurring autonomous run may:

- inspect this repo and public sources;
- create/update documentation;
- add low-risk tests and deterministic CLI functionality;
- commit and push directly to `main`/`master` after verification when the change preserves the original intention;
- report proposed next work.

A recurring autonomous run must not:

- drift away from the original system-mapping purpose;
- merge external branches;
- delete repository history;
- change licensing/legal claims;
- add paid services or credentials;
- make irreversible external changes;
- claim internet research proves user demand.

## Recommended cadence

During early rapid improvement, the Hermes operator runs hourly. That cadence should stay safe by keeping each run bounded, test-verified, and no-drift checked. Once the project stabilises, reduce cadence to daily or weekly to avoid churn.

## Operator checklist

Each run should report:

- checked scope and remote;
- sources researched, with URLs;
- self-reflection findings;
- chosen improvement and why it was bounded;
- files changed;
- tests/smoke checks run;
- branch/commit/push result;
- next recommended work.
