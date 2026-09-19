"""
Embeddings storage stub — replaces cockroach_vector_storage.

In local-only mode, embeddings are stored as node attributes in NetworkXStorage.
When a workspace_id is provided and vectors are computed, they are added to the
graph's node data and will be persisted when the graph is saved to GraphML.

This is a graceful degradation: embeddings are still available for re-retrieval
and RAG, they're just stored locally alongside the graph instead of in CockroachDB.

Requires: pip install numpy (already in requirements)
"""
import json
import os
from typing import Optional

import numpy as np

from .config import settings as parameter
from .storage.graph_storage import NetworkXStorage


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


async def nodes_missing_embeddings(
    workspace_id: str,
    graph_storage: NetworkXStorage,
) -> list[tuple[str, str]]:
    """
    Return (node_id, description) for every node in the graph
    that doesn't have an 'embedding' attribute yet.
    
    Called after extraction so only genuinely new entities get encoded.
    """
    missing = []
    for node_id, node_data in graph_storage._graph.nodes(data=True):
        if "embedding" not in node_data or node_data["embedding"] is None:
            # Use 'description' field or fall back to node_id
            description = node_data.get("description", node_id)
            missing.append((node_id, description))
    return missing


async def upsert_embedding(
    workspace_id: str,
    node_id: str,
    embedding,
    graph_storage: NetworkXStorage,
) -> None:
    """Store embedding as JSON string in node attribute."""
    embedding_json = _embedding_to_json(embedding)
    if graph_storage._graph.has_node(node_id):
        graph_storage._graph.nodes[node_id]["embedding"] = embedding_json
    else:
        # Node may not exist yet; create it with the embedding
        graph_storage._graph.add_node(node_id, embedding=embedding_json)


async def top_k_similar(
    workspace_id: str,
    query_embedding,
    graph_storage: NetworkXStorage,
    k: int = 5,
) -> list[tuple[str, float]]:
    """
    Return [(node_id, similarity)] ordered most-similar-first.
    Computes similarity in Python using stored node embeddings.
    """
    query_vec = np.array(query_embedding, dtype=np.float32)
    similarities = []

    for node_id, node_data in graph_storage._graph.nodes(data=True):
        embedding_json = node_data.get("embedding")
        if embedding_json is None:
            continue
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
