"""Private S3 storage for workspace-based document uploads with DynamoDB job tracking.

S3 is the durable copy for documents uploaded to workspaces. When a document is uploaded,
a corresponding Job record is created in DynamoDB to track the async processing pipeline.

S3 Key Format (workspace-based):
    workspaces/{workspace_id}/documents/{case_id}/{job_id}_{document_uuid}.{ext}

Job ID is embedded in the S3 key to enable Lambda to recover job_id from EventBridge
EventBridge events without requiring a DynamoDB scan.

DynamoDB Job Item (Item Type 5):
    PK: WORKSPACE#{workspace_id}
    SK: JOB#{job_id}
    
Phase 2 TODO: Lambda will extract entities and build graphs from uploaded documents.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class S3StorageConfigurationError(RuntimeError):
    """Raised when S3 storage is selected without a usable bucket."""


def is_enabled() -> bool:
    """Return whether the explicit production S3 backend is enabled."""
    return os.environ.get("DOCUMENT_STORAGE_BACKEND", "local").lower() == "s3"


def _bucket() -> str:
    bucket = os.environ.get("S3_DOCUMENT_BUCKET", "").strip()
    if not bucket:
        raise S3StorageConfigurationError(
            "S3_DOCUMENT_BUCKET must be set when DOCUMENT_STORAGE_BACKEND=s3"
        )
    return bucket


@lru_cache(maxsize=1)
def _client():
    try:
        import boto3
    except ImportError as exc:
        raise S3StorageConfigurationError(
            "boto3 is not installed; install the project requirements before enabling S3"
        ) from exc

    return boto3.client("s3", region_name=os.environ.get("AWS_REGION") or None)


def _sanitize_filename(filename: str) -> str:
    """Sanitize filename: replace unsafe chars with underscores."""
    name = Path(filename).name
    # Replace [^\w.\-] (anything that's not word char, dot, or dash) with underscore
    name = re.sub(r"[^\w.\-]", "_", name)
    # Limit to 255 chars
    return (name or "unnamed_file")[:255]


def upload_key(user_id: str, case_id: str, filename: str) -> str:
    """Return the private, tenant-scoped key for a raw document (legacy).
    
    DEPRECATED: Use workspace-based paths via upload_document_and_create_job().
    """
    return f"users/{user_id}/cases/{case_id}/uploads/{filename}"


async def upload_document(
    *,
    user_id: str,
    case_id: str,
    filename: str,
    data: bytes,
    content_type: str,
) -> str | None:
    """Durably store one upload when S3 is enabled; otherwise return None.
    
    DEPRECATED: Use upload_document_and_create_job() for workspace-based uploads.
    """
    if not is_enabled():
        return None

    bucket = _bucket()
    key = upload_key(user_id, case_id, filename)

    def _put() -> None:
        _client().put_object(
            Bucket=bucket,
            Key=key,
            Body=data,
            ContentType=content_type or "application/octet-stream",
            ServerSideEncryption="AES256",
        )

    await asyncio.to_thread(_put)
    return key


async def upload_document_and_create_job(
    *,
    workspace_id: str,
    case_id: str,
    filename: str,
    data: bytes,
    content_type: Optional[str] = None,
) -> dict:
    """Upload document to workspace storage and create DynamoDB job record.
    
    Workflow:
    1. Generate job_id (UUID)
    2. Sanitize filename and generate document UUID
    3. Upload file to S3 (if enabled) or local storage (if disabled)
       S3: workspaces/{workspace_id}/documents/{case_id}/{job_id}_{doc_uuid}.{ext}
       Local: data/uploads/workspaces/{workspace_id}/documents/{case_id}/{job_id}_{doc_uuid}.{ext}
    4. Create DynamoDB Job record (Item Type 5) with status=pending
    5. Return job metadata
    
    Args:
        workspace_id: Workspace ID (required)
        case_id: Case ID (required)
        filename: Original filename (will be sanitized)
        data: Document bytes
        content_type: MIME type (defaults to application/octet-stream)
    
    Returns:
        Dict with keys:
            - job_id: UUID of the job
            - s3_key: Full S3 key where document is stored
            - status: "pending"
            - created_at: Unix timestamp
            - workspace_id: Workspace ID
            - case_id: Case ID
            - filename: Original filename
            - size_bytes: Document size
    
    Raises:
        ValueError: If workspace_id, case_id, filename, or data is missing
        S3StorageConfigurationError: If S3 not properly configured (when S3 is enabled)
        ClientError: If DynamoDB or S3 operations fail
    
    Phase 2 TODO: Lambda will receive S3 event via EventBridge, extract job_id from
    the S3 key, query DynamoDB for the job record, and begin entity extraction and
    knowledge graph construction.
    """
    if not workspace_id:
        raise ValueError("workspace_id is required")
    if not case_id:
        raise ValueError("case_id is required")
    if not filename:
        raise ValueError("filename is required")
    if not data:
        raise ValueError("data is required")

    # Import here to avoid circular dependency at module load time
    from backend.storage.dynamodb_jobs import create_job

    # Generate IDs
    job_id = str(uuid.uuid4())
    doc_uuid = uuid.uuid4().hex[:12]
    
    # Sanitize and extract extension
    safe_filename = _sanitize_filename(filename)
    ext = Path(safe_filename).suffix or ".bin"
    
    # Build S3 key: workspaces/{workspace_id}/documents/{case_id}/{job_id}_{doc_uuid}.{ext}
    s3_key = f"workspaces/{workspace_id}/documents/{case_id}/{job_id}_{doc_uuid}{ext}"
    
    if is_enabled():
        # S3 enabled: upload to S3
        bucket = _bucket()
        
        def _put() -> None:
            _client().put_object(
                Bucket=bucket,
                Key=s3_key,
                Body=data,
                ContentType=content_type or "application/octet-stream",
                ServerSideEncryption="AES256",
                Metadata={
                    "job_id": job_id,
                    "workspace_id": workspace_id,
                    "case_id": case_id,
                },
            )
        
        await asyncio.to_thread(_put)
        logger.info(f"✅ Uploaded document to S3: {s3_key}")
    else:
        # S3 disabled: use local storage at data/uploads/{workspace_id}/documents/{case_id}/...
        local_path = Path("data/uploads") / s3_key.replace("workspaces/", "")
        local_path.parent.mkdir(parents=True, exist_ok=True)
        
        def _write() -> None:
            with open(local_path, "wb") as f:
                f.write(data)
        
        await asyncio.to_thread(_write)
        logger.info(f"✅ Saved document locally: {local_path}")
    
    # Create DynamoDB job record (Item Type 5)
    now = int(time.time())
    job_item = await create_job(
        workspace_id=workspace_id,
        case_id=case_id,
        job_id=job_id,
        s3_key=s3_key,
        status="pending",
    )
    logger.info(f"✅ Created DynamoDB job record: {job_id}")
    
    return {
        "job_id": job_id,
        "s3_key": s3_key,
        "status": "pending",
        "created_at": now,
        "workspace_id": workspace_id,
        "case_id": case_id,
        "filename": filename,
        "size_bytes": len(data),
    }


async def delete_case_documents(
    *,
    workspace_id: str,
    case_id: str,
) -> int:
    """Delete all documents for a case in a workspace when S3 is enabled.
    
    Updated to use workspace-based paths:
        workspaces/{workspace_id}/documents/{case_id}/
    
    Args:
        workspace_id: Workspace ID
        case_id: Case ID
    
    Returns:
        Number of documents deleted
    """
    if not is_enabled():
        return 0

    bucket = _bucket()
    prefix = f"workspaces/{workspace_id}/documents/{case_id}/"

    def _delete() -> int:
        client = _client()
        deleted = 0
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            objects = [{"Key": item["Key"]} for item in page.get("Contents", [])]
            if not objects:
                continue
            client.delete_objects(Bucket=bucket, Delete={"Objects": objects, "Quiet": True})
            deleted += len(objects)
        logger.info(f"Deleted {deleted} documents for case {case_id} from workspace {workspace_id}")
        return deleted

    return await asyncio.to_thread(_delete)


async def get_s3_document_metadata(
    *,
    workspace_id: str,
    case_id: str,
    job_id: str,
) -> Optional[dict]:
    """Get S3 object metadata for a document.
    
    Used for future retrieval and validation of documents.
    
    Args:
        workspace_id: Workspace ID
        case_id: Case ID
        job_id: Job ID
    
    Returns:
        Dict with metadata or None if not found
    """
    if not is_enabled():
        return None

    bucket = _bucket()
    prefix = f"workspaces/{workspace_id}/documents/{case_id}/"

    def _head() -> Optional[dict]:
        client = _client()
        try:
            paginator = client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    # Check if this key contains the job_id
                    if f"{job_id}_" in key:
                        response = client.head_object(Bucket=bucket, Key=key)
                        return {
                            "key": key,
                            "size": response.get("ContentLength"),
                            "content_type": response.get("ContentType"),
                            "last_modified": response.get("LastModified"),
                            "metadata": response.get("Metadata", {}),
                        }
            return None
        except client.exceptions.NoSuchKey:
            return None

    return await asyncio.to_thread(_head)


async def download_from_s3_to_temp(s3_key: str, temp_dir: str = "data/temp") -> str:
    """Download a document from S3 to a temporary local file.
    
    The builder needs a real file path on disk, not an S3 key.
    This function downloads from S3 and returns the local path.
    The caller is responsible for cleanup via cleanup_temp_file().
    
    For local testing without S3, stores documents in a local directory structure
    and retrieves them from there instead.
    
    Args:
        s3_key: Full S3 key (e.g., "workspaces/ws-123/documents/case-1/job-456_abc123.pdf")
        temp_dir: Directory for temporary files (default: "data/temp")
    
    Returns:
        Local file path where the document was downloaded
    
    Raises:
        FileNotFoundError: If document doesn't exist
        IOError: If file write fails
    """
    os.makedirs(temp_dir, exist_ok=True)
    
    # Extract filename from S3 key for local temp file
    filename = Path(s3_key).name
    local_path = str(Path(temp_dir) / filename)
    
    if is_enabled():
        # S3 enabled: download from S3
        bucket = _bucket()
        
        def _get() -> None:
            client = _client()
            try:
                client.download_file(bucket, s3_key, local_path)
            except client.exceptions.NoSuchKey as exc:
                raise FileNotFoundError(f"S3 object not found: {s3_key}") from exc
        
        await asyncio.to_thread(_get)
        logger.info(f"📥 Downloaded from S3: {s3_key} → {local_path}")
    else:
        # S3 disabled: use local storage (data/uploads/{workspace_id}/documents/{case_id}/...)
        # This is for local testing without AWS
        source_path = str(Path("data/uploads") / s3_key.replace("workspaces/", ""))
        
        if not Path(source_path).exists():
            raise FileNotFoundError(f"Local document not found: {source_path}")
        
        # Copy from local source to temp location
        def _copy() -> None:
            import shutil
            shutil.copy(source_path, local_path)
        
        await asyncio.to_thread(_copy)
        logger.info(f"📁 Copied from local: {source_path} → {local_path}")
    
    return local_path


def cleanup_temp_file(file_path: str) -> bool:
    """Delete a temporary file.
    
    Used to clean up temp files created by download_from_s3_to_temp().
    This ensures transient builder working files don't accumulate on disk.
    
    Args:
        file_path: Path to the temporary file
    
    Returns:
        True if file was deleted, False if file didn't exist
    """
    if not file_path or not os.path.exists(file_path):
        return False
    
    try:
        os.remove(file_path)
        logger.info(f"???  Cleaned up temp file: {file_path}")
        return True
    except Exception as exc:
        logger.error(f"??  Failed to clean up temp file {file_path}: {exc}")
        return False


def extract_entity_counts_from_graph(output_dir: str) -> tuple[int, int]:
    """Extract entity and relationship counts from the built graph.
    
    After MMKGBuilder completes, reads the final GraphML file to count
    entities (nodes) and relationships (edges).
    
    Args:
        output_dir: MMKGBuilder's output directory (where {mmkg_name}.graphml is saved)
    
    Returns:
        Tuple of (entity_count, relationship_count) or (0, 0) if graph not found
    """
    import networkx as nx
    
    # Try to find the most recent GraphML file
    graph_files = list(Path(output_dir).glob("*.graphml"))
    if not graph_files:
        logger.warning(f"No GraphML files found in {output_dir}")
        return 0, 0
    
    # Use the most recently modified file
    graph_path = max(graph_files, key=lambda p: p.stat().st_mtime)
    
    try:
        G = nx.read_graphml(str(graph_path))
        entity_count = G.number_of_nodes()
        relationship_count = G.number_of_edges()
        logger.info(f"?? Graph summary: {entity_count} entities, {relationship_count} relationships")
        return entity_count, relationship_count
    except Exception as exc:
        logger.error(f"Failed to read graph from {graph_path}: {exc}")
        return 0, 0
