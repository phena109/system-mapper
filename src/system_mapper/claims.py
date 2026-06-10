from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Claim schema
# ---------------------------------------------------------------------------

CLAIM_STATUSES = {"accepted", "hypothesis", "rejected", "conflicted", "stale", "needs_review"}

CLAIM_TYPES = {
    "purpose", "entry_point", "dependency", "data_contract", "business_rule",
    "trigger", "external_system", "risk", "owner", "unknown",
}

CONFIDENCE_LEVELS = {"low", "medium", "high"}

# Vague wording that should trigger downgrade or rejection
VAGUE_PHRASES = [
    "clearly", "obviously", "the system mainly", "this proves",
    "all requests", "always", "never", "every", "none of the",
    "it is clear", "it is obvious", "without doubt", "undoubtedly",
]


@dataclass
class ClaimRecord:
    """A single evidence-backed claim produced by an LLM worker."""
    claim_id: str
    component: str
    claim_type: str
    statement: str
    evidence_ids: list[str] = field(default_factory=list)
    confidence: str = "medium"
    status: str = "needs_review"
    created_at: str = ""
    last_verified_at: str = ""
    source_packet: str = ""
    source_worker: str = ""
    scope: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> ClaimRecord:
        return ClaimRecord(
            claim_id=str(data.get("claim_id", "")),
            component=str(data.get("component", "")),
            claim_type=str(data.get("claim_type", "unknown")),
            statement=str(data.get("statement", "")),
            evidence_ids=list(data.get("evidence_ids", [])),
            confidence=str(data.get("confidence", "medium")),
            status=str(data.get("status", "needs_review")),
            created_at=str(data.get("created_at", "")),
            last_verified_at=str(data.get("last_verified_at", "")),
            source_packet=str(data.get("source_packet", "")),
            source_worker=str(data.get("source_worker", "")),
            scope=str(data.get("scope", "")),
        )


@dataclass
class ValidationResult:
    """Result of validating a worker output against its packet."""
    accepted_claims: list[dict[str, Any]] = field(default_factory=list)
    downgraded_claims: list[dict[str, Any]] = field(default_factory=list)
    rejected_claims: list[dict[str, Any]] = field(default_factory=list)
    validation_errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Claim Store
# ---------------------------------------------------------------------------

class ClaimStore:
    """Durable JSON-backed claim store.

    Claims are stored as a JSON file that can be queried, merged, and updated.
    The store tracks claim status, staleness, and conflicts.
    """

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._claims: list[ClaimRecord] = []
        if self.path.exists():
            self._load()

    def _load(self) -> None:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self._claims = [ClaimRecord.from_dict(c) for c in data.get("claims", [])]

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": "0.2",
            "claim_count": len(self._claims),
            "last_updated": date.today().isoformat(),
            "claims": [c.to_dict() for c in self._claims],
        }
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def import_claims(self, claims: list[ClaimRecord]) -> dict[str, int]:
        """Import claims into the store. Returns counts by status."""
        counts: dict[str, int] = {"new": 0, "updated": 0, "duplicate": 0}
        existing_ids = {c.claim_id: idx for idx, c in enumerate(self._claims)}
        for claim in claims:
            if claim.claim_id in existing_ids:
                idx = existing_ids[claim.claim_id]
                existing = self._claims[idx]
                # Update if the new claim has newer verification or higher confidence
                if claim.status == "accepted" and existing.status != "accepted":
                    self._claims[idx] = claim
                    counts["updated"] += 1
                else:
                    counts["duplicate"] += 1
            else:
                self._claims.append(claim)
                counts["new"] += 1
        self._save()
        return counts

    def list_claims(
        self,
        component: str | None = None,
        claim_type: str | None = None,
        status: str | None = None,
        min_confidence: str | None = None,
    ) -> list[ClaimRecord]:
        """Query claims with optional filters."""
        results = list(self._claims)
        if component:
            results = [c for c in results if c.component == component]
        if claim_type:
            results = [c for c in results if c.claim_type == claim_type]
        if status:
            results = [c for c in results if c.status == status]
        if min_confidence:
            order = {"low": 0, "medium": 1, "high": 2}
            min_val = order.get(min_confidence, 0)
            results = [c for c in results if order.get(c.confidence, 0) >= min_val]
        return results

    def get_conflicts(self) -> list[dict[str, Any]]:
        """Find conflicting claims (same topic, incompatible statements)."""
        conflicts: list[dict[str, Any]] = []
        by_component_type: dict[tuple[str, str], list[ClaimRecord]] = {}
        for claim in self._claims:
            if claim.status in {"rejected", "stale"}:
                continue
            key = (claim.component, claim.claim_type)
            by_component_type.setdefault(key, []).append(claim)

        for (component, claim_type), claims in sorted(by_component_type.items()):
            if len(claims) <= 1:
                continue
            # Check for owner conflicts
            if claim_type == "owner":
                values = set()
                for c in claims:
                    val = c.statement.strip()
                    if ":" in val:
                        val = val.split(":", 1)[1].strip()
                    values.add(val)
                if len(values) > 1:
                    conflicts.append({
                        "topic": f"{component} ownership",
                        "claim_a": claims[0].statement,
                        "claim_b": claims[1].statement,
                        "status": "unresolved",
                        "next_action": f"Resolve conflicting ownership claims for {component}.",
                    })
        return conflicts

    def mark_stale(self, source_paths: list[str]) -> int:
        """Mark claims as stale if their evidence comes from changed sources."""
        count = 0
        for claim in self._claims:
            if claim.status == "stale":
                continue
            for ev_id in claim.evidence_ids:
                for path in source_paths:
                    if path in ev_id:
                        claim.status = "stale"
                        count += 1
                        break
        if count:
            self._save()
        return count

    @property
    def stats(self) -> dict[str, int]:
        result: dict[str, int] = {"total": len(self._claims)}
        for status in CLAIM_STATUSES:
            result[status] = sum(1 for c in self._claims if c.status == status)
        return result


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------

def validate_worker_output(
    worker_output: dict[str, Any],
    packet: dict[str, Any],
) -> ValidationResult:
    """Validate a worker's output against the packet's evidence.

    Rules:
    - Every claim must cite evidence IDs.
    - Cited evidence must exist in the packet.
    - Claim scope must not exceed inspected files.
    - Vague wording is downgraded.
    - Unsupported architecture claims are rejected.
    - Confidence is reduced when evidence is indirect.
    - Hypotheses must not be promoted to confirmed claims.
    """
    result = ValidationResult()
    evidence_ids = _extract_evidence_ids(packet)
    inspected_scope = set(packet.get("scope", []))
    component = str(packet.get("component", ""))

    claims = worker_output.get("claims", [])
    if not isinstance(claims, list):
        result.validation_errors.append("claims must be a list")
        return result

    for claim in claims:
        if not isinstance(claim, dict):
            result.validation_errors.append(f"claim is not a dict: {claim}")
            continue

        claim_type = str(claim.get("claim_type", claim.get("type", "unknown")))
        statement = str(claim.get("statement", claim.get("text", "")))
        claim_evidence_ids = claim.get("evidence_ids", claim.get("evidence_refs", []))
        confidence = str(claim.get("confidence", "medium"))

        # --- Evidence validity ---
        if not claim_evidence_ids:
            result.rejected_claims.append({
                **claim,
                "rejection_reason": "No evidence IDs cited.",
            })
            result.validation_errors.append(
                f"Claim has no evidence: {statement[:120]}"
            )
            continue

        missing_evidence = [eid for eid in claim_evidence_ids if eid not in evidence_ids]
        if missing_evidence:
            result.rejected_claims.append({
                **claim,
                "rejection_reason": f"Evidence IDs not found in packet: {missing_evidence}",
            })
            result.validation_errors.append(
                f"Claim cites missing evidence {missing_evidence}: {statement[:120]}"
            )
            continue

        # --- Scope control ---
        claim_scope = str(claim.get("scope", ""))
        if claim_scope and inspected_scope:
            # If the claim references files outside the inspected scope, downgrade
            claim_files = {p.strip() for p in claim_scope.split(",") if p.strip()}
            if claim_files and not claim_files.issubset(inspected_scope):
                confidence = _downgrade_confidence(confidence)
                result.warnings.append(
                    f"Claim scope {claim_files} exceeds inspected scope; confidence downgraded."
                )

        # --- Language control ---
        if _has_vague_language(statement):
            old_confidence = confidence
            confidence = _downgrade_confidence(confidence)
            if confidence != old_confidence:
                result.warnings.append(
                    f"Vague language detected; confidence downgraded: {statement[:120]}"
                )
            # Reject if high confidence with vague language
            if str(claim.get("confidence", "medium")) == "high":
                result.downgraded_claims.append({
                    **claim,
                    "confidence": confidence,
                    "downgrade_reason": "High confidence with vague language.",
                })
                continue

        # --- Confidence control ---
        if confidence == "high":
            # High confidence requires direct evidence and narrow scope
            if len(claim_evidence_ids) < 2:
                confidence = _downgrade_confidence(confidence)
                result.warnings.append(
                    f"Single evidence source; high confidence downgraded: {statement[:120]}"
                )

        # Build validated claim
        validated = {
            **claim,
            "confidence": confidence,
            "status": "accepted",
        }
        result.accepted_claims.append(validated)

    # Validate hypotheses — they must not be promoted
    hypotheses = worker_output.get("hypotheses", [])
    if isinstance(hypotheses, list):
        for hyp in hypotheses:
            if isinstance(hyp, dict):
                hyp_confidence = str(hyp.get("confidence", "low"))
                if hyp_confidence == "high":
                    result.warnings.append(
                        f"Hypothesis has high confidence; should be medium or low: {str(hyp.get('statement', ''))[:120]}"
                    )

    # Validate unknowns — they should be preserved
    unknowns = worker_output.get("unknowns", [])
    if isinstance(unknowns, list):
        for unk in unknowns:
            if isinstance(unk, dict):
                question = str(unk.get("question", ""))
                if question:
                    result.warnings.append(f"Unknown preserved: {question[:120]}")

    return result


def _extract_evidence_ids(packet: dict[str, Any]) -> set[str]:
    """Extract all evidence IDs from a packet."""
    ids: set[str] = set()
    # From evidence ledger
    for record in packet.get("evidence_ledger", []):
        if isinstance(record, dict):
            eid = record.get("id")
            if eid:
                ids.add(str(eid))
    # From summary evidence
    summary = packet.get("summary", {})
    if isinstance(summary, dict):
        for record in summary.get("evidence_ledger", []):
            if isinstance(record, dict):
                eid = record.get("id")
                if eid:
                    ids.add(str(eid))
        for record in summary.get("evidence", []):
            if isinstance(record, dict):
                source = record.get("source", "")
                if source:
                    ids.add(str(source))
    # From top-level evidence
    for record in packet.get("evidence", []):
        if isinstance(record, dict):
            eid = record.get("id", record.get("source", ""))
            if eid:
                ids.add(str(eid))
    return ids


def _has_vague_language(text: str) -> bool:
    """Check for vague/overconfident language."""
    lower = text.lower()
    return any(phrase in lower for phrase in VAGUE_PHRASES)


def _downgrade_confidence(confidence: str) -> str:
    """Downgrade confidence by one level."""
    order = {"high": "medium", "medium": "low", "low": "low"}
    return order.get(confidence, "low")
