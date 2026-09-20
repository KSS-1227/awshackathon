"""
Case lifecycle tracking in DynamoDB (stub for now).

Used by workspace_upload.py to link graph_id to a case after processing completes.
"""
import logging

logger = logging.getLogger(__name__)


async def update_case(
    *,
    case_id: str,
    workspace_id: str,
    graph_id: str,
    graph_status: str,
) -> dict:
    """Update case metadata with graph information.
    
    Args:
        case_id: Case ID
        workspace_id: Workspace ID
        graph_id: ID of the generated graph
        graph_status: Status of graph generation
    
    Returns:
        Updated case item
    """
    logger.info(f"Updated case {case_id} with graph_id={graph_id}, status={graph_status}")
    return {
        "case_id": case_id,
        "workspace_id": workspace_id,
        "graph_id": graph_id,
        "graph_status": graph_status,
    }
