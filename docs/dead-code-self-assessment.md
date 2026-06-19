# Dead-code self-assessment

This project treats dead code as code that does not materially help deliver the current product direction, even when it is referenced, documented, or tested.

Current product direction, from `README.md`, is:

- impact analysis;
- better query/search over generated maps;
- more exact deterministic symbol extraction;
- weak/cheap-LLM divide-and-conquer packets, validation, and claim quality.

Polished UI/reporting and general product-management helpers are later unless they directly improve those loops.

## Assessment method

1. **Verify the target repo.** In Discord project channels, the writable checkout is the repo matching the channel name. For `#system-mapper`, that is `/home/douglas/projects/system-mapper`.
2. **Run the tool on itself.** Generate `.system-map` artifacts with `uv run system-mapper next . --output-layout flat` until `no_change`, then query the generated map for product-relevant modules.
3. **Cross-check with static evidence.** Inspect imports, CLI parser entries, README/skill references, tests, and generated graph edges. A module is not material merely because tests or docs mention it.
4. **Apply the product filter.** Keep features that support the near-term loops above. Remove features whose main value is polished narrative output, generic architecture-document convenience, or side stores not needed for mapping/query/impact/worker validation.
5. **Update docs with the new surface.** Remove commands and scenarios that no longer exist; keep dogfood notes honest when historical output refers to removed commands.
6. **Verify by execution.** Run the test suite and representative CLI commands after deletion.

## First purge decision

Removed as non-material to the current product loop:

- `architecture-brief`: a polished human-readable graph brief; graph JSONL, clustering, and subsystem summaries remain.
- `map-report`: an onboarding/report surface; map querying and impact analysis remain the near-term surfaces.
- `adr`: an architecture decision side-store; claim stores remain because they support worker validation and uncertainty-aware mapping.

Retained even if peripheral:

- `cluster` and `subsystem-summaries`, because they expose graph structure useful for query/search and impact analysis.
- `eval` and `quality`, because deterministic quality/evaluation protects low-context worker output from becoming unsupported prose.

## Reproduction commands

```bash
uv run system-mapper reset .
uv run system-mapper next . --output-layout flat
uv run system-mapper next . --output-layout flat
uv run system-mapper next . --output-layout flat
uv run system-mapper map-query . "Which modules materially support impact analysis, map-query, deterministic symbol extraction, and CLI delivery?" --json
uv run pytest -q
```
