"""
Decision lineage API routes.

Endpoints
---------
GET /api/workspace/{case_id}/decisions
    Return all lineage rows for a case, ordered by created_at DESC.

GET /api/workspace/{case_id}/decisions/{decision_id}
    Return a single lineage row by its UUID.

Both endpoints scope the query to the authenticated user's workspace_id
(= case_id, following the same pattern used by the reconciliation route)
and verify case ownership before touching the database.
"""
from __future__ import annotations

import logging
import traceback

from fastapi import APIRouter, Depends, HTTPException

from backend.auth.dependencies import get_current_user
from backend.auth.middleware.jwt_middleware import AuthContext
from backend.auth.services.case_service import get_case as _verify_case_ownership
from backend.compliance.decision_lineage import fetch_lineage

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/workspace",
    tags=["Decision Lineage"],
)


@router.get("/{case_id}/decisions")
async def list_decisions(
    case_id: str,
    auth: AuthContext = Depends(get_current_user),
):
    """Return all lineage rows for a case, ordered newest-first.

    Each row contains:
    - decision_id   — UUID of the decision
    - conclusion    — human-readable description
    - evidence_ids  — list of node/chunk IDs that supported this conclusion
    - confidence    — float or null
    - decision_type — "match" | "exception" | "unresolved"
    - created_at    — ISO-8601 timestamp
    """
    try:
        await _verify_case_ownership(case_id=case_id, user_id=auth.user_id)
        rows = await fetch_lineage(workspace_id=case_id, case_id=case_id)
        return {"case_id": case_id, "total": len(rows), "decisions": rows}
    except HTTPException:
        raise
    except Exception as exc:
        tb = traceback.format_exc()
        logger.error("decisions list 500 — case=%s user=%s\n%s", case_id, auth.user_id, tb)
        raise HTTPException(status_code=500, detail=str(exc) or repr(exc)) from exc


@router.get("/{case_id}/decisions/{decision_id}")
async def get_decision(
    case_id:     str,
    decision_id: str,
    auth: AuthContext = Depends(get_current_user),
):
    """Return a single lineage row by decision_id.

    Returns 404 when no row exists for the given (case_id, decision_id) pair.
    """
    try:
        await _verify_case_ownership(case_id=case_id, user_id=auth.user_id)
        rows = await fetch_lineage(
            workspace_id=case_id,
            case_id=case_id,
            decision_id=decision_id,
        )
        if not rows:
            raise HTTPException(
                status_code=404,
                detail=f"Decision {decision_id!r} not found for case {case_id!r}",
            )
        return rows[0]
    except HTTPException:
        raise
    except Exception as exc:
        tb = traceback.format_exc()
        logger.error(
            "decisions get 500 — case=%s decision=%s user=%s\n%s",
            case_id, decision_id, auth.user_id, tb,
        )
        raise HTTPException(status_code=500, detail=str(exc) or repr(exc)) from exc
