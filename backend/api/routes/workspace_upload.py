"""
Workspace Upload Pipeline — Step 2 Integration.

Wires the upload stub to MMKGBuilder via BackgroundTasks:
1. Receives file upload from endpoint
2. Uploads document to S3 and creates DynamoDB job (via upload_document_and_create_job)
3. Queues background processing task
4. Returns job IDs and next steps to client

Background task flow:
1. Download file from S3 to temp location
2. Initialize MMKGBuilder with workspace-scoped directories
3. Call builder.index_many() to run extraction pipeline
4. Extract entity/relationship counts from output graph
5. Update job status to completed with counts
6. On error: update status to failed with error message
7. Finally: clean up temp file

Requirements: Phase 2 Step 2
"""

import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile

from backend.auth.middleware.jwt_middleware import AuthContext
from backend.auth.dependencies import get_current_user
from backend.builder import MMKGBuilder
from backend.storage.s3_document_storage import (
    upload_document_and_create_job,
    download_from_s3_to_temp,
    cleanup_temp_file,
    extract_entity_counts_from_graph,
)
from backend.storage.dynamodb_jobs import update_job_status

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/workspace-upload",
    tags=["Workspace Document Upload"],
)

# Supported file extensions for extraction pipeline
SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx",
    ".xlsx", ".xls",
    ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff",
    ".mp3", ".wav", ".m4a", ".flac", ".ogg",
}


async def process_uploaded_job(
    workspace_id: str,
    case_id: str,
    job_id: str,
    s3_key: str,
) -> None:
    """Background task: download, extract, and build knowledge graph.
    
    Workflow:
    1. Update job status to processing
    2. Download file from S3 to temp location
    3. Initialize MMKGBuilder with workspace-scoped directories
    4. Run extraction pipeline via builder.index_many([local_path])
    5. Extract entity/relationship counts from output graph
    6. Update job status to completed with counts
    7. On exception: update status to failed
    8. Finally: clean up temp file
    
    Args:
        workspace_id: Workspace ID for this job
        case_id: Case ID this document belongs to
        job_id: Job ID for tracking in DynamoDB
        s3_key: S3 key to download from
    """
    local_path = None
    
    try:
        # Step 1: Update job status to processing
        logger.info(
            f"Processing job {job_id}: workspace={workspace_id}, case={case_id}"
        )
        await update_job_status(
            workspace_id=workspace_id,
            job_id=job_id,
            status="processing",
        )
        
        # Step 2: Download file from S3 to temp location
        logger.info(f"Downloading document from S3: {s3_key}")
        local_path = await download_from_s3_to_temp(s3_key)
        logger.info(f"Downloaded to temp file: {local_path}")
        
        # Step 3: Initialize MMKGBuilder with workspace-scoped directories
        working_dir = f"data/uploads/{case_id}/working"
        output_dir = f"data/uploads/{case_id}/output"
        
        logger.info(
            f"Initializing builder: working_dir={working_dir}, "
            f"output_dir={output_dir}"
        )
        
        builder = MMKGBuilder(
            working_dir=working_dir,
            output_dir=output_dir,
            workspace_id=workspace_id,
        )
        
        # Step 4: Run extraction pipeline
        logger.info(f"Starting extraction pipeline for: {Path(local_path).name}")
        results, entity_count, relationship_count = await builder.index_many([local_path])
        
        # Step 5: Check results
        filename = Path(local_path).name
        result_status = results.get(filename, "unknown")
        
        if "failed" in result_status:
            # If builder reports failure, raise error
            error_msg = f"Extraction pipeline failed: {result_status}"
            logger.error(error_msg)
            raise RuntimeError(error_msg)
        
        if result_status == "skipped":
            # Document was already processed, still mark as completed with counts from builder
            logger.info(f"Document {filename} was already indexed, marking as completed with {entity_count} entities, {relationship_count} relationships")
        else:
            # Builder returned the counts directly
            logger.info(f"Builder returned: {entity_count} entities, {relationship_count} relationships")
        
        # Step 6: Update job status to completed with counts
        logger.info(
            f"Job {job_id} completed: {entity_count} entities, "
            f"{relationship_count} relationships"
        )
        await update_job_status(
            workspace_id=workspace_id,
            job_id=job_id,
            status="completed",
            entities_extracted=entity_count,
            relationships_extracted=relationship_count,
        )
        
    except Exception as exc:
        # Step 7: On exception, update status to failed
        error_message = str(exc)
        logger.error(f"Job {job_id} failed: {error_message}")
        
        try:
            await update_job_status(
                workspace_id=workspace_id,
                job_id=job_id,
                status="failed",
                error_message=error_message,
            )
        except Exception as update_exc:
            logger.error(f"Failed to update job status to failed: {update_exc}")
    
    finally:
        # Step 8: Clean up temp file
        if local_path:
            cleanup_temp_file(local_path)


@router.post("/upload")
async def upload_documents(
    files: list[UploadFile] = File(...),
    case_id: Optional[str] = None,
    background_tasks: BackgroundTasks = BackgroundTasks(),
    auth: AuthContext = Depends(get_current_user),
) -> dict:
    """Upload documents to workspace and queue extraction pipeline.
    
    Workflow:
    1. Validate authenticated user
    2. Create or validate case
    3. For each supported file:
       - Read file content
       - Upload to S3 and create DynamoDB job
       - Queue background processing task
    4. Return job IDs, S3 keys, and next_steps guidance
    
    Args:
        files: List of files to upload
        case_id: Optional case ID (generated if not provided)
        background_tasks: FastAPI BackgroundTasks for async processing
        auth: Current user context from JWT
    
    Returns:
        Dict with:
        - success: True if at least one file was queued
        - case_id: Case ID for this upload
        - workspace_id: Workspace ID from JWT
        - jobs: List of job metadata dicts
        - files: Per-file status (uploaded, skipped, failed)
        - next_steps: Guidance for client
    
    Raises:
        HTTPException 400: If no files provided or all files unsupported
        HTTPException 500: If S3 or DynamoDB operations fail
    """
    
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")
    
    workspace_id = auth.workspace_id
    
    # If case_id not provided, generate one
    if not case_id:
        import uuid
        case_id = str(uuid.uuid4())
        logger.info(f"Generated new case_id: {case_id}")
    
    logger.info(
        f"Uploading documents: workspace={workspace_id}, case={case_id}, "
        f"file_count={len(files)}"
    )
    
    job_ids: list[str] = []
    s3_keys: list[str] = []
    file_status: list[dict] = []
    
    # Process each file
    for upload in files:
        filename = upload.filename or "unnamed"
        extension = Path(filename).suffix.lower()
        
        # Check if file type is supported
        if extension not in SUPPORTED_EXTENSIONS:
            logger.warning(
                f"Unsupported file type: {filename} (extension: {extension})"
            )
            file_status.append({
                "filename": filename,
                "status": "skipped",
                "reason": f"Unsupported file type: {extension}",
            })
            continue
        
        try:
            # Read file content
            content = await upload.read()
            
            # Upload to S3 and create DynamoDB job
            logger.info(f"Uploading {filename} to S3 for workspace {workspace_id}")
            job_result = await upload_document_and_create_job(
                workspace_id=workspace_id,
                case_id=case_id,
                filename=filename,
                data=content,
                content_type=upload.content_type,
            )
            
            job_id = job_result["job_id"]
            s3_key = job_result["s3_key"]
            
            job_ids.append(job_id)
            s3_keys.append(s3_key)
            
            # Queue background processing task
            logger.info(
                f"Queueing background task: job_id={job_id}, s3_key={s3_key}"
            )
            background_tasks.add_task(
                process_uploaded_job,
                workspace_id=workspace_id,
                case_id=case_id,
                job_id=job_id,
                s3_key=s3_key,
            )
            
            file_status.append({
                "filename": filename,
                "status": "queued",
                "job_id": job_id,
                "size_bytes": len(content),
            })
            
        except Exception as exc:
            logger.error(f"Failed to upload {filename}: {exc}")
            file_status.append({
                "filename": filename,
                "status": "failed",
                "error": str(exc),
            })
    
    # Check if at least one file was queued
    if not job_ids:
        raise HTTPException(
            status_code=400,
            detail="No supported files could be uploaded. Check file types and try again.",
        )
    
    queued_count = len([s for s in file_status if s["status"] == "queued"])
    skipped_count = len([s for s in file_status if s["status"] == "skipped"])
    failed_count = len([s for s in file_status if s["status"] == "failed"])
    
    logger.info(
        f"Upload session complete: case={case_id}, queued={queued_count}, "
        f"skipped={skipped_count}, failed={failed_count}"
    )
    
    return {
        "success": True,
        "case_id": case_id,
        "workspace_id": workspace_id,
        "message": f"Uploaded {queued_count} file(s) for processing",
        "jobs": [{"job_id": jid, "s3_key": key} for jid, key in zip(job_ids, s3_keys)],
        "files": file_status,
        "next_steps": [
            "Files are now queued for extraction pipeline processing",
            "Check job status via /workspace-jobs endpoints",
            "Graph will be available in output directory after completion",
        ],
    }
