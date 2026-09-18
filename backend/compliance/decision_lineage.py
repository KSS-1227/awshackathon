"""
Decision lineage persistence and retrieval.

This module is the single point of contact for the ``decision_lineage`` table.
It reuses the shared async connection pool from ``cockroach_graph_storage``
(the same ``_get_pool`` / open-on-first-use pattern used throughout the
codebase) so there is never a second database connection strategy in play.

Public API
----------
persist_lineage(workspace_id, case_id, rows)
    Batch-insert one row per conclusion produced by a reconciliation run.
    Wrapped in error-handling that logs but never raises — a lineage write
    failure must never break the caller's response.

fetch_lineage(workspace_id, case_id, decision_id=None)
    Return lineage rows for a workspace/case, optionally filtered to a
    single decision_id.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from backend.cockroach_graph_storage import _get_pool

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
        this conclusion.  Stored as JSONB; any JSON-serialisable value
        is accepted.
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
# Pool helper — identical pattern to CockroachGraphStorage._pool()
# ---------------------------------------------------------------------------

async def _pool():
    pool = _get_pool()
    if pool.closed:
        await pool.open()
    return pool


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

async def persist_lineage(
    workspace_id: str,
    case_id:      str,
    rows:         list[LineageRow],
) -> int:
    """Batch-insert all lineage rows for one reconciliation run.

    Uses a single ``executemany`` call — one network round-trip regardless
    of how many conclusions were produced.

    Returns the number of rows inserted, or 0 if the write was skipped or
    failed.  Errors are logged at WARNING level and swallowed so the caller's
    response is never affected.
    """
    if not rows:
        return 0

    params = [
        (
            row.decision_id,
            workspace_id,
            case_id,
            row.conclusion,
            json.dumps(row.evidence_ids),
            row.confidence,
            row.decision_type,
        )
        for row in rows
    ]

    try:
        pool = await _pool()
        async with pool.connection() as conn:
            await conn.executemany(
                """
                INSERT INTO decision_lineage
                    (decision_id, workspace_id, case_id, conclusion,
                     evidence_ids, confidence, decision_type)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s)
                ON CONFLICT (workspace_id, decision_id) DO NOTHING
                """,
                params,
            )
        logger.info(
            "lineage: persisted %d row(s) for workspace=%s case=%s",
            len(rows), workspace_id, case_id,
        )
        return len(rows)

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
        Scope for all DB queries — matches the pattern in graph_nodes.
    case_id:
        The case whose decisions are requested.
    decision_id:
        When supplied, return only the single row with this UUID.
        When None, return all rows for the case ordered by created_at DESC.

    Returns
    -------
    List of dicts with keys:
        decision_id, conclusion, evidence_ids, confidence,
        decision_type, created_at (ISO-8601 string)
    Raises on DB errors (callers — the API route — handle those).
    """
    pool = await _pool()

    if decision_id is not None:
        async with pool.connection() as conn:
            rows = await (await conn.execute(
                """
                SELECT decision_id, conclusion, evidence_ids,
                       confidence, decision_type, created_at
                FROM   decision_lineage
                WHERE  workspace_id = %s
                  AND  case_id      = %s
                  AND  decision_id  = %s
                """,
                (workspace_id, case_id, decision_id),
            )).fetchall()
    else:
        async with pool.connection() as conn:
            rows = await (await conn.execute(
                """
                SELECT decision_id, conclusion, evidence_ids,
                       confidence, decision_type, created_at
                FROM   decision_lineage
                WHERE  workspace_id = %s
                  AND  case_id      = %s
                ORDER  BY created_at DESC
                """,
                (workspace_id, case_id),
            )).fetchall()

    return [
        {
            "decision_id":   str(row[0]),
            "conclusion":    row[1],
            "evidence_ids":  row[2] if isinstance(row[2], list) else json.loads(row[2] or "[]"),
            "confidence":    row[3],
            "decision_type": row[4],
            "created_at":    row[5].isoformat() if hasattr(row[5], "isoformat") else str(row[5]),
        }
        for row in rows
    ]
