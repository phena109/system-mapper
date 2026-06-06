# Interpreting system maps

`system-mapper` outputs are evidence packages, not final truth. This page explains how to judge whether an output is useful and what to do with weak claims.

## Trust hierarchy

Use this rough trust order when reading summaries:

1. **Observed code/config evidence with exact files and lines** — strongest, but still may miss runtime behaviour.
2. **Current documentation that matches code/config evidence** — strong enough for a working claim.
3. **Detected names, symbols, URLs, cron strings, table-like constants, imports, and calls** — useful hints, not necessarily intent.
4. **Manual/process language in docs or comments** — important, but often stale or incomplete.
5. **Inferences from filenames and folder structure** — weak; keep as hypotheses.
6. **Missing evidence** — should become an unknown, not a guessed answer.

## Strong vs weak claims

A strong claim names its evidence:

> `src/billing.py` calls `https://partner.example/export` from `export_invoice`, so this slice appears to export invoices to a partner API.

A weak claim admits uncertainty:

> `docs/billing.md` says manual retry may be required, but no runbook or retry code was inspected. Human confirmation is needed.

A bad claim hides the gap:

> Billing has a complete manual retry process.

The last statement may be true, but the inspected evidence did not prove it.

## How to handle unknowns

Unknowns are one of the main outputs. Treat them as a queue:

- **Inspect next** when another file or folder is likely to answer it.
- **Ask a human** when the answer depends on current operations, ownership, or intent.
- **Mark stale** when docs and code appear to disagree.
- **Defer** when the unknown is not important for the current mapping goal.

Do not erase an unknown just because it is awkward. A preserved unknown is better than a confident hallucination.

## Confidence fields

Confidence values are local to the inspected slice. `high` does not mean “globally complete”; it means the current evidence was relatively direct for that field.

Before using a summary for decisions, ask:

- What files were in `scope`?
- What important files were not inspected?
- Are docs and code telling the same story?
- Are external systems, data stores, triggers, or manual steps only hinted at?
- Which unknowns would change a decision if answered?

## Edges and graphs

Edges are directional hints about relationships such as:

- internal repository dependencies;
- calls between detected local symbols;
- external URLs or systems;
- data stores;
- triggers.

Use JSONL for mergeable machine output. Use Mermaid only for fast visual review.

When an edge has `source_line`, prefer jumping to that line before trusting it. When an edge lacks a source line, treat it as a lower-confidence extracted hint.

## Merge discipline

When combining slice summaries into a larger system map:

1. merge claims only from cited lower-level summaries;
2. keep conflicting claims side by side;
3. preserve the source slice and file evidence for every important claim;
4. copy unknowns upward if they affect the parent system;
5. avoid smoothing uncertainty into polished prose.

A useful system map should remain a living decision aid, not a one-time architecture essay.
