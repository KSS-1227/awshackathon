"""
Job lifecycle tracking for async document processing via S3 upload.

Uses DynamoDB for durable persistence via get_dynamodb_client().
All operations fail loudly if DynamoDB is unreachable — no fallback to in-memory.

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
import logging
import time
from typing import Optional

from .dynamodb_client import get_dynamodb_client

logger = logging.getLogger(__name__)


async def create_job(
    *,
    workspace_id: str,
    case_id: str,
    job_id: str,
    s3_key: str,
    status: str = "pending",
) -> dict:
    """Create a new job record in DynamoDB.
    
    Args:
        workspace_id: Workspace ID
        case_id: Case ID
        job_id: Job ID (UUID)
        s3_key: S3 key to document
        status: Initial status (default: "pending")
    
    Returns:
        Job item dict as stored in DynamoDB
        
    Raises:
        Exception: If DynamoDB operation fails
    """
    now = int(time.time())
    
    item = {
        "PK": f"WORKSPACE#{workspace_id}",
        "SK": f"JOB#{job_id}",
        "workspace_id": workspace_id,
        "job_id": job_id,
        "case_id": case_id,
        "s3_key": s3_key,
        "status": status,
        "created_at": now,
        "updated_at": now,
    }
    
    client = get_dynamodb_client()
    result = await client.put_item(item)
    logger.info(f"✓ Created job: {job_id} in workspace {workspace_id}")
    return result


async def get_job(
    *,
    workspace_id: str,
    job_id: str,
) -> Optional[dict]:
    """Get a job by workspace_id and job_id from DynamoDB.
    
    Args:
        workspace_id: Workspace ID
        job_id: Job ID
    
    Returns:
        Job item dict or None if not found
        
    Raises:
        Exception: If DynamoDB operation fails
    """
    pk = f"WORKSPACE#{workspace_id}"
    sk = f"JOB#{job_id}"
    
    client = get_dynamodb_client()
    item = await client.get_item(pk, sk)
    
    if item:
        logger.debug(f"✓ Retrieved job: {job_id} in workspace {workspace_id}")
    else:
        logger.debug(f"Job not found: {job_id} in workspace {workspace_id}")
    
    return item


async def list_jobs(
    *,
    workspace_id: str,
    case_id: Optional[str] = None,
) -> list[dict]:
    """List all jobs for a workspace, optionally filtered by case_id.
    
    Queries DynamoDB for all JOB# items under the workspace, then filters
    by case_id in Python if provided.
    
    Args:
        workspace_id: Workspace ID
        case_id: Optional case ID filter
    
    Returns:
        List of job items sorted by created_at descending (newest first)
        
    Raises:
        Exception: If DynamoDB operation fails
    """
    pk = f"WORKSPACE#{workspace_id}"
    sk_prefix = "JOB#"
    
    client = get_dynamodb_client()
    jobs = await client.query(pk, sk_prefix=sk_prefix, limit=1000)
    
    # Filter by case_id if provided
    if case_id:
        jobs = [j for j in jobs if j.get("case_id") == case_id]
    
    # Sort by created_at descending
    jobs.sort(key=lambda j: j.get("created_at", 0), reverse=True)
    
    logger.debug(f"✓ Listed {len(jobs)} jobs for workspace {workspace_id}" +
                 (f" filtered to case {case_id}" if case_id else ""))
    return jobs


async def update_job_status(
    *,
    workspace_id: str,
    job_id: str,
    status: str,
    entities_extracted: Optional[int] = None,
    relationships_extracted: Optional[int] = None,
    error_message: Optional[str] = None,
) -> Optional[dict]:
    """Update a job's status and optional result data in DynamoDB.
    
    Args:
        workspace_id: Workspace ID
        job_id: Job ID
        status: New status ("processing", "completed", "failed", etc.)
        entities_extracted: Number of entities (only for completed jobs)
        relationships_extracted: Number of relationships (only for completed jobs)
        error_message: Error details (only for failed jobs)
    
    Returns:
        Updated job item or None if not found
        
    Raises:
        Exception: If DynamoDB operation fails
    """
    pk = f"WORKSPACE#{workspace_id}"
    sk = f"JOB#{job_id}"
    now = int(time.time())
    
    # Build update expression and attribute values
    set_parts = ["#status = :status", "#updated = :updated"]
    attr_values = {
        ":status": status,
        ":updated": now,
    }
    attr_names = {
        "#status": "status",
        "#updated": "updated_at",
    }
    
    # Add optional fields if provided
    if entities_extracted is not None:
        set_parts.append("#entities = :entities")
        attr_values[":entities"] = entities_extracted
        attr_names["#entities"] = "entities_extracted"
    
    if relationships_extracted is not None:
        set_parts.append("#relationships = :relationships")
        attr_values[":relationships"] = relationships_extracted
        attr_names["#relationships"] = "relationships_extracted"
    
    if error_message is not None:
        set_parts.append("#error = :error")
        attr_values[":error"] = error_message
        attr_names["#error"] = "error_message"
    
    update_expr = "SET " + ", ".join(set_parts)
    
    client = get_dynamodb_client()
    result = await client.update_item(pk, sk, update_expr, attr_values, attr_names)
    
    logger.info(f"✓ Updated job {job_id} status to {status}")
    return result


async def delete_job(
    *,
    workspace_id: str,
    job_id: str,
) -> bool:
    """Delete a job record from DynamoDB.
    
    Args:
        workspace_id: Workspace ID
        job_id: Job ID
    
    Returns:
        True if deleted, False if not found
        
    Raises:
        Exception: If DynamoDB operation fails
    """
    pk = f"WORKSPACE#{workspace_id}"
    sk = f"JOB#{job_id}"
    
    client = get_dynamodb_client()
    was_deleted = await client.delete_item(pk, sk)
    
    if was_deleted:
        logger.info(f"✓ Deleted job {job_id} from workspace {workspace_id}")
    else:
        logger.warning(f"Job not found for deletion: {job_id} in workspace {workspace_id}")
    
    return was_deleted
