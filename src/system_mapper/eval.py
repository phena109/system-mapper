from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class BenchmarkQuestion:
    """A single benchmark question to evaluate map usefulness."""
    question: str
    expected_evidence: list[str] = field(default_factory=list)
    expected_claim_type: str = ""
    category: str = "general"  # architecture, dependency, risk, change_impact, unknown


@dataclass
class BenchmarkResult:
    """Result of evaluating a single question."""
    question: str
    answered: bool
    answer: str
    evidence_cited: list[str]
    claim_type: str
    correct: bool | None = None  # None if not verifiable
    notes: str = ""


@dataclass
class BenchmarkReport:
    """Full benchmark report comparing raw vs mapped performance."""
    total_questions: int = 0
    raw_correct: int = 0
    mapped_correct: int = 0
    raw_results: list[dict[str, Any]] = field(default_factory=list)
    mapped_results: list[dict[str, Any]] = field(default_factory=list)
    improvement: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_benchmark(path: Path | str) -> list[BenchmarkQuestion]:
    """Load benchmark questions from a JSON file."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    questions = []
    for item in data:
        if isinstance(item, dict):
            questions.append(BenchmarkQuestion(
                question=str(item.get("question", "")),
                expected_evidence=list(item.get("expected_evidence", [])),
                expected_claim_type=str(item.get("expected_claim_type", "")),
                category=str(item.get("category", "general")),
            ))
    return questions


def create_sample_benchmark(path: Path | str) -> None:
    """Create a sample benchmark file for a project."""
    sample = [
        {
            "question": "Where does checkout payment start?",
            "expected_evidence": ["CheckoutController.php"],
            "expected_claim_type": "entry_point",
            "category": "architecture",
        },
        {
            "question": "Which files touch order status?",
            "expected_claim_type": "dependency",
            "category": "dependency",
        },
        {
            "question": "What external systems are involved in refund flow?",
            "expected_claim_type": "external_system",
            "category": "architecture",
        },
        {
            "question": "Which claims are uncertain?",
            "expected_claim_type": "unknown",
            "category": "unknown",
        },
        {
            "question": "What changed in this PR that may affect fulfilment?",
            "expected_claim_type": "change_impact",
            "category": "change_impact",
        },
    ]
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(sample, indent=2) + "\n", encoding="utf-8")


def evaluate_map_usefulness(
    questions: list[BenchmarkQuestion],
    system_map: dict[str, Any],
) -> BenchmarkReport:
    """Evaluate how well a system map answers benchmark questions.

    This is a deterministic heuristic evaluation:
    - Checks if expected evidence files are referenced in the map
    - Checks if the expected claim type exists in the map
    - Does NOT require an LLM — this measures map coverage/completeness
    """
    report = BenchmarkReport(total_questions=len(questions))

    # Flatten the system map for searching
    all_evidence = set()
    all_claim_types = set()
    all_text = []

    def _collect(obj: Any) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in ("source", "path", "file", "evidence_ids"):
                    if isinstance(v, str):
                        all_evidence.add(v)
                    elif isinstance(v, list):
                        for item in v:
                            if isinstance(item, str):
                                all_evidence.add(item)
                if k == "claim_type" and isinstance(v, str):
                    all_claim_types.add(v)
                _collect(v)
        elif isinstance(obj, list):
            for item in obj:
                _collect(item)
        elif isinstance(obj, str):
            all_text.append(obj.lower())

    _collect(system_map)

    full_text = " ".join(all_text)

    for bq in questions:
        result = BenchmarkResult(
            question=bq.question,
            answered=False,
            answer="",
            evidence_cited=[],
            claim_type=bq.expected_claim_type,
        )

        # Check if expected evidence is in the map
        evidence_found = []
        for ev in bq.expected_evidence:
            for map_ev in all_evidence:
                if ev.lower() in map_ev.lower():
                    evidence_found.append(map_ev)
                    break

        # Check if claim type exists
        claim_type_found = bq.expected_claim_type in all_claim_types

        # Check if question keywords appear in map text
        question_keywords = [w for w in bq.question.lower().split() if len(w) > 3]
        keyword_matches = sum(1 for kw in question_keywords if kw in full_text)
        keyword_ratio = keyword_matches / max(len(question_keywords), 1)

        result.answered = bool(evidence_found or claim_type_found or keyword_ratio > 0.3)
        result.evidence_cited = evidence_found
        result.correct = result.answered  # Heuristic: if we found evidence, it's likely correct

        report.mapped_results.append({
            "question": result.question,
            "answered": result.answered,
            "answer": result.answer,
            "evidence_cited": result.evidence_cited,
            "claim_type": result.claim_type,
            "correct": result.correct,
            "notes": result.notes,
        })
        if result.answered:
            report.mapped_correct += 1

    if report.total_questions > 0:
        report.improvement = report.mapped_correct / report.total_questions

    return report
