"""
Workspace-aware reconciliation route.

Endpoint
--------
    POST /api/workspace/{case_id}/reconcile

Reconciles every INVOICE entity in the case's knowledge graph against its
vendor's CONTRACT_AMOUNT, returning a matched / exception / unresolved summary.
Pure graph traversal + regex (no LLM), so it responds fast.

The workspace is resolved server-side from (user_id, case_id):
    data/users/{user_id}/cases/{case_id}/working/
The CockroachDB workspace_id for a case equals its case_id (see
WorkspaceDocumentService), so the case_id is passed straight through as the
graph scope.
"""
from __future__ import annotations

import logging
import traceback
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

from backend.auth.dependencies import get_current_user
from backend.auth.middleware.jwt_middleware import AuthContext
from backend.auth.services.case_service import get_case as _verify_case_ownership
from backend.auth.workspace import UserWorkspace
from backend.compliance.reconciliation_engine import ReconciliationEngine

router = APIRouter(
    prefix="/workspace",
    tags=["Reconciliation"],
)


class ReconcileRequest(BaseModel):
    # Optional override for the directory holding kv_store_text_chunks.json.
    # For safety it is only honored when it resolves inside the caller's own
    # workspace root; otherwise the server-resolved working dir is used.
    working_dir: str | None = None


@router.post("/{case_id}/reconcile")
async def reconcile_workspace(
    case_id: str,
    request: ReconcileRequest | None = None,
    auth: AuthContext = Depends(get_current_user),
):
    """Reconcile invoices against vendor contracts for a single case workspace.

    - case_id comes from the path, user_id from the JWT.
    - Ownership is verified before any graph access.
    - The workspace path is resolved server-side; a client-supplied working_dir
      is only accepted when it points inside this user's own workspace.
    - If graph exists in S3, download to temp before reconciliation.
    """
    temp_graph_path = None
    
    try:
        await _verify_case_ownership(case_id=case_id, user_id=auth.user_id)

        workspace = UserWorkspace(user_id=auth.user_id, case_id=case_id)
        working_dir = str(workspace.working)

        if request and request.working_dir:
            candidate = Path(request.working_dir).resolve()
            if not candidate.is_relative_to(workspace.root.resolve()):
                raise HTTPException(
                    status_code=400,
                    detail="working_dir must be inside the case workspace",
                )
            working_dir = str(candidate)

        # Check if graph exists in S3 and download to temp if needed
        try:
            from backend.storage.s3_graph_storage import (
                download_graph_from_s3,
                cleanup_temp_graph,
                is_enabled as s3_enabled,
            )
            
            if s3_enabled():
                logger.info(f"Attempting to download graph from S3 for case {case_id}")
                try:
                    temp_graph_path = await download_graph_from_s3(
                        workspace_id=case_id,
                        case_id=case_id,
                        temp_dir="data/temp",
                    )
                    logger.info(f"✅ Downloaded graph from S3 to: {temp_graph_path}")
                    # TODO: If needed in future, copy temp graph to working_dir for reconciliation engine
                except FileNotFoundError:
                    logger.debug(f"No S3 graph found for case {case_id}, using local graph")
                except Exception as exc:
                    logger.warning(f"Failed to download S3 graph: {exc}, falling back to local")
        except ImportError:
            logger.debug("S3 graph storage module not available, using local graph")

        engine = ReconciliationEngine(workspace_id=case_id, working_dir=working_dir)
        return await engine.reconcile()
    except HTTPException:
        raise
    except Exception as exc:
        tb = traceback.format_exc()
        logger.error("Reconcile 500 — case=%s user=%s\n%s", case_id, auth.user_id, tb)
        raise HTTPException(status_code=500, detail=str(exc) or repr(exc)) from exc
    finally:
        # Clean up temp graph file if downloaded
        if temp_graph_path:
            try:
                from backend.storage.s3_graph_storage import cleanup_temp_graph
                cleanup_temp_graph(temp_graph_path)
            except Exception as cleanup_exc:
                logger.warning(f"Failed to clean up temp graph: {cleanup_exc}")
