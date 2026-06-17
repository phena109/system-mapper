from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VALID_STATUSES = {"proposed", "accepted", "superseded", "deprecated", "rejected"}


def _load_store(store_path: Path | str) -> list[dict[str, Any]]:
    path = Path(store_path)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        records = data.get("decisions", [])
    else:
        records = data
    return [record for record in records if isinstance(record, dict)]


def _write_store(store_path: Path | str, records: list[dict[str, Any]]) -> None:
    path = Path(store_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"contract": "system-mapper.architecture-decisions.v1", "decisions": records}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _next_id(records: list[dict[str, Any]]) -> str:
    max_seen = 0
    for record in records:
        raw = str(record.get("id", ""))
        if raw.startswith("adr-"):
            try:
                max_seen = max(max_seen, int(raw.removeprefix("adr-")))
            except ValueError:
                continue
    return f"adr-{max_seen + 1:04d}"


def add_decision(
    store_path: Path | str,
    *,
    title: str,
    status: str,
    context: str,
    decision: str,
    consequences: str,
    supersedes: list[str] | None = None,
) -> dict[str, Any]:
    """Persist an Architecture Decision Record in a machine-readable store."""
    normalized_status = status.lower().strip()
    if normalized_status not in VALID_STATUSES:
        raise ValueError(f"status must be one of: {', '.join(sorted(VALID_STATUSES))}")
    records = _load_store(store_path)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    record = {
        "id": _next_id(records),
        "title": title,
        "status": normalized_status,
        "context": context,
        "decision": decision,
        "consequences": consequences,
        "supersedes": supersedes or [],
        "created_at": now,
        "updated_at": now,
    }
    records.append(record)
    _write_store(store_path, records)
    return record


def list_decisions(store_path: Path | str, *, status: str | None = None) -> list[dict[str, Any]]:
    records = _load_store(store_path)
    if status:
        normalized_status = status.lower().strip()
        records = [record for record in records if str(record.get("status", "")).lower() == normalized_status]
    return records
