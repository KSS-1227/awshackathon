"""
Decision lineage persistence and retrieval — now DynamoDB-backed.

This module is the single point of contact for lineage queries. It delegates
to backend/storage/dynamodb_lineage.py which uses an in-memory store with
disk persistence for local development, ready to swap to DynamoDB in production.

Public API
----------
persist_lineage(workspace_id, case_id, rows)
    Batch-insert one row per conclusion produced by a reconciliation run.
    Wrapped in error-handling that logs but never raises — a lineage write
    failure must never break the caller's response.

fetch_lineage(workspace_id, case_id, decision_id=None)
    Return lineage rows for a workspace/case, optionally filtered to a
    single decision_id.

LineageRow
    Data class for a single lineage record.
"""
from __future__ import annotations

# Re-export the public API from dynamodb_lineage
from backend.storage.dynamodb_lineage import (
    LineageRow,
    persist_lineage,
    fetch_lineage,
)

__all__ = ["LineageRow", "persist_lineage", "fetch_lineage"]
