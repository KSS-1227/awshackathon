"""DynamoDB audit logging for workspace, case, and member operations.

This module provides audit trail functionality for the metadata migration.
For Phase 3 (Task 6.2), this will be extended to persist audit logs to DynamoDB.

Currently implements best-effort mock logging that doesn't block operations.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, Any

logger = logging.getLogger(__name__)


async def insert_audit_log(
    workspace_id: str,
    event_type: str,
    actor_id: str,
    action: str,
    detail: dict
) -> None:
    """Insert an audit log entry into DynamoDB.
    
    This is a best-effort operation: errors are logged but never propagate
    to the caller. This ensures audit logging failures never block critical
    workspace operations.
    
    **Implements Requirement 13 (phase 3 partial implementation)**
    
    Args:
        workspace_id: Workspace ID where the operation occurred
        event_type: Type of event (e.g., "workspace_created", "member_added")
        actor_id: User ID performing the operation
        action: Action verb (e.g., "create", "delete", "update")
        detail: Dict containing operation context (max 2000 chars when serialized)
        
    Returns:
        None (always succeeds or silently fails)
        
    Examples:
        >>> await insert_audit_log(
        ...     workspace_id="ws-123",
        ...     event_type="workspace_created",
        ...     actor_id="user-456",
        ...     action="create",
        ...     detail={"name": "My Workspace", "owner_id": "user-456"}
        ... )
        # Logs entry, no exceptions raised
    """
    try:
        # Generate timestamp
        now = datetime.now(timezone.utc)
        timestamp = now.isoformat()
        
        # Phase 2: Mock implementation - just log to application log
        # Phase 4 (Task 6.2): Implement actual DynamoDB write
        # Create WORKSPACE#{workspace_id}+AUDIT#{timestamp} item with immutable pattern
        
        logger.info(
            f"Audit log: event_type={event_type}, actor_id={actor_id}, "
            f"workspace_id={workspace_id}, action={action}, detail={detail}"
        )
        
        # TODO: Phase 3, Task 6.2 - Implement actual DynamoDB write:
        # db = get_dynamodb_client()
        # audit_item = {
        #     "PK": f"WORKSPACE#{workspace_id}",
        #     "SK": f"AUDIT#{timestamp}",
        #     "event_type": event_type,
        #     "actor_id": actor_id,
        #     "action": action,
        #     "detail": json.dumps(detail)[:2000],  # max 2000 chars
        #     "timestamp": timestamp,
        # }
        # await db.put_item(audit_item)
        
    except Exception as e:
        # Best-effort: log the error but never raise
        # This ensures audit failures never block the main operation
        logger.warning(f"Failed to insert audit log: {e}")


# For testing purposes: track audit logs inserted during test
_test_audit_logs: list[dict] = []


async def _mock_insert_audit_log_for_testing(
    workspace_id: str,
    event_type: str,
    actor_id: str,
    action: str,
    detail: dict
) -> None:
    """Mock implementation for testing that tracks audit logs in memory."""
    try:
        now = datetime.now(timezone.utc)
        timestamp = now.isoformat()
        
        audit_entry = {
            "workspace_id": workspace_id,
            "event_type": event_type,
            "actor_id": actor_id,
            "action": action,
            "detail": detail,
            "timestamp": timestamp,
        }
        
        _test_audit_logs.append(audit_entry)
        logger.info(f"Mock audit log inserted: {audit_entry}")
        
    except Exception as e:
        logger.warning(f"Failed to insert mock audit log: {e}")


def _get_test_audit_logs() -> list[dict]:
    """Retrieve all audit logs inserted during testing."""
    return _test_audit_logs.copy()


def _clear_test_audit_logs() -> None:
    """Clear all test audit logs (call between tests)."""
    global _test_audit_logs
    _test_audit_logs = []
