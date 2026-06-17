# Autonomous Improvement Loop

This repository is improved by a scheduled dogfood scout/worker that makes one bounded, verified change at a time. The schedule is operationally configurable and may change over time. The loop uses the quality-gate score as a persistent operational metric, recorded across runs in `docs/dogfood-quality-history.jsonl`, so each run can compare direction instead of treating quality as a one-off observation.

## Scope and non-drift rule

`system-mapper` exists to help low-context humans and LLM workers build evidence-backed living maps of large systems. Autonomous work must strengthen at least one of these surfaces:

- bounded slice planning and mapping;
- evidence-led summaries, claims, freshness, unknowns, and conflicts;
- graph/dependency/data-flow views;
- low-context worker packets and prompt contracts;
- living-change update and quality/evaluation workflows.

The current product course is to learn as much as practical from Understand-Anything and codebase-memory-mcp, with a target of delivering at least 80% of their useful general capabilities without copying their product identity. The non-negotiable differentiators are weak/cheap LLM decomposition, both frontend and backend surfaces, and adaptation to new changes. See `docs/competitive-learning-course.md` before selecting larger roadmap-shaped work.

Do not turn the project into a generic chatbot, generic RAG application, generic code search tool, or unrelated developer assistant.

## Required run shape

1. Verify repository scope: `pwd`, `git remote -v`, `git status --short --branch`.
2. Start from clean, current `main`: `git pull --ff-only origin main`.
3. Read `README.md`, this runbook, `docs/dogfood-run.md`, `docs/dogfood-quality-history.jsonl`, and the most relevant docs/tests for the candidate change.
4. Create a fresh temporary dogfood artifact root and run `system-mapper` against this repository enough to produce representative map artifacts.
5. Run the quality gate on the most representative generated/reviewed artifact(s), normally with `--min-score 0.8`, and capture score, pass/fail, artifact path, commit, and timestamp.
6. Append one JSONL record per scored artifact to `docs/dogfood-quality-history.jsonl`. Keep old records intact.
7. Compare the latest representative score with the previous representative score and choose direction:
   - **Score decreased or stayed the same:** prioritize a bounded, progressive score-recovery objective likely to make maps more reviewable, such as stronger evidence packaging, clearer citations, lower overconfidence, smaller packets, better claim wording, or more actionable quality failure reports.
   - **Score increased:** broaden to safe self-improvement work such as documentation/code/comment polish, experimental but narrow ideas, minor-situation coverage, tests, benchmark questions, dogfood ergonomics, or other maintainability improvements.
   - **No comparable previous score:** treat the run as baseline creation, then make a small safe improvement that improves future comparability.
8. Do not change quality-gate metric calculations merely to improve the score. Only change metric logic when inspection proves the metric is misleading, and justify that separately from score movement.
9. Select one bounded improvement tied to the non-drift rule and the score-aware direction above.
10. Use TDD for behavior changes: add the failing test, verify RED, implement, verify GREEN.
11. Run `uv run pytest -q` plus at least one relevant CLI smoke check. Re-run the relevant dogfood/quality command if the change affects generated artifacts or quality output.
12. If changes are safe and verified, commit and push directly to `main`.
13. Report previous score, latest score, selected direction, files changed, verification output, commit/push result, and next recommended objective.

## Current self-reflection backlog

These are prompts for future bounded runs, not approval to build all of them at once:

1. **Small/local LLM efficiency:** packet and worker flows should expose prompt-size risk, compression opportunities, or smaller-slice recommendations before calling a model.
2. **Subsystem discovery:** cluster and subsystem outputs should keep improving names, entrypoints, unknowns, and evidence citations so a low-context worker can inspect one subsystem at a time.
3. **Quality-driven reruns and score recovery:** quality failures should point to the exact claim/evidence weakness and suggest narrower remapping where possible. When the persisted representative score decreases or stays flat, prioritize progressive improvements that make the next score more likely to recover without gaming the metric.
4. **Living-change workflow:** `update`, claim freshness, and uncertainty-aware planning should make stale claims easy to find after a diff.
5. **Evaluation realism:** benchmark examples should evolve from sample command coverage toward project-specific questions that test whether maps help a worker answer real maintenance questions.
6. **Competitive learning:** periodically compare against Understand-Anything's explanatory/frontend strengths and codebase-memory-mcp's deterministic backend/query strengths; convert useful gaps into bounded tasks that preserve the weak/cheap-LLM and living-map positioning.

## Recent research basis

Public provider documentation and announcements reinforce this runbook's bias toward explicit prompt-size and context-management signals before using a model:

- OpenAI API text generation documentation: https://platform.openai.com/docs/guides/text?api-mode=responses
- OpenAI prompt caching documentation: https://platform.openai.com/docs/guides/prompt-caching
- Anthropic prompt engineering overview: https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/overview
- Anthropic prompt caching documentation: https://platform.claude.com/docs/en/build-with-claude/prompt-caching.md
- Anthropic context windows documentation: https://platform.claude.com/docs/en/build-with-claude/context-windows.md
- Anthropic prompt caching announcement: https://www.anthropic.com/news/prompt-caching

Treat these sources as directional support for efficient bounded prompts, stable-prefix reuse, and explicit context-size management, not as product requirements.
