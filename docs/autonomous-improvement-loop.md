# Autonomous Improvement Loop

This repository is improved by a scheduled scout/worker that makes one bounded, verified change at a time.

## Scope and non-drift rule

`system-mapper` exists to help low-context humans and LLM workers build evidence-backed living maps of large systems. Autonomous work must strengthen at least one of these surfaces:

- bounded slice planning and mapping;
- evidence-led summaries, claims, freshness, unknowns, and conflicts;
- graph/dependency/data-flow views;
- low-context worker packets and prompt contracts;
- living-change update and quality/evaluation workflows.

Do not turn the project into a generic chatbot, generic RAG application, generic code search tool, or unrelated developer assistant.

## Required run shape

1. Verify repository scope: `pwd`, `git remote -v`, `git status --short --branch`.
2. Start from clean, current `main`: `git pull --ff-only origin main`.
3. Read `README.md`, this runbook, and the most relevant docs/tests for the candidate change.
4. Self-reflect on dogfood findings, tests, CLI ergonomics, and living-map workflow bottlenecks.
5. Use public research for new direction or nontrivial capability work; keep sources in the final report.
6. Select one bounded improvement tied to the non-drift rule.
7. Use TDD for behavior changes: add the failing test, verify RED, implement, verify GREEN.
8. Run `uv run pytest -q` plus at least one relevant CLI smoke check.
9. If changes are safe and verified, commit and push directly to `main`.

## Current self-reflection backlog

These are prompts for future bounded runs, not approval to build all of them at once:

1. **Small/local LLM efficiency:** packet and worker flows should expose prompt-size risk, compression opportunities, or smaller-slice recommendations before calling a model.
2. **Subsystem discovery:** cluster and subsystem outputs should keep improving names, entrypoints, unknowns, and evidence citations so a low-context worker can inspect one subsystem at a time.
3. **Quality-driven reruns:** quality failures should point to the exact claim/evidence weakness and suggest narrower remapping where possible.
4. **Living-change workflow:** `update`, claim freshness, and uncertainty-aware planning should make stale claims easy to find after a diff.
5. **Evaluation realism:** benchmark examples should evolve from sample command coverage toward project-specific questions that test whether maps help a worker answer real maintenance questions.

## Recent research basis

Public provider documentation and announcements reinforce this runbook's bias toward explicit prompt-size and context-management signals before using a model:

- OpenAI API text generation documentation: https://platform.openai.com/docs/guides/text?api-mode=responses
- OpenAI prompt caching documentation: https://platform.openai.com/docs/guides/prompt-caching
- Anthropic prompt engineering overview: https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/overview
- Anthropic prompt caching documentation: https://platform.claude.com/docs/en/build-with-claude/prompt-caching.md
- Anthropic context windows documentation: https://platform.claude.com/docs/en/build-with-claude/context-windows.md
- Anthropic prompt caching announcement: https://www.anthropic.com/news/prompt-caching

Treat these sources as directional support for efficient bounded prompts, stable-prefix reuse, and explicit context-size management, not as product requirements.
