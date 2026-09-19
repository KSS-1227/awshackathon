"""
Conversation memory for workspace GraphRAG sessions.

STUBBED: CockroachDB backend removed. Multi-turn session history gracefully
degrades to single-turn behavior — history retrieval returns empty list,
turn saves return 0 (no persistence).

Call sites in workspace_document_service.py:
1. get_session_history() → returns [] → if history: evaluates False → single-turn
2. save_turn() → returns 0 → response includes "turn": 0 (harmless)

No errors thrown, no exceptions. Clean no-op degradation.
"""
from __future__ import annotations


async def get_session_history(workspace_id: str, session_id: str) -> list[dict[str, str | int]]:
    """Return prior turns for one workspace/session.
    
    STUBBED: Returns empty list. Multi-turn history unavailable (no CockroachDB).
    Caller (workspace_document_service.py line ~171) will detect empty history
    and fall back to single-turn query behavior.
    """
    # Return empty list — graceful degradation to single-turn queries
    return []


async def save_turn(
    workspace_id: str,
    session_id: str,
    question: str,
    answer: str,
) -> int:
    """Persist a turn and return its per-session sequence number.
    
    STUBBED: Returns 0 (turn not persisted). No CockroachDB backend.
    Caller (workspace_document_service.py line ~242) will receive 0 and
    include it in response as "turn": 0 (harmless indicator that session
    persistence is unavailable).
    """
    # Return 0 — indicate no turn was persisted
    return 0
