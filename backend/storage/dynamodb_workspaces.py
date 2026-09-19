"""DynamoDB workspace operations for the metadata migration.

This module implements all workspace CRUD operations using DynamoDB:
- create_workspace: Create a new workspace with atomic forward/reverse-index items
- list_workspaces_for_user: List workspaces a user is a member of using reverse-index
- get_workspace: Fetch a single workspace metadata with membership validation
- update_workspace: Update workspace metadata (Admin only)
- soft_delete_workspace: Soft delete a workspace (Admin only)

All operations maintain forward (WORKSPACE#) and reverse (USER#) index consistency
using transact_write_items for atomic multi-item updates.

Audit logging is integrated into all operations using a best-effort pattern:
audit log failures never propagate to the caller.
"""

import asyncio
import logging
import time
from typing import Optional
from datetime import datetime, timezone
from fastapi import HTTPException

from .dynamodb_client import get_dynamodb_client
from .dynamodb_schema import WorkspaceMetaValidator, UserWorkspaceValidator
from .dynamodb_audit_log import insert_audit_log

logger = logging.getLogger(__name__)


async def create_workspace(
    workspace_id: str,
    owner_id: str,
    name: str,
    s3_bucket: str
) -> dict:
    """Create a new workspace in DynamoDB with atomic transaction.
    
    Creates a new workspace with the given metadata and atomically creates a
    reverse-index item for the owner to enable O(n) workspace listing.
    
    **Atomic Operation**: Both items (WORKSPACE#{id}+META and USER#{owner_id}+WORKSPACE#{id})
    are created together using transact_write_items. If either fails, both rollback.
    
    **Implements Requirements 1, 18**
    
    Args:
        workspace_id: Unique workspace identifier (UUID format)
        owner_id: User ID of the workspace owner
        name: Workspace display name (3-80 characters)
        s3_bucket: S3 bucket name for document storage
        
    Returns:
        Dict with created workspace metadata:
        - id: workspace_id
        - name: workspace name
        - owner_id: owner user ID
        - s3_bucket: S3 bucket name
        - status: "active"
        - created_at: Unix timestamp
        - updated_at: Unix timestamp
        
    Raises:
        HTTPException: 400 Bad Request if name length invalid (< 3 or > 80 chars)
        HTTPException: 409 Conflict if workspace with duplicate name already exists for owner
        HTTPException: 503 Service Unavailable on DynamoDB transaction failure
        
    Examples:
        >>> workspace = await create_workspace(
        ...     workspace_id="ws-001",
        ...     owner_id="user-123",
        ...     name="Project Alpha",
        ...     s3_bucket="my-compliance-bucket"
        ... )
        >>> workspace["id"]
        "ws-001"
        >>> workspace["status"]
        "active"
    """
    db = get_dynamodb_client()
    
    try:
        logger.debug(f"Creating workspace: workspace_id={workspace_id}, owner_id={owner_id}, name={name}")
        
        # Step 1: Validate name length (3-80 characters)
        if len(name) < 3:
            logger.warning(f"Workspace name too short: length={len(name)}, must be >= 3")
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "name_too_short",
                    "message": f"Workspace name must be at least 3 characters, got {len(name)}"
                }
            )
        
        if len(name) > 80:
            logger.warning(f"Workspace name too long: length={len(name)}, must be <= 80")
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "name_too_long",
                    "message": f"Workspace name must be at most 80 characters, got {len(name)}"
                }
            )
        
        # Step 2: Check for duplicate workspace name for this owner
        # Query USER#{owner_id}+WORKSPACE#* to check for existing workspaces with same name
        logger.debug(f"Checking for duplicate workspace names for owner_id={owner_id}")
        
        existing_workspaces = await db.query(
            pk=f"USER#{owner_id}",
            sk_prefix="WORKSPACE#",
            limit=100
        )
        
        # For each workspace, fetch metadata and check name
        for existing_ws_item in existing_workspaces:
            try:
                workspace_id_extracted = existing_ws_item.get("SK", "").replace("WORKSPACE#", "")
                
                existing_meta = await db.get_item(
                    pk=f"WORKSPACE#{workspace_id_extracted}",
                    sk="META"
                )
                
                if existing_meta:
                    existing_name = existing_meta.get("name", "")
                    if existing_name.lower() == name.lower():
                        logger.warning(
                            f"Duplicate workspace name for owner: "
                            f"owner_id={owner_id}, name={name}, existing_id={workspace_id_extracted}"
                        )
                        raise HTTPException(
                            status_code=409,
                            detail={
                                "error": "workspace_name_exists",
                                "message": f"A workspace with name '{name}' already exists for this owner"
                            }
                        )
            except HTTPException:
                raise
            except Exception as inner_exc:
                logger.warning(f"Error checking for duplicate name: {inner_exc}, continuing")
        
        # Step 3: Create timestamp (Unix epoch in seconds)
        current_time = int(time.time())
        
        # Step 4: Create workspace metadata item (WORKSPACE#{id}+META)
        workspace_meta_item = {
            "PK": f"WORKSPACE#{workspace_id}",
            "SK": "META",
            "name": name,
            "owner_id": owner_id,
            "s3_bucket": s3_bucket,
            "status": "active",
            "created_at": current_time,
            "updated_at": current_time,
        }
        
        # Validate the workspace meta item before creating
        try:
            WorkspaceMetaValidator.validate(workspace_meta_item)
        except ValueError as val_exc:
            logger.error(f"Workspace meta validation failed: {val_exc}")
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "validation_error",
                    "message": str(val_exc)
                }
            ) from val_exc
        
        # Step 5: Create reverse-index item (USER#{owner_id}+WORKSPACE#{id})
        # This enables O(n) listing of user's workspaces
        user_workspace_item = {
            "PK": f"USER#{owner_id}",
            "SK": f"WORKSPACE#{workspace_id}",
            "role": "Admin",  # Owner is automatically Admin
            "membership_status": "active",
            "joined_at": current_time,
        }
        
        # Validate the user workspace item before creating
        try:
            UserWorkspaceValidator.validate(user_workspace_item)
        except ValueError as val_exc:
            logger.error(f"User workspace item validation failed: {val_exc}")
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "validation_error",
                    "message": str(val_exc)
                }
            ) from val_exc
        
        # Step 6: Create atomic transaction with both items
        logger.debug(f"Creating atomic transaction for workspace_id={workspace_id}")
        
        transact_items = [
            {"Put": {"Item": workspace_meta_item}},
            {"Put": {"Item": user_workspace_item}},
        ]
        
        # Execute the transaction
        try:
            await db.transact_write(transact_items)
        except Exception as transact_exc:
            logger.error(
                f"Transaction failed for workspace_id={workspace_id}: {transact_exc}",
                exc_info=True
            )
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "transaction_failed",
                    "message": "Failed to create workspace due to service error"
                }
            ) from transact_exc
        
        # Step 7: Build response
        response = {
            "id": workspace_id,
            "name": name,
            "owner_id": owner_id,
            "s3_bucket": s3_bucket,
            "status": "active",
            "created_at": current_time,
            "updated_at": current_time,
        }
        
        logger.info(f"✓ Created workspace: workspace_id={workspace_id}, owner_id={owner_id}, name={name}")
        
        # Step 8: Audit log (best-effort — must not block workspace creation)
        try:
            await insert_audit_log(
                workspace_id=workspace_id,
                event_type="workspace_created",
                actor_id=owner_id,
                action="create",
                detail={"name": name, "owner_id": owner_id}
            )
        except Exception as audit_exc:
            logger.warning(f"Failed to insert audit log for workspace creation: {audit_exc}")
        
        return response
        
    except HTTPException:
        # Re-raise HTTPExceptions as-is
        raise
    except Exception as exc:
        logger.error(f"Error creating workspace: {exc}", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail={
                "error": "service_unavailable",
                "message": "Failed to create workspace"
            }
        ) from exc


async def list_workspaces_for_user(user_id: str) -> list[dict]:
    """List all active workspaces the user is a member of.
    
    This function queries the reverse-index (USER#{user_id}+WORKSPACE#*) to find
    all workspaces the user is a member of in O(n_workspaces) time, avoiding a full table scan.
    
    For each workspace found, fetches the full WORKSPACE#{id}+META item to get complete metadata
    including name, owner_id, creation date, and member count.
    
    **Implements Requirements 2, 15**
    
    Args:
        user_id: The UUID of the user requesting their workspace list
        
    Returns:
        List of workspace dicts with fields:
        - id: workspace ID
        - name: workspace name
        - owner_id: UUID of workspace owner
        - created_at: Unix timestamp of workspace creation
        - status: workspace status ("active", "archived", "deleted")
        - member_count: number of active members
        - user_role: the requesting user's role in this workspace
        
    Raises:
        HTTPException: 503 on DynamoDB errors
        
    Examples:
        >>> workspaces = await list_workspaces_for_user("user-123")
        >>> len(workspaces)
        2
        >>> workspaces[0]["name"]
        "Project Alpha"
        >>> workspaces[0]["user_role"]
        "Admin"
    """
    db = get_dynamodb_client()
    
    try:
        # Step 1: Query reverse-index USER#{user_id}+WORKSPACE#* to find workspace memberships
        logger.debug(f"Querying reverse-index for user_id={user_id}")
        
        membership_items = await db.query(
            pk=f"USER#{user_id}",
            sk_prefix="WORKSPACE#",
            limit=100
        )
        
        if not membership_items:
            logger.debug(f"User {user_id} has no workspace memberships")
            return []
        
        logger.debug(f"Found {len(membership_items)} workspace memberships for user_id={user_id}")
        
        # Step 2: For each workspace membership, fetch full WORKSPACE#{id}+META metadata
        workspace_list = []
        
        for membership_item in membership_items:
            try:
                # Parse workspace_id from the SK (format: "WORKSPACE#{workspace_id}")
                sk = membership_item.get("SK", "")
                if not sk.startswith("WORKSPACE#"):
                    logger.warning(f"Invalid reverse-index SK format: {sk}, skipping")
                    continue
                
                workspace_id = sk.replace("WORKSPACE#", "")
                user_role = membership_item.get("role")
                membership_status = membership_item.get("membership_status", "active")
                
                # Skip if not active member
                if membership_status != "active":
                    logger.debug(f"Skipping inactive membership: workspace_id={workspace_id}, status={membership_status}")
                    continue
                
                # Fetch full workspace metadata
                logger.debug(f"Fetching workspace metadata: workspace_id={workspace_id}")
                
                workspace_meta = await db.get_item(
                    pk=f"WORKSPACE#{workspace_id}",
                    sk="META"
                )
                
                if not workspace_meta:
                    logger.warning(f"Workspace metadata not found: workspace_id={workspace_id}, skipping")
                    continue
                
                # Check if workspace is deleted
                is_deleted = workspace_meta.get("is_deleted", False)
                if is_deleted:
                    logger.debug(f"Skipping deleted workspace: workspace_id={workspace_id}")
                    continue
                
                # Fetch member count for this workspace
                # Query WORKSPACE#{id}+MEMBER#* items and count active members
                member_items = await db.query(
                    pk=f"WORKSPACE#{workspace_id}",
                    sk_prefix="MEMBER#",
                    limit=100
                )
                
                # Count active members only
                member_count = sum(
                    1 for m in member_items
                    if m.get("membership_status", "active") == "active"
                )
                
                # Build workspace response dict
                workspace_response = {
                    "id": workspace_id,
                    "name": workspace_meta.get("name"),
                    "owner_id": workspace_meta.get("owner_id"),
                    "created_at": workspace_meta.get("created_at"),
                    "status": workspace_meta.get("status", "active"),
                    "member_count": member_count,
                    "user_role": user_role,
                }
                
                workspace_list.append(workspace_response)
                logger.debug(f"✓ Fetched workspace: {workspace_response['id']}")
                
            except Exception as inner_exc:
                logger.warning(f"Error processing workspace from membership item: {inner_exc}, skipping")
                continue
        
        # Step 3: Sort by created_at DESC (newest first)
        workspace_list.sort(
            key=lambda w: w.get("created_at", 0),
            reverse=True
        )
        
        logger.debug(f"✓ list_workspaces_for_user completed: user_id={user_id}, count={len(workspace_list)}")
        
        return workspace_list
        
    except HTTPException:
        # Re-raise HTTPExceptions as-is
        raise
    except Exception as exc:
        logger.error(f"Error listing workspaces for user_id={user_id}: {exc}", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail={"error": "service_unavailable", "message": "Failed to list workspaces"}
        ) from exc


async def get_member_role(workspace_id: str, user_id: str) -> Optional[str]:
    """Get the role of a user in a workspace if they are an active member.
    
    Queries WORKSPACE#{workspace_id}+MEMBER#{user_id} to fetch the user's role.
    Returns None if the user is not an active member or member record doesn't exist.
    
    **Implements Requirement 8, 5.5**
    
    Args:
        workspace_id: The workspace ID
        user_id: The user ID
        
    Returns:
        The user's role ("Admin", "Analyst", "Viewer") if active member, None otherwise
        
    Raises:
        HTTPException: 503 on DynamoDB errors
    """
    db = get_dynamodb_client()
    
    try:
        logger.debug(f"Fetching member role: workspace_id={workspace_id}, user_id={user_id}")
        
        member_item = await db.get_item(
            pk=f"WORKSPACE#{workspace_id}",
            sk=f"MEMBER#{user_id}"
        )
        
        if not member_item:
            logger.debug(f"Member not found: workspace_id={workspace_id}, user_id={user_id}")
            return None
        
        # Check if membership is active
        membership_status = member_item.get("membership_status", "active")
        if membership_status != "active":
            logger.debug(f"Member not active: workspace_id={workspace_id}, user_id={user_id}, status={membership_status}")
            return None
        
        role = member_item.get("role")
        logger.debug(f"✓ Got member role: workspace_id={workspace_id}, user_id={user_id}, role={role}")
        return role
        
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Error getting member role: workspace_id={workspace_id}, user_id={user_id}: {exc}", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail={"error": "service_unavailable", "message": "Failed to get member role"}
        ) from exc


async def update_workspace(
    workspace_id: str,
    user_id: str,
    updates: dict
) -> dict:
    """Update workspace metadata (Admin only).
    
    Verifies the requesting user is an Admin member, validates the update fields,
    then updates the WORKSPACE#{workspace_id}+META item with allowed fields.
    
    **Allowed update fields**: name, status, s3_bucket
    
    **Status validation**: If status is provided, must be one of: "active", "archived", "deleted"
    
    Automatically records the updated_at timestamp for all updates.
    
    **Implements Requirements 3, 15**
    
    Args:
        workspace_id: The workspace ID to update
        user_id: The user requesting the update
        updates: Dict with fields to update (name, status, s3_bucket)
        
    Returns:
        Updated workspace metadata dict with all fields
        
    Raises:
        HTTPException: 403 Forbidden if user is not Admin
        HTTPException: 404 Not Found if workspace doesn't exist
        HTTPException: 400 Bad Request if status value is invalid
        HTTPException: 503 Service Unavailable on DynamoDB errors
        
    Examples:
        >>> result = await update_workspace(
        ...     workspace_id="ws-123",
        ...     user_id="user-456",
        ...     updates={"name": "New Name", "status": "archived"}
        ... )
        >>> result["name"]
        "New Name"
        >>> result["status"]
        "archived"
        >>> result["updated_at"]  # Unix timestamp
        1234567890
    """
    db = get_dynamodb_client()
    
    try:
        logger.debug(f"Updating workspace: workspace_id={workspace_id}, user_id={user_id}, updates={updates}")
        
        # Step 1: Verify workspace exists
        workspace_meta = await db.get_item(
            pk=f"WORKSPACE#{workspace_id}",
            sk="META"
        )
        
        if not workspace_meta:
            logger.warning(f"Workspace not found: workspace_id={workspace_id}")
            raise HTTPException(
                status_code=404,
                detail={"error": "workspace_not_found", "message": "The workspace does not exist"}
            )
        
        # Step 2: Verify user is Admin
        user_role = await get_member_role(workspace_id, user_id)
        
        if user_role != "Admin":
            logger.warning(f"Permission denied: workspace_id={workspace_id}, user_id={user_id}, role={user_role}")
            raise HTTPException(
                status_code=403,
                detail={"error": "permission_denied", "message": "Only Admin members can update workspace metadata"}
            )
        
        # Step 3: Validate update fields
        allowed_fields = {"name", "status", "s3_bucket"}
        invalid_fields = set(updates.keys()) - allowed_fields
        
        if invalid_fields:
            logger.warning(f"Invalid update fields: {invalid_fields}")
            raise HTTPException(
                status_code=400,
                detail={"error": "invalid_fields", "message": f"Cannot update fields: {', '.join(invalid_fields)}"}
            )
        
        # Step 4: Validate status if provided
        if "status" in updates:
            valid_statuses = {"active", "archived", "deleted"}
            if updates["status"] not in valid_statuses:
                logger.warning(f"Invalid status value: {updates['status']}")
                raise HTTPException(
                    status_code=400,
                    detail={"error": "invalid_status", "message": f"Status must be one of: {', '.join(valid_statuses)}"}
                )
        
        # Step 5: Build update expression and attribute values
        update_parts = []
        attr_values = {}
        
        # Add updated_at timestamp to all updates
        current_time = int(time.time())
        update_parts.append("#updated_at = :updated_at")
        attr_values[":updated_at"] = current_time
        
        # Add each requested update field
        if "name" in updates:
            update_parts.append("#name = :name")
            attr_values[":name"] = updates["name"]
        
        if "status" in updates:
            update_parts.append("#status = :status")
            attr_values[":status"] = updates["status"]
        
        if "s3_bucket" in updates:
            update_parts.append("s3_bucket_name = :s3_bucket")
            attr_values[":s3_bucket"] = updates["s3_bucket"]
        
        update_expr = "SET " + ", ".join(update_parts)
        
        # Handle reserved words
        attr_names = {
            "#name": "name",
            "#status": "status",
            "#updated_at": "updated_at"
        }
        
        # Step 6: Perform the update
        logger.debug(f"Executing update: workspace_id={workspace_id}, expr={update_expr}, values={attr_values}")
        
        updated_item = await db.update_item(
            pk=f"WORKSPACE#{workspace_id}",
            sk="META",
            update_expr=update_expr,
            attr_values=attr_values,
            attr_names=attr_names
        )
        
        logger.info(f"✓ Updated workspace: workspace_id={workspace_id}, by user_id={user_id}")
        
        # Step 7: Audit log (best-effort — must not block workspace update)
        try:
            await insert_audit_log(
                workspace_id=workspace_id,
                event_type="workspace_updated",
                actor_id=user_id,
                action="update",
                detail=updates
            )
        except Exception as audit_exc:
            logger.warning(f"Failed to insert audit log for workspace update: {audit_exc}")
        
        return updated_item
        
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Error updating workspace: workspace_id={workspace_id}, user_id={user_id}: {exc}", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail={"error": "service_unavailable", "message": "Failed to update workspace"}
        ) from exc


async def get_workspace(workspace_id: str, user_id: str) -> dict:
    """Retrieve workspace metadata after verifying membership and deletion status.
    
    Fetches the WORKSPACE#{workspace_id}+META item and verifies:
    1. Workspace exists (404 if not found)
    2. Workspace is not deleted (404 if is_deleted=true)
    3. User is an active member (403 if not a member or membership inactive)
    
    **Implements Requirements 2, 15**
    
    Args:
        workspace_id: Workspace ID to retrieve
        user_id: User ID requesting the workspace (for membership verification)
    
    Returns:
        dict containing full workspace metadata with fields:
        - workspace_id, owner_id, name, s3_bucket, status
        - created_at, updated_at, is_deleted, member_count
    
    Raises:
        HTTPException: 404 Workspace not found or is_deleted=true
        HTTPException: 403 User is not an active member
        HTTPException: 500 DynamoDB operation failed
    
    Examples:
        >>> workspace = await get_workspace("ws-123", "user-456")
        >>> print(workspace['name'])
        'Q3 Compliance Review'
        >>> print(workspace['member_count'])
        5
    """
    db = get_dynamodb_client()
    pk = f"WORKSPACE#{workspace_id}"
    sk = "META"
    
    logger.debug(f"Fetching workspace: workspace_id={workspace_id}, user_id={user_id}")
    
    try:
        # Step 1: Query WORKSPACE#{workspace_id}+META item
        workspace_item = await db.get_item(pk, sk)
        
        # Step 2: Check if workspace exists
        if workspace_item is None:
            logger.debug(f"Workspace not found: workspace_id={workspace_id}")
            raise HTTPException(
                status_code=404,
                detail={"error": "workspace_not_found", "message": "The workspace does not exist"}
            )
        
        # Step 3: Check if workspace is deleted (soft delete flag)
        is_deleted = workspace_item.get("is_deleted", False)
        if is_deleted:
            logger.debug(f"Workspace is deleted: workspace_id={workspace_id}")
            raise HTTPException(
                status_code=404,
                detail={"error": "workspace_not_found", "message": "The workspace does not exist"}
            )
        
        # Step 4: Verify user is an active member
        member_pk = f"WORKSPACE#{workspace_id}"
        member_sk = f"MEMBER#{user_id}"
        
        member_item = await db.get_item(member_pk, member_sk)
        
        # Check if member exists and has active membership status
        if member_item is None:
            logger.debug(f"User is not a member: workspace_id={workspace_id}, user_id={user_id}")
            raise HTTPException(
                status_code=403,
                detail={"error": "permission_denied", "message": "User is not a member of this workspace"}
            )
        
        membership_status = member_item.get("membership_status", "pending")
        if membership_status != "active":
            logger.debug(
                f"User membership is not active: workspace_id={workspace_id}, "
                f"user_id={user_id}, status={membership_status}"
            )
            raise HTTPException(
                status_code=403,
                detail={"error": "permission_denied", "message": "User membership is not active"}
            )
        
        # Step 5: Fetch member count for response
        try:
            # Query all members with MEMBER# prefix to get count
            members = await db.query(member_pk, sk_prefix="MEMBER#")
            # Count only active members
            active_member_count = sum(
                1 for m in members 
                if m.get("membership_status") == "active"
            )
        except Exception as exc:
            logger.warning(f"Failed to fetch member count, defaulting to 0: {exc}")
            active_member_count = 0
        
        # Step 6: Build response with all workspace metadata
        response = {
            "workspace_id": workspace_id,
            "owner_id": workspace_item.get("owner_id", ""),
            "name": workspace_item.get("name", ""),
            "s3_bucket": workspace_item.get("s3_bucket", ""),
            "status": workspace_item.get("status", "active"),
            "created_at": workspace_item.get("created_at", ""),
            "updated_at": workspace_item.get("updated_at", ""),
            "is_deleted": is_deleted,
            "member_count": active_member_count,
        }
        
        logger.info(
            f"✓ Retrieved workspace: workspace_id={workspace_id}, "
            f"name={response['name']}, members={active_member_count}"
        )
        
        return response
        
    except HTTPException:
        # Re-raise HTTPException as-is
        raise
    except Exception as exc:
        logger.error(f"Error getting workspace: workspace_id={workspace_id}, user_id={user_id}: {exc}", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail={"error": "service_unavailable", "message": "Failed to retrieve workspace"}
        ) from exc


async def soft_delete_workspace(workspace_id: str, user_id: str) -> dict:
    """Soft delete a workspace (Admin only).
    
    Sets is_deleted=true and deleted_at timestamp on WORKSPACE#{workspace_id}+META.
    Does NOT remove items; subsequent queries filter out deleted workspace.
    
    Verifies the requesting user is an Admin member before allowing deletion.
    
    **Implements Requirements 4, 15**
    
    Args:
        workspace_id: Workspace ID to delete
        user_id: User ID requesting the delete (for RBAC check)
    
    Returns:
        dict with deletion confirmation containing:
        - workspace_id: the deleted workspace ID
        - is_deleted: true (confirmation flag)
        - deleted_at: Unix timestamp when deleted
    
    Raises:
        HTTPException: 403 Forbidden if user is not Admin
        HTTPException: 404 Not Found if workspace doesn't exist
        HTTPException: 503 Service Unavailable on DynamoDB errors
    
    Examples:
        >>> result = await soft_delete_workspace("ws-123", "user-456")
        >>> result["is_deleted"]
        True
        >>> result["deleted_at"]  # Unix timestamp
        1234567890
    """
    db = get_dynamodb_client()
    
    try:
        logger.debug(f"Soft deleting workspace: workspace_id={workspace_id}, user_id={user_id}")
        
        # Step 1: Verify workspace exists
        workspace_meta = await db.get_item(
            pk=f"WORKSPACE#{workspace_id}",
            sk="META"
        )
        
        if not workspace_meta:
            logger.warning(f"Workspace not found: workspace_id={workspace_id}")
            raise HTTPException(
                status_code=404,
                detail={"error": "workspace_not_found", "message": "The workspace does not exist"}
            )
        
        # Step 2: Verify user is Admin
        user_role = await get_member_role(workspace_id, user_id)
        
        if user_role != "Admin":
            logger.warning(f"Permission denied: workspace_id={workspace_id}, user_id={user_id}, role={user_role}")
            raise HTTPException(
                status_code=403,
                detail={"error": "permission_denied", "message": "Only Admin members can delete workspace"}
            )
        
        # Step 3: Perform soft delete
        current_time = int(time.time())
        
        updated_item = await db.update_item(
            pk=f"WORKSPACE#{workspace_id}",
            sk="META",
            update_expr="SET #is_deleted = :is_deleted, #deleted_at = :deleted_at, #updated_at = :updated_at",
            attr_values={
                ":is_deleted": True,
                ":deleted_at": current_time,
                ":updated_at": current_time,
            },
            attr_names={
                "#is_deleted": "is_deleted",
                "#deleted_at": "deleted_at",
                "#updated_at": "updated_at",
            }
        )
        
        logger.info(f"✓ Soft deleted workspace: workspace_id={workspace_id}, by user_id={user_id}")
        
        # Step 4: Audit log (best-effort — must not block workspace deletion)
        try:
            await insert_audit_log(
                workspace_id=workspace_id,
                event_type="workspace_deleted",
                actor_id=user_id,
                action="delete",
                detail={"workspace_id": workspace_id, "deleted_by": user_id}
            )
        except Exception as audit_exc:
            logger.warning(f"Failed to insert audit log for workspace deletion: {audit_exc}")
        
        # Step 5: Return deletion confirmation
        return {
            "workspace_id": workspace_id,
            "is_deleted": True,
            "deleted_at": current_time,
        }
        
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Error deleting workspace: workspace_id={workspace_id}, user_id={user_id}: {exc}", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail={"error": "service_unavailable", "message": "Failed to delete workspace"}
        ) from exc
