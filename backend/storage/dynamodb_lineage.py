"""Decision lineage persistence and retrieval — DynamoDB-backed.

Replaces backend/compliance/decision_lineage.py (CockroachDB).

For production, this would use DynamoDB. For local testing, we use
an in-memory dict + file-backed persistence (matching dynamodb_jobs.py pattern).

Lineage Item Schema:
    PK: WORKSPACE#{workspace_id}
    SK: LINEAGE#{decision_id}
    
    case_id: str
    conclusion: str (human-readable decision description)
    evidence_ids: list (JSON-serializable)
    confidence: float | None
    decision_type: str ("match", "exception", "unresolved")
    created_at: Unix timestamp
"""
import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# In-memory lineage store: {workspace_id: {decision_id: {item}}}
_lineage: dict[str, dict[str, dict]] = {}
_lineage_lock = asyncio.Lock()

# Persistence file
_LINEAGE_FILE = "data/lineage.json"


def _load_lineage_from_disk() -> None:
    """Load lineage from disk on module init."""
    global _lineage
    if Path(_LINEAGE_FILE).exists():
        try:
            with open(_LINEAGE_FILE, "r") as f:
                data = json.load(f)
                # Reconstruct nested dict
                _lineage = {
                    ws_id: {did: item for did, item in decisions.items()}
                    for ws_id, decisions in data.items()
                }
            total = sum(len(decisions) for decisions in _lineage.values())
            logger.info(f"✓ Loaded {total} lineage records from disk")
        except Exception as exc:
            logger.warning(f"Failed to load lineage from disk: {exc}; starting fresh")
            _lineage = {}
    else:
        _lineage = {}


def _save_lineage_to_disk() -> None:
    """Persist lineage to disk."""
    os.makedirs(Path(_LINEAGE_FILE).parent, exist_ok=True)
    try:
        with open(_LINEAGE_FILE, "w") as f:
            json.dump(_lineage, f, indent=2, default=str)
    except Exception as exc:
        logger.error(f"Failed to save lineage to disk: {exc}")


# Load on import
_load_lineage_from_disk()


# ---------------------------------------------------------------------------
# Domain object
# ---------------------------------------------------------------------------

@dataclass
class LineageRow:
    """One persisted conclusion from a reconciliation run.

    Attributes
    ----------
    conclusion:
        Human-readable description of the decision, e.g.
        "INVOICE-1042 matched CONTRACT-88, amount verified".
    evidence_ids:
        Node IDs, edge (src, tgt) pairs, or chunk IDs that supported
        this conclusion. Stored as JSON; any JSON-serialisable value is accepted.
    decision_type:
        One of "match", "exception", "unresolved".
    confidence:
        Optional float — the F1 / confidence score already computed by
        the engine (None if not applicable).
    decision_id:
        Auto-assigned UUID if not supplied.
    """
    conclusion:    str
    evidence_ids:  list[Any]
    decision_type: str
    confidence:    float | None = None
    decision_id:   str          = field(default_factory=lambda: str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

async def persist_lineage(
    workspace_id: str,
    case_id:      str,
    rows:         list[LineageRow],
) -> int:
    """Batch-insert all lineage rows for one reconciliation run.

    Returns the number of rows inserted, or 0 if the write was skipped or
    failed. Errors are logged at WARNING level and swallowed so the caller's
    response is never affected.
    """
    if not rows:
        return 0

    now = int(time.time())
    items = []

    for row in rows:
        item = {
            "workspace_id": workspace_id,
            "decision_id": row.decision_id,
            "case_id": case_id,
            "conclusion": row.conclusion,
            "evidence_ids": row.evidence_ids,  # Stored as list, serialized on save
            "confidence": row.confidence,
            "decision_type": row.decision_type,
            "created_at": now,
        }
        items.append(item)

    try:
        async with _lineage_lock:
            if workspace_id not in _lineage:
                _lineage[workspace_id] = {}
            
            for item in items:
                decision_id = item["decision_id"]
                # Upsert: if already exists, skip (matching CockroachDB ON CONFLICT behavior)
                if decision_id not in _lineage[workspace_id]:
                    _lineage[workspace_id][decision_id] = item
            
            _save_lineage_to_disk()

        logger.info(
            "lineage: persisted %d row(s) for workspace=%s case=%s",
            len(items), workspace_id, case_id,
        )
        return len(items)

    except Exception as exc:
        # Lineage write failure must never surface to the caller.
        logger.warning(
            "lineage: write failed for workspace=%s case=%s — "
            "reconciliation response is unaffected. Error: %s",
            workspace_id, case_id, exc,
        )
        return 0


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

async def fetch_lineage(
    workspace_id: str,
    case_id:      str,
    decision_id:  str | None = None,
) -> list[dict]:
    """Fetch lineage rows for a workspace/case.

    Parameters
    ----------
    workspace_id:
        Scope for all queries — matches pattern in graph storage.
    case_id:
        The case whose decisions are requested.
    decision_id:
        When supplied, return only the single row with this UUID.
        When None, return all rows for the case ordered by created_at DESC.

    Returns
    -------
    List of dicts with keys:
        decision_id, conclusion, evidence_ids, confidence,
        decision_type, created_at (Unix timestamp)
    Raises on errors (callers — the API route — handle those).
    """
    async with _lineage_lock:
        decisions = _lineage.get(workspace_id, {})

        if decision_id is not None:
            # Fetch single decision
            item = decisions.get(decision_id)
            if not item:
                return []
            rows = [item]
        else:
            # Fetch all decisions for this case, filtered by case_id
            rows = [
                item for item in decisions.values()
                if item.get("case_id") == case_id
            ]
            # Sort by created_at DESC
            rows.sort(key=lambda x: x.get("created_at", 0), reverse=True)

    return [
        {
            "decision_id":   row.get("decision_id"),
            "conclusion":    row.get("conclusion"),
            "evidence_ids":  row.get("evidence_ids", []),
            "confidence":    row.get("confidence"),
            "decision_type": row.get("decision_type"),
            "created_at":    row.get("created_at"),  # Unix timestamp
        }
        for row in rows
    ]


# For testing: clear all lineage
async def _clear_all_lineage() -> None:
    """Clear all lineage records (for testing only)."""
    global _lineage
    async with _lineage_lock:
        _lineage = {}
        if Path(_LINEAGE_FILE).exists():
            os.remove(_LINEAGE_FILE)
        logger.info("✓ Cleared all lineage records")
