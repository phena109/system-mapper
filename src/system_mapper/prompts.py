from __future__ import annotations


def slice_prompt(component: str | None = None) -> str:
    component_label = component or "<component>"
    return f"""You are mapping a large legacy/living system using divide and conquer.

Target component: {component_label}

Analyse only the files, summaries, and artefacts provided. Do not infer system-wide behaviour unless directly supported by evidence.

For the target component, produce:

1. Component name and inspected scope
2. Observed facts
3. Purpose, if supported by evidence
4. Entry points
5. Inputs
6. Outputs
7. Data stores read/written
8. External systems touched
9. Internal dependencies
10. Configuration used
11. Business rules found
12. Human/manual steps implied
13. Failure modes / operational risks
14. Unknowns and missing evidence
15. Conflicts or contradictions
16. Suggested next files/components to inspect
17. Machine-readable edges

Classify evidence separately:

- Code evidence
- Configuration evidence
- Documentation evidence
- Operational/runbook evidence
- Human/process evidence
- Test evidence

Rules:

- Every important claim must cite file paths, symbols, config keys, or provided summaries.
- Mark unsupported interpretations as assumptions.
- Use confidence: high / medium / low.
- Do not assume documentation is current.
- If docs and code disagree, preserve the conflict instead of smoothing it over.
- Prefer implemented behaviour from code/config for current behaviour.
- Prefer docs/runbooks for intent, process, and human context.
- If the component cannot be understood from the provided artefacts, say what evidence is missing.
"""


def update_prompt(component: str | None = None) -> str:
    component_label = component or "<component>"
    return f"""You are updating a living system map.

Target component: {component_label}

This is a living system. New code, documents, tests, configuration, and operational artefacts may be merged at any time.

Given the previous component summary plus the supplied diff, PR, commit, changed files, or replacement artefacts:

1. Identify affected components.
2. Compare against existing component summaries.
3. Describe behaviour changes.
4. Describe interface, data, dependency, config, and operational changes.
5. Identify documentation or summaries that are now stale.
6. Update machine-readable edges.
7. Mark downstream components that may need reinspection.
8. Preserve unresolved uncertainty and conflicts.
9. Output a concise changelog entry for the system map.

Rules:

- Use evidence from the changed artefacts and previous summaries only.
- Do not assume a changed document describes deployed behaviour unless code/config supports it.
- Do not assume unchanged summaries are still true if their cited source changed.
- Mark confidence and freshness risk for each important change.
- Prefer small, local updates over rewriting the whole map.
"""


def build_prompt(kind: str, component: str | None = None) -> str:
    if kind == "slice":
        return slice_prompt(component)
    if kind == "update":
        return update_prompt(component)
    raise ValueError(f"unknown prompt kind: {kind}")
