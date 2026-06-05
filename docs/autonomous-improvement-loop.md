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
   - Python entry-point extraction now uses the stdlib AST so async functions, classes, and methods are captured more reliably than regex-only scans.
   - Continue adding optional Tree-sitter or AST-based extractors for Python/JS/TS.
   - Extract function calls, classes, route declarations, decorators, SQL/table references, and config keys with source spans.

2. **Graph/community layer**
   - Store edges as JSONL.
   - Generate component clusters from edges.
   - Emit subsystem-level summaries from lower-level summaries, preserving conflicts.

3. **Document freshness scoring**
   - Track source revision, modified time, and citation source.
   - Mark docs stale when code/config cited by the doc changes.

4. **Prompt pack**
   - Maintain built-in prompts for slice analysis, summary merge, diff update, conflict review, and human-question generation.

5. **Change pipeline**
   - Given a git diff, map changed files to affected components.
   - Re-run slice summaries for only affected areas.
   - Produce changelog entries and stale-source warnings.

### Efficiency

1. **Content hashing**
   - Skip unchanged files and summaries.
   - Cache extraction results by file hash.

2. **Bounded context packaging**
   - Generate small AI work packets: selected files, previous summary, edge neighbourhood, related docs, and exact output contract.
   - Initial implementation: `system-mapper packet` emits a deterministic slice summary, prompt contract, edges, unknowns, and next actions as JSON for low-context worker handoff.

3. **Dependency-aware traversal**
   - Suggest next files by edge fan-out, unknowns, and stale evidence rather than folder order.

4. **CI/local smoke checks**
   - Keep deterministic CLI checks cheap so scheduled runs can execute often.

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
