"""In-memory job tracking for async document processing.

For production, this would be DynamoDB. For local testing, we use
an in-memory dict + file-backed persistence.

Job Item Schema (Item Type 5):
    PK: WORKSPACE#{workspace_id}
    SK: JOB#{job_id}
    
    status: "pending" | "processing" | "completed" | "failed"
    entities_extracted: int (only if status=="completed")
    relationships_extracted: int (only if status=="completed")
    error_message: str (only if status=="failed")
    created_at: Unix timestamp
    updated_at: Unix timestamp
    s3_key: str (S3 path to document)
    case_id: str
"""
import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# In-memory job store: {workspace_id: {job_id: {item}}}
_jobs: dict[str, dict[str, dict]] = {}
_jobs_lock = asyncio.Lock()

# Persistence file
_JOBS_FILE = "data/jobs.json"


def _load_jobs_from_disk() -> None:
    """Load jobs from disk on module init."""
    global _jobs
    if Path(_JOBS_FILE).exists():
        try:
            with open(_JOBS_FILE, "r") as f:
                data = json.load(f)
                # Reconstruct nested dict
                _jobs = {
                    ws_id: {jid: item for jid, item in jobs.items()}
                    for ws_id, jobs in data.items()
                }
            logger.info(f"✓ Loaded {sum(len(jobs) for jobs in _jobs.values())} jobs from disk")
        except Exception as exc:
            logger.warning(f"Failed to load jobs from disk: {exc}; starting fresh")
            _jobs = {}
    else:
        _jobs = {}


def _save_jobs_to_disk() -> None:
    """Persist jobs to disk."""
    os.makedirs(Path(_JOBS_FILE).parent, exist_ok=True)
    try:
        with open(_JOBS_FILE, "w") as f:
            json.dump(_jobs, f, indent=2, default=str)
    except Exception as exc:
        logger.error(f"Failed to save jobs to disk: {exc}")


# Load on import
_load_jobs_from_disk()


async def create_job(
    *,
    workspace_id: str,
    case_id: str,
    job_id: str,
    s3_key: str,
    status: str = "pending",
) -> dict:
    """Create a new job record.
    
    Args:
        workspace_id: Workspace ID
        case_id: Case ID
        job_id: Job ID (UUID)
        s3_key: S3 key to document
        status: Initial status (default: "pending")
    
    Returns:
        Job item dict
    """
    now = int(time.time())
    
    item = {
        "workspace_id": workspace_id,
        "case_id": case_id,
        "job_id": job_id,
        "s3_key": s3_key,
        "status": status,
        "created_at": now,
        "updated_at": now,
    }
    
    async with _jobs_lock:
        if workspace_id not in _jobs:
            _jobs[workspace_id] = {}
        _jobs[workspace_id][job_id] = item
        _save_jobs_to_disk()
    
    logger.info(f"✓ Created job: {job_id} in workspace {workspace_id}")
    return item


async def get_job(
    *,
    workspace_id: str,
    job_id: str,
) -> Optional[dict]:
    """Get a job by workspace_id and job_id.
    
    Args:
        workspace_id: Workspace ID
        job_id: Job ID
    
    Returns:
        Job item dict or None if not found
    """
    async with _jobs_lock:
        return _jobs.get(workspace_id, {}).get(job_id)


async def list_jobs(
    *,
    workspace_id: str,
    case_id: Optional[str] = None,
) -> list[dict]:
    """List all jobs for a workspace, optionally filtered by case_id.
    
    Args:
        workspace_id: Workspace ID
        case_id: Optional case ID filter
    
    Returns:
        List of job items
    """
    async with _jobs_lock:
        jobs = _jobs.get(workspace_id, {}).values()
        if case_id:
            jobs = [j for j in jobs if j.get("case_id") == case_id]
        return sorted(jobs, key=lambda j: j.get("created_at", 0), reverse=True)


async def update_job_status(
    *,
    workspace_id: str,
    job_id: str,
    status: str,
    entities_extracted: Optional[int] = None,
    relationships_extracted: Optional[int] = None,
    error_message: Optional[str] = None,
) -> Optional[dict]:
    """Update a job's status and optional result data.
    
    Args:
        workspace_id: Workspace ID
        job_id: Job ID
        status: New status ("processing", "completed", "failed", etc.)
        entities_extracted: Number of entities (only for completed jobs)
        relationships_extracted: Number of relationships (only for completed jobs)
        error_message: Error details (only for failed jobs)
    
    Returns:
        Updated job item or None if not found
    """
    async with _jobs_lock:
        job = _jobs.get(workspace_id, {}).get(job_id)
        if not job:
            logger.warning(f"Job not found: {job_id} in workspace {workspace_id}")
            return None
        
        job["status"] = status
        job["updated_at"] = int(time.time())
        
        if entities_extracted is not None:
            job["entities_extracted"] = entities_extracted
        
        if relationships_extracted is not None:
            job["relationships_extracted"] = relationships_extracted
        
        if error_message is not None:
            job["error_message"] = error_message
        
        _save_jobs_to_disk()
        logger.info(f"✓ Updated job {job_id} status to {status}")
        return job


async def delete_job(
    *,
    workspace_id: str,
    job_id: str,
) -> bool:
    """Delete a job record.
    
    Args:
        workspace_id: Workspace ID
        job_id: Job ID
    
    Returns:
        True if deleted, False if not found
    """
    async with _jobs_lock:
        if job_id in _jobs.get(workspace_id, {}):
            del _jobs[workspace_id][job_id]
            _save_jobs_to_disk()
            logger.info(f"✓ Deleted job {job_id} from workspace {workspace_id}")
            return True
        return False


# For testing: clear all jobs
async def _clear_all_jobs() -> None:
    """Clear all jobs (for testing only)."""
    global _jobs
    async with _jobs_lock:
        _jobs = {}
        if Path(_JOBS_FILE).exists():
            os.remove(_JOBS_FILE)
        logger.info("✓ Cleared all jobs")
