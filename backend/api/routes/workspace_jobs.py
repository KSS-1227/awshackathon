"""Workspace job tracking and status polling endpoints.

Allows clients to:
- Get status of a specific job (with entity/relationship counts on completion)
- List all jobs for a workspace or case
- Query job details and results
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from backend.auth.middleware.jwt_middleware import AuthContext
from backend.auth.dependencies import get_current_user
from backend.storage.dynamodb_jobs import get_job, list_jobs

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/workspace-jobs",
    tags=["Workspace Job Tracking"],
)


@router.get("/{job_id}")
async def get_job_status(
    job_id: str,
    auth: AuthContext = Depends(get_current_user),
) -> dict:
    """Get the status of a specific job.
    
    Returns job metadata including:
    - status: "pending", "processing", "completed", or "failed"
    - entities_extracted: Number of entities (if completed)
    - relationships_extracted: Number of relationships (if completed)
    - error_message: Error details (if failed)
    - created_at, updated_at: Timestamps
    
    Args:
        job_id: Job ID to query
        auth: Current user context
    
    Returns:
        Job metadata dict
    
    Raises:
        HTTPException 404: If job not found
    """
    workspace_id = auth.workspace_id
    
    job = await get_job(workspace_id=workspace_id, job_id=job_id)
    if not job:
        raise HTTPException(
            status_code=404,
            detail=f"Job {job_id} not found in workspace {workspace_id}",
        )
    
    logger.info(f"Fetched job {job_id} status: {job.get('status')}")
    return job


@router.get("")
async def list_workspace_jobs(
    case_id: Optional[str] = None,
    status: Optional[str] = None,
    auth: AuthContext = Depends(get_current_user),
) -> dict:
    """List all jobs for the workspace.
    
    Optionally filter by case_id or status.
    
    Args:
        case_id: Optional case ID filter
        status: Optional status filter ("pending", "processing", "completed", "failed")
        auth: Current user context
    
    Returns:
        Dict with:
        - workspace_id: Workspace ID
        - case_id: Filter case_id (if provided)
        - jobs: List of job items
        - total: Total count
        - by_status: Breakdown by status
    """
    workspace_id = auth.workspace_id
    
    jobs = await list_jobs(workspace_id=workspace_id, case_id=case_id)
    
    # Apply status filter if provided
    if status:
        jobs = [j for j in jobs if j.get("status") == status]
    
    # Count by status
    by_status = {}
    for job in jobs:
        s = job.get("status", "unknown")
        by_status[s] = by_status.get(s, 0) + 1
    
    logger.info(
        f"Listed {len(jobs)} jobs for workspace {workspace_id}"
        f"{f' case {case_id}' if case_id else ''}"
    )
    
    return {
        "workspace_id": workspace_id,
        "case_id": case_id,
        "jobs": jobs,
        "total": len(jobs),
        "by_status": by_status,
    }
