"""S3 graph storage for workspace-based MMKG graphs.

Provides upload/download operations for GraphML files stored in S3.

S3 Key Format:
    workspaces/{workspace_id}/graphs/{case_id}.graphml

All operations check if S3 is enabled before attempting uploads/downloads.
Falls back gracefully to local disk if S3 is disabled.
"""
import asyncio
import logging
import os
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def is_enabled() -> bool:
    """Return whether S3 graph storage is enabled."""
    return os.environ.get("DOCUMENT_STORAGE_BACKEND", "local").lower() == "s3"


def _bucket() -> str:
    """Get the S3 bucket name for graph storage."""
    bucket = os.environ.get("S3_GRAPH_BUCKET") or os.environ.get("S3_DOCUMENT_BUCKET", "").strip()
    if not bucket:
        raise RuntimeError(
            "S3_GRAPH_BUCKET or S3_DOCUMENT_BUCKET must be set when S3 is enabled"
        )
    return bucket


@lru_cache(maxsize=1)
def _client():
    """Get or create the S3 client (boto3)."""
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError(
            "boto3 is not installed; install project requirements before enabling S3"
        ) from exc

    return boto3.client("s3", region_name=os.environ.get("AWS_REGION") or None)


def get_graph_s3_key(workspace_id: str, case_id: str) -> str:
    """Return the full S3 key for a workspace graph.
    
    Args:
        workspace_id: Workspace ID
        case_id: Case ID
    
    Returns:
        S3 key: workspaces/{workspace_id}/graphs/{case_id}.graphml
    """
    return f"workspaces/{workspace_id}/graphs/{case_id}.graphml"


async def upload_graph_to_s3(
    workspace_id: str,
    case_id: str,
    graph_path: str,
) -> str:
    """Upload a local GraphML file to S3.
    
    Reads the graph from graph_path and uploads it to:
        workspaces/{workspace_id}/graphs/{case_id}.graphml
    
    If S3 is not enabled, returns the local graph_path unchanged.
    
    Args:
        workspace_id: Workspace ID
        case_id: Case ID
        graph_path: Absolute path to local GraphML file
    
    Returns:
        S3 key if uploaded, or local path if S3 disabled
    
    Raises:
        FileNotFoundError: If graph_path does not exist
        RuntimeError: If S3 operation fails
    """
    if not is_enabled():
        logger.debug(f"S3 graph storage disabled, keeping local copy: {graph_path}")
        return graph_path

    if not os.path.exists(graph_path):
        raise FileNotFoundError(f"Graph file not found: {graph_path}")

    s3_key = get_graph_s3_key(workspace_id, case_id)
    bucket = _bucket()

    try:
        # Read file content
        with open(graph_path, "rb") as f:
            graph_data = f.read()

        # Upload to S3
        logger.info(f"Uploading graph to S3: s3://{bucket}/{s3_key}")
        
        def _upload_sync():
            _client().put_object(
                Bucket=bucket,
                Key=s3_key,
                Body=graph_data,
                ContentType="application/graphml+xml",
            )
        
        await asyncio.to_thread(_upload_sync)
        
        logger.info(f"âœ… Graph uploaded to S3: {s3_key}")
        return s3_key

    except Exception as exc:
        logger.error(f"Failed to upload graph to S3: {exc}")
        raise RuntimeError(f"S3 graph upload failed: {exc}") from exc


async def download_graph_from_s3(
    workspace_id: str,
    case_id: str,
    temp_dir: str = "data/temp",
) -> str:
    """Download a graph from S3 to a temporary local path.
    
    Downloads from:
        workspaces/{workspace_id}/graphs/{case_id}.graphml
    
    To a temp file:
        {temp_dir}/{workspace_id}_{case_id}.graphml
    
    If S3 is not enabled, attempts to return a local copy if available.
    
    Args:
        workspace_id: Workspace ID
        case_id: Case ID
        temp_dir: Directory for temporary files (created if needed)
    
    Returns:
        Path to downloaded graph file
    
    Raises:
        FileNotFoundError: If graph does not exist in S3
        RuntimeError: If S3 operation fails
    """
    if not is_enabled():
        logger.debug("S3 graph storage disabled, cannot download graph")
        raise FileNotFoundError("S3 graph storage is disabled")

    s3_key = get_graph_s3_key(workspace_id, case_id)
    bucket = _bucket()
    
    # Create temp directory
    os.makedirs(temp_dir, exist_ok=True)
    
    # Generate temp file path
    temp_filename = f"{workspace_id}_{case_id}.graphml"
    temp_path = os.path.join(temp_dir, temp_filename)

    try:
        logger.info(f"Downloading graph from S3: s3://{bucket}/{s3_key}")
        
        def _download_sync():
            _client().download_file(bucket, s3_key, temp_path)
        
        await asyncio.to_thread(_download_sync)
        
        logger.info(f"âœ… Graph downloaded to temp: {temp_path}")
        return temp_path

    except _client().exceptions.NoSuchKey:
        raise FileNotFoundError(f"Graph not found in S3: {s3_key}") from None
    except Exception as exc:
        logger.error(f"Failed to download graph from S3: {exc}")
        raise RuntimeError(f"S3 graph download failed: {exc}") from exc


def cleanup_temp_graph(file_path: str) -> bool:
    """Clean up a temporary graph file.
    
    Args:
        file_path: Path to temp file to delete
    
    Returns:
        True if deleted, False if already gone or error
    """
    try:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
            logger.debug(f"Cleaned up temp graph: {file_path}")
            return True
        return False
    except Exception as exc:
        logger.warning(f"Failed to clean up temp graph {file_path}: {exc}")
        return False

