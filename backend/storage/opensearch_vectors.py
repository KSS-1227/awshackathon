"""
OpenSearch k-NN vector index for similarity search — additive to NetworkX storage.

When enabled via VECTOR_SEARCH_BACKEND=opensearch env var, embeddings are
indexed in Amazon OpenSearch for fast k-NN retrieval, while still being stored in
NetworkX node attributes for portability.

OpenSearch Domain Configuration:
- Endpoint: https://search-personal-project-vectors-ob6mlmv336fe3bptbg3o5idyf4.ap-south-1.es.amazonaws.com
- Region: ap-south-1
- Auth: IAM-based (SigV4)
- Index: k-NN enabled, 1024 dimensions (Titan Text Embeddings V2)
- Engine: faiss (fast cosine similarity)
"""
import json
import logging
from typing import Optional

import boto3
import numpy as np
import requests
from requests_aws4auth import AWS4Auth

logger = logging.getLogger(__name__)

# OpenSearch configuration
_OPENSEARCH_ENDPOINT = "https://search-personal-project-vectors-ob6mlmv336fe3bptbg3o5idyf4.ap-south-1.es.amazonaws.com"
_OPENSEARCH_REGION = "ap-south-1"
_OPENSEARCH_INDEX_NAME = "compliance-platform-vectors"
_OPENSEARCH_VECTOR_DIM = 1024

_OPENSEARCH_SESSION: Optional[requests.Session] = None


def get_opensearch_session() -> requests.Session:
    """
    Return singleton requests Session with AWS SigV4 auth for OpenSearch.
    
    Credentials from default AWS chain: env vars → IAM instance profile → ~/.aws/credentials.
    """
    global _OPENSEARCH_SESSION
    
    if _OPENSEARCH_SESSION is not None:
        return _OPENSEARCH_SESSION
    
    session = boto3.Session(region_name=_OPENSEARCH_REGION)
    credentials = session.get_credentials()
    
    if not credentials:
        raise RuntimeError(
            "Failed to obtain AWS credentials. Ensure AWS_ACCESS_KEY_ID/"
            "AWS_SECRET_ACCESS_KEY are set, or IAM credentials are available."
        )
    
    auth = AWS4Auth(
        credentials.access_key,
        credentials.secret_key,
        _OPENSEARCH_REGION,
        "es",
        session_token=credentials.token,
    )
    
    _OPENSEARCH_SESSION = requests.Session()
    _OPENSEARCH_SESSION.auth = auth
    _OPENSEARCH_SESSION.headers.update({"Content-Type": "application/json"})
    
    logger.info(
        "✓ OpenSearch session initialized (region=%s, index=%s)",
        _OPENSEARCH_REGION,
        _OPENSEARCH_INDEX_NAME,
    )
    
    return _OPENSEARCH_SESSION


async def create_index() -> bool:
    """
    Create k-NN index if it doesn't exist.
    
    Index config:
    - Engine: faiss
    - Vector dimension: 1024
    - Space type: cosine
    """
    try:
        session = get_opensearch_session()
        
        # Check if index exists
        url = f"{_OPENSEARCH_ENDPOINT}/{_OPENSEARCH_INDEX_NAME}"
        resp = session.head(url, verify=True)
        
        if resp.status_code == 200:
            # Index exists — delete and recreate to apply new schema
            logger.info("Index exists, recreating with new schema...")
            resp = session.delete(url, verify=True)
            if resp.status_code != 200:
                logger.warning("Failed to delete existing index: %s", resp.text)
        
        # Create index with correct schema
        index_body = {
            "settings": {
                "index": {
                    "knn": True,
                }
            },
            "mappings": {
                "properties": {
                    "workspace_id": {"type": "keyword"},
                    "node_id": {"type": "keyword"},
                    "embedding": {
                        "type": "knn_vector",
                        "dimension": _OPENSEARCH_VECTOR_DIM,
                    },
                    "metadata": {"type": "object"},
                }
            },
        }
        
        resp = session.put(
            url,
            json=index_body,
            verify=True,
        )
        
        if resp.status_code in (200, 201):
            logger.info("✓ OpenSearch index '%s' created successfully", _OPENSEARCH_INDEX_NAME)
            logger.debug("Index creation response: %s", resp.json())
            return True
        else:
            logger.error("✗ Failed to create index: %s %s", resp.status_code, resp.text)
            return False
        
    except Exception as e:
        logger.error("✗ Failed to create OpenSearch index: %s", e)
        return False


async def upsert_vector(
    workspace_id: str,
    node_id: str,
    embedding: np.ndarray,
    metadata: Optional[dict] = None,
) -> bool:
    """
    Index one embedding in OpenSearch.
    
    Parameters:
        workspace_id: Workspace scope
        node_id: Unique node identifier
        embedding: 1024-dim float32 numpy array
        metadata: Optional metadata dict
    
    Returns:
        True on success, False on error.
    """
    if embedding.shape[0] != _OPENSEARCH_VECTOR_DIM:
        logger.error(
            "✗ Invalid embedding dimension: expected %d, got %d",
            _OPENSEARCH_VECTOR_DIM,
            embedding.shape[0],
        )
        return False
    
    try:
        session = get_opensearch_session()
        
        doc = {
            "workspace_id": workspace_id,
            "node_id": node_id,
            "embedding": embedding.tolist(),
            "metadata": metadata or {},
        }
        
        # Use workspace_id#node_id as doc ID for uniqueness
        url = f"{_OPENSEARCH_ENDPOINT}/{_OPENSEARCH_INDEX_NAME}/_doc/{workspace_id}%23{node_id}"
        
        resp = session.put(
            url,
            json=doc,
            verify=True,
        )
        
        if resp.status_code in (200, 201):
            logger.debug(
                "✓ Indexed vector: workspace=%s, node=%s",
                workspace_id,
                node_id,
            )
            return True
        else:
            logger.error(
                "✗ Failed to index vector: %s %s",
                resp.status_code,
                resp.text,
            )
            return False
        
    except Exception as e:
        logger.error(
            "✗ Failed to index vector: workspace=%s, node=%s, error=%s",
            workspace_id,
            node_id,
            e,
        )
        return False


async def search_similar(
    workspace_id: str,
    query_embedding: np.ndarray,
    k: int = 10,
) -> list[tuple[str, float]]:
    """
    Search for k-nearest neighbors in OpenSearch.
    
    Parameters:
        workspace_id: Workspace scope (filter to this workspace)
        query_embedding: 1024-dim float32 query vector
        k: Number of results to return
    
    Returns:
        List of (node_id, similarity_score) tuples, ordered by similarity descending.
    """
    if query_embedding.shape[0] != _OPENSEARCH_VECTOR_DIM:
        logger.error(
            "✗ Invalid query embedding dimension: expected %d, got %d",
            _OPENSEARCH_VECTOR_DIM,
            query_embedding.shape[0],
        )
        return []
    
    try:
        session = get_opensearch_session()
        
        query_body = {
            "size": k,
            "query": {
                "knn": {
                    "embedding": {
                        "vector": query_embedding.tolist(),
                        "k": k,
                    }
                }
            },
            "post_filter": {
                "term": {
                    "workspace_id": workspace_id,
                }
            },
        }
        
        url = f"{_OPENSEARCH_ENDPOINT}/{_OPENSEARCH_INDEX_NAME}/_search"
        
        resp = session.post(
            url,
            json=query_body,
            verify=True,
        )
        
        if resp.status_code != 200:
            logger.error(
                "✗ Search failed: %s %s",
                resp.status_code,
                resp.text,
            )
            return []
        
        # Extract results
        results = []
        response = resp.json()
        hits = response.get("hits", {}).get("hits", [])
        
        for hit in hits:
            node_id = hit["_source"].get("node_id")
            score = hit.get("_score", 0.0)
            score = max(0.0, min(1.0, float(score)))
            
            if node_id:
                results.append((node_id, score))
        
        logger.debug(
            "✓ Search completed: workspace=%s, results=%d",
            workspace_id,
            len(results),
        )
        return results
        
    except Exception as e:
        logger.error(
            "✗ Failed to search vectors: workspace=%s, error=%s",
            workspace_id,
            e,
        )
        return []


async def health_check() -> bool:
    """Check OpenSearch connection."""
    try:
        session = get_opensearch_session()
        url = f"{_OPENSEARCH_ENDPOINT}/_cluster/health"
        resp = session.get(url, verify=True, timeout=5)
        
        if resp.status_code == 200:
            health = resp.json()
            logger.info(
                "✓ OpenSearch health check passed (status=%s)",
                health.get("status"),
            )
            return True
        else:
            logger.error("✗ OpenSearch health check failed: %s", resp.status_code)
            return False
        
    except Exception as e:
        logger.error("✗ OpenSearch health check failed: %s", e)
        return False
