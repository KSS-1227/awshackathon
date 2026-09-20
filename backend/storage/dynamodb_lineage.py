"""
Decision lineage persistence and retrieval — DynamoDB-backed.

Uses DynamoDB for durable persistence via get_dynamodb_client().
All operations fail loudly if DynamoDB is unreachable — no fallback to in-memory.

Lineage Item Schema:
    PK: WORKSPACE#{workspace_id}
    SK: LINEAGE#{decision_id}
    
    case_id: str
    conclusion: str (human-readable decision description)
    evidence_ids: list (JSON-serializable)
    confidence: float | None (converted to Decimal for DynamoDB)
    decision_type: str ("match", "exception", "unresolved")
    created_at: Unix timestamp
"""
import logging
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

from .dynamodb_client import get_dynamodb_client

logger = logging.getLogger(__name__)


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
        this conclusion. Stored as list in DynamoDB; any JSON-serializable 
        value is accepted.
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
    decision_id:   str          = None
    
    def __post_init__(self):
        """Auto-assign decision_id if not provided."""
        if self.decision_id is None:
            self.decision_id = str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

async def persist_lineage(
    workspace_id: str,
    case_id:      str,
    rows:         list[LineageRow],
) -> int:
    """Batch-insert all lineage rows for one reconciliation run into DynamoDB.

    Returns the number of rows inserted. Errors are logged at WARNING level
    and swallowed so the caller's response is never affected — but this means
    lineage writes can fail silently. Consider this the best-effort pattern
    for audit trails.

    Args:
        workspace_id: Workspace scope (PK prefix)
        case_id: Case being reconciled
        rows: List of LineageRow objects to persist
    
    Returns:
        Number of rows inserted (0 if write failed)
        
    Raises:
        No exceptions — failures are logged but never propagate
    """
    if not rows:
        return 0

    now = int(time.time())
    client = get_dynamodb_client()
    inserted = 0

    for row in rows:
        item = {
            "PK": f"WORKSPACE#{workspace_id}",
            "SK": f"LINEAGE#{row.decision_id}",
            "workspace_id": workspace_id,
            "decision_id": row.decision_id,
            "case_id": case_id,
            "conclusion": row.conclusion,
            "evidence_ids": row.evidence_ids,
            "confidence": Decimal(str(row.confidence)) if row.confidence is not None else None,
            "decision_type": row.decision_type,
            "created_at": now,
        }
        
        try:
            await client.put_item(item)
            inserted += 1
        except Exception as exc:
            # Lineage write failure must never surface to the caller
            logger.warning(
                f"lineage: write failed for workspace={workspace_id} case={case_id} "
                f"decision_id={row.decision_id} — "
                f"reconciliation response is unaffected. Error: {exc}",
            )
    
    if inserted > 0:
        logger.info(
            f"lineage: persisted {inserted}/{len(rows)} row(s) for workspace={workspace_id} case={case_id}",
        )
    
    return inserted


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

async def fetch_lineage(
    workspace_id: str,
    case_id:      str,
    decision_id:  str | None = None,
) -> list[dict]:
    """Fetch lineage rows for a workspace/case from DynamoDB.

    Parameters
    ----------
    workspace_id:
        Workspace scope — matches pattern in graph storage.
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
        
    Raises:
        Exception: If DynamoDB operation fails
    """
    pk = f"WORKSPACE#{workspace_id}"
    client = get_dynamodb_client()
    
    if decision_id is not None:
        # Fetch single decision
        sk = f"LINEAGE#{decision_id}"
        item = await client.get_item(pk, sk)
        rows = [item] if item else []
    else:
        # Query all lineage items for this workspace, then filter by case_id
        items = await client.query(pk, sk_prefix="LINEAGE#", limit=1000)
        rows = [item for item in items if item.get("case_id") == case_id]
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
