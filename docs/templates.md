# Mapping templates

Copy these sections into your own `.system-map/` notes when a JSON summary needs a human-readable companion. Keep them short and evidence-backed.

## Component summary note

```markdown
# Component: <name>

## Scope inspected

- `<path>` — why it was included
- `<path>` — why it was included

## Observed purpose

<One paragraph. Say “appears to” when purpose is inferred rather than explicitly documented.>

## Evidence

| Claim | Evidence | Confidence |
| --- | --- | --- |
| <claim> | `<file>` / line / summary field | high/medium/low |

## Relationships

- Internal dependencies:
  - `<source>` -> `<target>` because <evidence>
- External systems:
  - `<source>` -> `<target>` because <evidence>
- Data stores / queues / files:
  - `<source>` -> `<target>` because <evidence>
- Human/manual process:
  - <manual step or ownership hint, with source>

## Unknowns

| Unknown | Why it matters | Next action |
| --- | --- | --- |
| <question> | <decision or risk affected> | inspect / ask human / defer |

## Change sensitivity

If these files change, re-check this summary:

- `<path>` because <claim that depends on it>
```

## Human interview prompts

Use these when the map finds operational gaps that code cannot answer.

```markdown
1. What is this component responsible for in production?
2. Who owns it when it fails or becomes stale?
3. Which docs, dashboards, runbooks, or tickets are considered authoritative?
4. Are there manual steps that are not represented in code/config?
5. Which external systems or data stores are business-critical?
6. What would make the current summary misleading or dangerous?
7. Which unknown should be resolved first?
```

## Review checklist

Before trusting or merging a map:

```markdown
- [ ] The inspected scope is explicit.
- [ ] Important claims cite files, lines, or lower-level summaries.
- [ ] Inference is labelled as inference.
- [ ] Docs and code conflicts are preserved, not hidden.
- [ ] Unknowns are kept with next actions.
- [ ] External systems and data stores are not invented from names alone.
- [ ] Manual/human processes are either evidenced or marked for confirmation.
- [ ] The summary says what would make it stale.
```
