from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .claims import VAGUE_PHRASES


@dataclass
class MapQualityReport:
    """Measurable anti-garbage quality report for a system map artifact."""

    anti_garbage_score: float
    passed: bool
    metrics: dict[str, float] = field(default_factory=dict)
    thresholds: dict[str, float] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_map_quality(system_map: dict[str, Any], *, min_score: float = 0.8) -> MapQualityReport:
    """Score whether a map is evidence-backed enough to trust.

    The score is intentionally conservative. It is not a truth score; it is an
    anti-garbage gate that rewards claims being cited, citations being valid,
    high-confidence claims having stronger support, uncertainty being visible,
    and vague overclaiming being rare.
    """

    claims = _collect_claims(system_map)
    evidence_ids = _collect_evidence_ids(system_map)
    unknowns = _collect_unknowns(system_map, claims)
    conflicts = _collect_conflicts(system_map)

    total_claims = len(claims)
    claims_with_evidence = sum(1 for claim in claims if _claim_evidence_ids(claim))
    all_citations = [eid for claim in claims for eid in _claim_evidence_ids(claim)]
    valid_citations = [eid for eid in all_citations if eid in evidence_ids]
    high_confidence_claims = [claim for claim in claims if str(claim.get("confidence", "")).lower() == "high"]
    supported_high_confidence = [
        claim for claim in high_confidence_claims
        if _high_confidence_supported(claim, evidence_ids)
    ]
    vague_claims = [claim for claim in claims if _has_vague_language(str(claim.get("statement", claim.get("text", ""))))]
    accepted_claims = [claim for claim in claims if str(claim.get("status", "accepted")) == "accepted"]
    unsupported_accepted = [
        claim for claim in accepted_claims
        if not _claim_evidence_ids(claim) or any(eid not in evidence_ids for eid in _claim_evidence_ids(claim))
    ]

    metrics = {
        "claim_count": float(total_claims),
        "evidence_count": float(len(evidence_ids)),
        "claim_evidence_coverage": _ratio(claims_with_evidence, total_claims),
        "citation_validity": _ratio(len(valid_citations), len(all_citations)),
        "high_confidence_support": _ratio(len(supported_high_confidence), len(high_confidence_claims)),
        "unknown_visibility": 1.0 if unknowns else 0.0,
        "conflict_visibility": 1.0 if conflicts else 0.0,
        "vague_language_rate": _ratio(len(vague_claims), total_claims),
        "unsupported_accepted_rate": _ratio(len(unsupported_accepted), len(accepted_claims)),
    }

    # Weighted score: citations and citation validity dominate; uncertainty is
    # deliberately smaller because some tiny maps genuinely have no unknowns.
    anti_garbage_score = round(max(0.0, min(1.0, (
        0.30 * metrics["claim_evidence_coverage"]
        + 0.30 * metrics["citation_validity"]
        + 0.20 * metrics["high_confidence_support"]
        + 0.10 * metrics["unknown_visibility"]
        + 0.10 * (1.0 - metrics["vague_language_rate"])
        - 0.20 * metrics["unsupported_accepted_rate"]
    ))), 3)

    thresholds = {
        "anti_garbage_score": min_score,
        "claim_evidence_coverage": 1.0,
        "citation_validity": 1.0,
        "high_confidence_support": 1.0,
        "vague_language_rate_max": 0.10,
        "unsupported_accepted_rate_max": 0.0,
    }

    failures: list[str] = []
    recommendations: list[str] = []

    if anti_garbage_score < min_score:
        failures.append(f"anti_garbage_score {anti_garbage_score:.3f} is below {min_score:.3f}")
        recommendations.append("Treat this map as orientation only; do not let agents rely on it without review.")
    if metrics["claim_evidence_coverage"] < thresholds["claim_evidence_coverage"]:
        failures.append("claim_evidence_coverage is below 1.0; every claim must cite evidence IDs")
        recommendations.append("Move uncited statements to hypotheses/unknowns or add evidence citations.")
    if metrics["citation_validity"] < thresholds["citation_validity"]:
        failures.append("citation_validity is below 1.0; at least one cited evidence ID is missing")
        recommendations.append("Reject claims with missing evidence IDs or rebuild the packet/summary evidence ledger.")
    if metrics["high_confidence_support"] < thresholds["high_confidence_support"]:
        failures.append("high_confidence_support is below 1.0; high-confidence claims need multiple valid citations and precise wording")
        recommendations.append("Downgrade high-confidence claims unless they have at least two valid evidence IDs and no vague language.")
    if metrics["vague_language_rate"] > thresholds["vague_language_rate_max"]:
        failures.append("vague_language_rate is too high; overconfident wording makes the map look like generated garbage")
        recommendations.append("Replace words like clearly/always/never/every with scoped, evidenced statements.")
    if metrics["unsupported_accepted_rate"] > thresholds["unsupported_accepted_rate_max"]:
        failures.append("unsupported_accepted_rate is above 0; accepted claims include unsupported citations")
        recommendations.append("Rejected or needs_review status is safer than accepted for unsupported claims.")
    if not unknowns:
        recommendations.append("Add explicit unknowns when runtime behaviour, ownership, or production configuration was not inspected.")

    passed = not failures
    return MapQualityReport(
        anti_garbage_score=anti_garbage_score,
        passed=passed,
        metrics=metrics,
        thresholds=thresholds,
        failures=failures,
        recommendations=recommendations,
    )


def _collect_claims(obj: Any) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if _looks_like_claim(value):
                claims.append(value)
                return
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(obj)
    return claims


def _looks_like_claim(value: dict[str, Any]) -> bool:
    return (
        ("claim_type" in value or "type" in value)
        and ("statement" in value or "text" in value)
    )


def _collect_evidence_ids(obj: Any) -> set[str]:
    ids: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if "id" in value and _looks_like_evidence(value):
                ids.add(str(value["id"]))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(obj)
    return ids


def _looks_like_evidence(value: dict[str, Any]) -> bool:
    return any(key in value for key in ("source", "line_start", "line_end", "excerpt", "freshness", "kind"))


def _collect_unknowns(system_map: dict[str, Any], claims: list[dict[str, Any]]) -> list[Any]:
    unknowns: list[Any] = []

    def visit(value: Any, parent_key: str = "") -> None:
        if isinstance(value, dict):
            if parent_key == "unknowns" or str(value.get("claim_type", value.get("type", ""))) == "unknown":
                unknowns.append(value)
                return
            for key, child in value.items():
                visit(child, str(key))
        elif isinstance(value, list):
            for child in value:
                visit(child, parent_key)

    visit(system_map)
    for claim in claims:
        if str(claim.get("claim_type", claim.get("type", ""))) == "unknown" and claim not in unknowns:
            unknowns.append(claim)
    return unknowns


def _collect_conflicts(system_map: dict[str, Any]) -> list[Any]:
    conflicts: list[Any] = []

    def visit(value: Any, parent_key: str = "") -> None:
        if isinstance(value, dict):
            if parent_key == "conflicts":
                conflicts.append(value)
                return
            for key, child in value.items():
                visit(child, str(key))
        elif isinstance(value, list):
            for child in value:
                visit(child, parent_key)

    visit(system_map)
    return conflicts


def _claim_evidence_ids(claim: dict[str, Any]) -> list[str]:
    raw = claim.get("evidence_ids", claim.get("evidence_refs", []))
    if isinstance(raw, list):
        return [str(item) for item in raw if str(item)]
    if isinstance(raw, str) and raw:
        return [raw]
    return []


def _high_confidence_supported(claim: dict[str, Any], evidence_ids: set[str]) -> bool:
    cited = _claim_evidence_ids(claim)
    statement = str(claim.get("statement", claim.get("text", "")))
    return len(cited) >= 2 and all(eid in evidence_ids for eid in cited) and not _has_vague_language(statement)


def _has_vague_language(text: str) -> bool:
    lower = text.lower()
    return any(phrase in lower for phrase in VAGUE_PHRASES)


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 1.0
    return round(numerator / denominator, 3)
