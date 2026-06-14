from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from .claims import CLAIM_TYPES, CONFIDENCE_LEVELS, ClaimRecord, ClaimStore, ValidationResult, validate_worker_output
from .planner import CHARS_PER_TOKEN_ESTIMATE


# ---------------------------------------------------------------------------
# Worker prompt contract
# ---------------------------------------------------------------------------

WORKER_CONTRACT = """You are a low-context system-mapping worker.

You are only allowed to make claims based on the provided packet.

Rules:
1. Do not infer beyond the inspected scope.
2. Every claim must cite evidence IDs from the packet's evidence_ledger.
3. If evidence is weak, label the statement as a hypothesis.
4. If something is missing, record it as an unknown.
5. Do not hide conflicts.
6. Do not produce a full-system architecture unless the packet supports it.
7. Prefer small, precise claims over broad summaries.
8. Output valid JSON only.
9. Do not use vague language like "clearly", "obviously", "always", "never" unless directly supported by evidence.
10. Confidence must be explicit: low, medium, or high.

Claim types: {claim_types}

Confidence levels: {confidence_levels}

Output schema:
{output_schema}
""".format(
    claim_types=", ".join(sorted(CLAIM_TYPES)),
    confidence_levels=", ".join(sorted(CONFIDENCE_LEVELS)),
    output_schema=json.dumps({
        "schema_version": "0.2",
        "component": "string",
        "inspected_scope": ["list of file paths"],
        "claims": [
            {
                "claim_type": "purpose | entry_point | dependency | data_contract | business_rule | trigger | external_system | risk | owner | unknown",
                "statement": "string",
                "evidence_ids": ["evidence ID from packet evidence_ledger"],
                "confidence": "low | medium | high",
                "scope": "string (optional, file or component this claim covers)",
            }
        ],
        "hypotheses": [
            {
                "statement": "string",
                "reason": "string (why this is not a confirmed claim)",
                "supporting_evidence_ids": ["evidence ID"],
                "confidence": "low | medium",
            }
        ],
        "unknowns": [
            {
                "question": "string",
                "why_it_matters": "string",
                "suggested_next_paths": ["file path"],
            }
        ],
        "conflicts": [
            {
                "topic": "string",
                "evidence_ids": ["evidence ID"],
                "description": "string",
            }
        ],
        "next_actions": [
            {
                "action_type": "inspect_file | inspect_component | ask_human | compare_docs | run_command",
                "description": "string",
                "priority": "low | medium | high",
            }
        ],
    }, indent=2),
)


def get_worker_contract() -> str:
    """Return the strict worker prompt contract."""
    return WORKER_CONTRACT


def _prompt_budget_metrics(prompt: str) -> dict[str, Any]:
    """Return rough prompt-size signals for low-context/local worker handoff."""
    char_count = len(prompt)
    estimated_tokens = max(1, (char_count + CHARS_PER_TOKEN_ESTIMATE - 1) // CHARS_PER_TOKEN_ESTIMATE)
    if estimated_tokens >= 8_000:
        local_worker_risk = "high"
        recommendation = "Use a smaller slice, narrower paths, or packet compression before calling a small/local LLM."
    elif estimated_tokens >= 4_000:
        local_worker_risk = "medium"
        recommendation = "Review prompt size before calling a small/local LLM; consider a smaller slice if output quality drops."
    else:
        local_worker_risk = "low"
        recommendation = "Prompt is likely suitable for a small bounded worker packet."
    return {
        "char_count": char_count,
        "estimated_tokens": estimated_tokens,
        "chars_per_token_estimate": CHARS_PER_TOKEN_ESTIMATE,
        "local_worker_risk": local_worker_risk,
        "compression_recommended": local_worker_risk == "high",
        "recommendation": recommendation,
    }


# ---------------------------------------------------------------------------
# Worker output schema helpers
# ---------------------------------------------------------------------------

def parse_worker_output(raw: str | dict[str, Any]) -> dict[str, Any]:
    """Parse and validate the structure of worker output.

    Accepts either a JSON string or an already-parsed dict.
    Returns a normalized dict with the expected keys.
    """
    if isinstance(raw, str):
        data = json.loads(raw)
    else:
        data = raw

    # Ensure required keys exist
    result: dict[str, Any] = {
        "schema_version": str(data.get("schema_version", "0.2")),
        "component": str(data.get("component", "")),
        "inspected_scope": list(data.get("inspected_scope", [])),
        "claims": [],
        "hypotheses": [],
        "unknowns": [],
        "conflicts": [],
        "next_actions": [],
    }

    # Normalize claims
    for claim in data.get("claims", []):
        if isinstance(claim, dict):
            result["claims"].append({
                "claim_type": str(claim.get("claim_type", claim.get("type", "unknown"))),
                "statement": str(claim.get("statement", claim.get("text", ""))),
                "evidence_ids": list(claim.get("evidence_ids", claim.get("evidence_refs", []))),
                "confidence": str(claim.get("confidence", "medium")),
                "scope": str(claim.get("scope", "")),
            })

    # Normalize hypotheses
    for hyp in data.get("hypotheses", []):
        if isinstance(hyp, dict):
            result["hypotheses"].append({
                "statement": str(hyp.get("statement", "")),
                "reason": str(hyp.get("reason", "")),
                "supporting_evidence_ids": list(hyp.get("supporting_evidence_ids", hyp.get("evidence_ids", []))),
                "confidence": str(hyp.get("confidence", "low")),
            })

    # Normalize unknowns
    for unk in data.get("unknowns", []):
        if isinstance(unk, dict):
            result["unknowns"].append({
                "question": str(unk.get("question", "")),
                "why_it_matters": str(unk.get("why_it_matters", "")),
                "suggested_next_paths": list(unk.get("suggested_next_paths", [])),
            })

    # Normalize conflicts
    for conflict in data.get("conflicts", []):
        if isinstance(conflict, dict):
            result["conflicts"].append({
                "topic": str(conflict.get("topic", "")),
                "evidence_ids": list(conflict.get("evidence_ids", [])),
                "description": str(conflict.get("description", "")),
            })

    # Normalize next_actions
    for action in data.get("next_actions", []):
        if isinstance(action, dict):
            result["next_actions"].append({
                "action_type": str(action.get("action_type", "inspect_file")),
                "description": str(action.get("description", "")),
                "priority": str(action.get("priority", "medium")),
            })

    return result


def claims_from_worker_output(
    worker_output: dict[str, Any],
    validation_result: ValidationResult,
    component: str,
    source_packet: str = "",
    source_worker: str = "",
) -> list[ClaimRecord]:
    """Convert validated worker output into ClaimRecord objects."""
    today = __import__("datetime").date.today().isoformat()
    claims: list[ClaimRecord] = []

    for accepted in validation_result.accepted_claims:
        claim_id = _stable_claim_id(component, str(accepted.get("claim_type", "")), str(accepted.get("statement", "")))
        claims.append(ClaimRecord(
            claim_id=claim_id,
            component=component,
            claim_type=str(accepted.get("claim_type", accepted.get("type", "unknown"))),
            statement=str(accepted.get("statement", accepted.get("text", ""))),
            evidence_ids=list(accepted.get("evidence_ids", accepted.get("evidence_refs", []))),
            confidence=str(accepted.get("confidence", "medium")),
            status="accepted",
            created_at=today,
            last_verified_at=today,
            source_packet=source_packet,
            source_worker=source_worker,
            scope=str(accepted.get("scope", "")),
        ))

    # Also import downgraded claims with their new confidence
    for downgraded in validation_result.downgraded_claims:
        claim_id = _stable_claim_id(component, str(downgraded.get("claim_type", "")), str(downgraded.get("statement", "")))
        claims.append(ClaimRecord(
            claim_id=claim_id,
            component=component,
            claim_type=str(downgraded.get("claim_type", downgraded.get("type", "unknown"))),
            statement=str(downgraded.get("statement", downgraded.get("text", ""))),
            evidence_ids=list(downgraded.get("evidence_ids", downgraded.get("evidence_refs", []))),
            confidence=str(downgraded.get("confidence", "low")),
            status="needs_review",
            created_at=today,
            last_verified_at=today,
            source_packet=source_packet,
            source_worker=source_worker,
            scope=str(downgraded.get("scope", "")),
        ))

    return claims


def _stable_claim_id(component: str, claim_type: str, statement: str) -> str:
    import hashlib
    joined = f"{component}\0{claim_type}\0{statement}"
    return f"claim.{component}.{claim_type}." + hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Worker runner (calls external LLM)
# ---------------------------------------------------------------------------

def run_worker(
    packet_path: Path | str,
    *,
    model: str = "local",
    output_path: Path | str | None = None,
    llm_command: str | None = None,
    max_prompt_tokens: int | None = None,
) -> dict[str, Any]:
    """Run a weak LLM worker over a packet.

    If llm_command is provided, it is used to call the LLM.
    Otherwise, returns the prompt + packet for external processing.

    The llm_command receives the prompt via stdin and should output JSON to stdout.
    Example: llm_command="ollama run qwen3:4b"
    """
    packet = json.loads(Path(packet_path).read_text(encoding="utf-8"))

    prompt = WORKER_CONTRACT + "\n\n--- PACKET ---\n" + json.dumps(packet, indent=2)
    prompt_metrics = _prompt_budget_metrics(prompt)

    if max_prompt_tokens is not None and prompt_metrics["estimated_tokens"] > max_prompt_tokens:
        raise RuntimeError(
            f"Prompt estimated_tokens={prompt_metrics['estimated_tokens']} exceeds --max-prompt-tokens={max_prompt_tokens}; "
            f"{prompt_metrics['recommendation']}"
        )

    if llm_command:
        result = subprocess.run(
            llm_command.split(),
            input=prompt,
            text=True,
            capture_output=True,
            timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(f"LLM worker failed: {result.stderr}")
        worker_output = parse_worker_output(result.stdout)
    else:
        # Return the prompt for external processing
        worker_output = {
            "_prompt": prompt,
            "_prompt_metrics": prompt_metrics,
            "_packet_path": str(packet_path),
            "_model": model,
            "_status": "prompt_generated",
            "schema_version": "0.2",
            "component": packet.get("component", ""),
            "inspected_scope": packet.get("scope", []),
            "claims": [],
            "hypotheses": [],
            "unknowns": [],
            "conflicts": [],
            "next_actions": [],
        }

    if output_path:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(worker_output, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return worker_output
