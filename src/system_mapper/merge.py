from __future__ import annotations

import re
from dataclasses import asdict
from typing import Any

from .models import Claim, ComponentSummary, Edge, Evidence, EvidenceRecord


def _dedupe_dicts(records: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for record in records:
        record_key = str(record.get(key, ""))
        if record_key in seen:
            continue
        seen.add(record_key)
        deduped.append(record)
    return deduped


def _dedupe_by_signature(records: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    deduped: list[dict[str, Any]] = []
    for record in records:
        signature = tuple(record.get(key) for key in keys)
        if signature in seen:
            continue
        seen.add(signature)
        deduped.append(record)
    return deduped


def _owner_value(text: str) -> str:
    match = re.search(r"owner[^:]*:\s*(.+)$", text, re.I)
    return (match.group(1) if match else text).strip()


def _detect_conflicts(claims: list[dict[str, Any]]) -> list[str]:
    conflicts: list[str] = []
    by_type: dict[str, set[str]] = {}
    for claim in claims:
        claim_type = str(claim.get("type", ""))
        if claim_type not in {"owner"}:
            continue
        value = _owner_value(str(claim.get("text", "")))
        if value:
            by_type.setdefault(claim_type, set()).add(value)
    for claim_type, values in sorted(by_type.items()):
        if len(values) > 1:
            conflicts.append(f"Conflicting {claim_type} claims: " + " vs ".join(sorted(values)))
    return conflicts


def _evidence_from_dict(record: dict[str, Any]) -> Evidence:
    return Evidence(
        source=str(record.get("source", "")),
        kind=str(record.get("kind", "")),
        symbols=list(record.get("symbols", [])),
        notes=str(record.get("notes", "")),
        freshness=str(record.get("freshness", "unknown")),
    )


def _edge_from_dict(record: dict[str, Any]) -> Edge:
    return Edge(
        kind=str(record.get("kind", "")),
        source=str(record.get("source", "")),
        target=str(record.get("target", "")),
        confidence=str(record.get("confidence", "medium")),
        source_line=record.get("source_line"),
    )


def _claim_from_dict(record: dict[str, Any]) -> Claim:
    return Claim(
        id=str(record.get("id", "")),
        type=str(record.get("type", "")),
        text=str(record.get("text", "")),
        confidence=str(record.get("confidence", "medium")),
        evidence_refs=list(record.get("evidence_refs", [])),
        state=str(record.get("state", "active")),
    )


def _evidence_record_from_dict(record: dict[str, Any]) -> EvidenceRecord:
    return EvidenceRecord(
        id=str(record.get("id", "")),
        source=str(record.get("source", "")),
        line_start=int(record.get("line_start", 1)),
        line_end=int(record.get("line_end", record.get("line_start", 1))),
        kind=str(record.get("kind", "")),
        excerpt=str(record.get("excerpt", "")),
        freshness=str(record.get("freshness", "unknown")),
    )


def _classify_claims(claim_records: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Classify incoming claims during merge.

    Returns categories: confirmed, repeated, new, weakened, contradicted, stale, unsupported.
    """
    classification: dict[str, list[str]] = {
        "confirmed": [],
        "repeated": [],
        "new": [],
        "weakened": [],
        "contradicted": [],
        "stale": [],
        "unsupported": [],
    }
    seen_statements: dict[str, str] = {}
    for claim in claim_records:
        claim_id = str(claim.get("id", ""))
        claim_type = str(claim.get("type", ""))
        text = str(claim.get("text", "")).strip().lower()
        confidence = str(claim.get("confidence", "medium"))
        state = str(claim.get("state", "active"))

        if state == "stale":
            classification["stale"].append(claim_id)
            continue
        if state == "rejected":
            classification["unsupported"].append(claim_id)
            continue

        # Check for repeated claims
        statement_key = f"{claim_type}:{text}"
        if statement_key in seen_statements:
            classification["repeated"].append(claim_id)
            continue
        seen_statements[statement_key] = claim_id

        # Check for weakened claims (low confidence in a merge)
        if confidence == "low":
            classification["weakened"].append(claim_id)
            continue

        classification["confirmed"].append(claim_id)

    return classification


def merge_component_summaries(
    summaries: list[dict[str, Any]],
    component: str | None = None,
    claim_store_path: str | None = None,
) -> ComponentSummary:
    """Merge lower-level component summaries into an upward map.

    The merge is intentionally conservative: it preserves source claims and
    evidence ledger records rather than rewriting them, then records explicit
    conflicts for review by a human or a higher-context agent.
    """
    name = component or "merged-system"
    scope = [str(summary.get("component", "unknown")) for summary in summaries]
    evidence_records = _dedupe_dicts(
        [record for summary in summaries for record in summary.get("evidence", []) if isinstance(record, dict)],
        "source",
    )
    edge_records = _dedupe_by_signature(
        [record for summary in summaries for record in summary.get("edges", []) if isinstance(record, dict)],
        ("kind", "source", "target", "source_line"),
    )
    claim_records = _dedupe_dicts(
        [record for summary in summaries for record in summary.get("claims", []) if isinstance(record, dict)],
        "id",
    )
    ledger_records = _dedupe_dicts(
        [record for summary in summaries for record in summary.get("evidence_ledger", []) if isinstance(record, dict)],
        "id",
    )
    conflicts = [
        conflict
        for summary in summaries
        for conflict in summary.get("conflicts", [])
        if isinstance(conflict, str)
    ]
    conflicts.extend(_detect_conflicts(claim_records))

    # Classify incoming claims
    claim_classification = _classify_claims(claim_records)

    # If a claim store is provided, enrich with stored claims
    if claim_store_path:
        try:
            from .claims import ClaimStore
            store = ClaimStore(claim_store_path)
            stored_claims = store.list_claims(component=name)
            stored_conflicts = store.get_conflicts()
            if stored_conflicts:
                for sc in stored_conflicts:
                    conflicts.append(
                        f"Stored conflict on '{sc.get('topic', '')}': "
                        f"{sc.get('claim_a', '')} vs {sc.get('claim_b', '')}"
                    )
            # Add stored claims that aren't already in the merge
            existing_ids = {str(c.get("id", "")) for c in claim_records}
            for sc in stored_claims:
                if sc.claim_id not in existing_ids:
                    claim_records.append(sc.to_dict())
                    claim_classification["repeated"].append(sc.claim_id)
        except (FileNotFoundError, KeyError):
            pass

    unknowns = sorted({unknown for summary in summaries for unknown in summary.get("unknowns", []) if isinstance(unknown, str)})
    risks = sorted({risk for summary in summaries for risk in summary.get("risks", []) if isinstance(risk, str)})
    suggested_next = sorted(
        {next_step for summary in summaries for next_step in summary.get("suggested_next", []) if isinstance(next_step, str)}
    )
    if conflicts:
        unknowns.append("Merged summaries contain conflicting claims that need review")
        suggested_next.append("Resolve preserved conflicts before treating the upward map as authoritative")

    # Add claim classification info to unknowns
    if claim_classification.get("weakened"):
        unknowns.append(f"Weakened claims: {', '.join(claim_classification['weakened'][:3])}")
    if claim_classification.get("contradicted"):
        conflicts.extend(claim_classification["contradicted"])

    confidence = {
        "purpose": "medium" if summaries else "low",
        "interfaces": "medium" if edge_records else "low",
        "business_rules": "medium" if any(claim.get("type") == "business_rule" for claim in claim_records) else "low",
        "operational_process": "medium" if any(claim.get("type") == "human_step" for claim in claim_records) else "low",
        "claim_merge": "medium" if claim_records else "low",
    }

    return ComponentSummary(
        component=name,
        scope=scope,
        purpose=f"Merged system map for {name} from {len(summaries)} lower-level summaries",
        evidence=[_evidence_from_dict(record) for record in evidence_records],
        edges=[_edge_from_dict(record) for record in edge_records],
        entry_points=[entry for summary in summaries for entry in summary.get("entry_points", []) if isinstance(entry, str)],
        inputs=[item for summary in summaries for item in summary.get("inputs", []) if isinstance(item, str)],
        outputs=[item for summary in summaries for item in summary.get("outputs", []) if isinstance(item, str)],
        business_rules=[item for summary in summaries for item in summary.get("business_rules", []) if isinstance(item, str)],
        human_steps=[item for summary in summaries for item in summary.get("human_steps", []) if isinstance(item, str)],
        risks=risks,
        unknowns=unknowns,
        suggested_next=suggested_next,
        confidence=confidence,
        claims=[_claim_from_dict(record) for record in claim_records],
        evidence_ledger=[_evidence_record_from_dict(record) for record in ledger_records],
        conflicts=conflicts,
    )
