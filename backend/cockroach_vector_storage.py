"""
CockroachDB-backed vector storage ? replaces the local .npy file +
sklearn cosine_similarity previously used in retrieval/query.py.

Stores embeddings as JSONB arrays in CockroachDB (since CockroachDB
doesn't support native VECTOR type). Vector similarity search is
performed in Python using numpy cosine similarity.

Requires: pip install "psycopg[binary,pool]" numpy
Env var:  COCKROACH_DATABASE_URL
"""
import json
import os
from typing import Optional

import numpy as np
import psycopg
from psycopg_pool import AsyncConnectionPool

_DSN = os.environ.get("COCKROACH_DATABASE_URL")
_pool: AsyncConnectionPool | None = None


async def _get_pool() -> AsyncConnectionPool:
    global _pool
    if _pool is None:
        if not _DSN:
            raise RuntimeError(
                "COCKROACH_DATABASE_URL is not set. Get a free-tier connection "
                "string from the CockroachDB Cloud Console and set it as an "
                "env var before using cockroach_vector_storage."
            )
        _pool = AsyncConnectionPool(_DSN, min_size=1, max_size=10, open=False)
    if _pool.closed:
        await _pool.open()
    return _pool


def _embedding_to_json(embedding) -> str:
    """Convert embedding array to JSON string for storage."""
    return json.dumps([float(v) for v in embedding])


def _json_to_embedding(json_str: str) -> np.ndarray:
    """Convert JSON string back to numpy array."""
    return np.array(json.loads(json_str), dtype=np.float32)


def _cosine_similarity(vec1: np.ndarray, vec2: np.ndarray) -> float:
    """Calculate cosine similarity between two vectors (0..1 scale)."""
    norm1 = np.linalg.norm(vec1)
    norm2 = np.linalg.norm(vec2)
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return float(np.dot(vec1, vec2) / (norm1 * norm2))


async def nodes_missing_embeddings(workspace_id: str) -> list[tuple[str, str]]:
    """
    Return (node_id, description) for every node in this workspace's graph
    that doesn't have an embedding row yet. Called after extraction so only
    genuinely new entities get (re-)encoded ? mirrors the same
    "only touch what's new" pattern as the chunk-extraction tracking in
    builder.py's _step_text_extraction.
    """
    pool = await _get_pool()
    async with pool.connection() as conn:
        rows = await (await conn.execute(
            """
            SELECT n.node_id, n.description
            FROM graph_nodes n
            LEFT JOIN entity_embeddings e
              ON e.workspace_id = n.workspace_id AND e.node_id = n.node_id
            WHERE n.workspace_id = %s AND e.node_id IS NULL
            """,
            (workspace_id,),
        )).fetchall()
        return [(r[0], r[1] or r[0]) for r in rows]


async def upsert_embedding(workspace_id: str, node_id: str, embedding) -> None:
    """Store embedding as JSON array in CockroachDB."""
    pool = await _get_pool()
    embedding_json = _embedding_to_json(embedding)
    async with pool.connection() as conn:
        await conn.execute(
            """
            INSERT INTO entity_embeddings (workspace_id, node_id, embedding, updated_at)
            VALUES (%s, %s, %s, now())
            ON CONFLICT (workspace_id, node_id) DO UPDATE SET
                embedding  = excluded.embedding,
                updated_at = now()
            """,
            (workspace_id, node_id, embedding_json),
        )


async def top_k_similar(workspace_id: str, query_embedding, k: int = 5) -> list[tuple[str, float]]:
    """
    Return [(node_id, similarity)] ordered most-similar-first.
    Since CockroachDB doesn't have native vector search, we fetch all embeddings
    for the workspace and compute similarity in Python.
    
    For production with many embeddings, consider using pgvector in PostgreSQL
    or switching to a dedicated vector database like Pinecone/Weaviate.
    """
    pool = await _get_pool()
    query_vec = np.array(query_embedding, dtype=np.float32)
    
    async with pool.connection() as conn:
        rows = await (await conn.execute(
            """
            SELECT node_id, embedding
            FROM entity_embeddings
            WHERE workspace_id = %s
            """,
            (workspace_id,),
        )).fetchall()
    
    # Compute similarities in Python
    similarities = []
    for node_id, embedding_json in rows:
        try:
            stored_vec = _json_to_embedding(embedding_json)
            sim = _cosine_similarity(query_vec, stored_vec)
            similarities.append((node_id, sim))
        except Exception:
            # Skip malformed embeddings
            continue
    
    # Sort by similarity descending and return top k
    similarities.sort(key=lambda x: x[1], reverse=True)
    return similarities[:k]
