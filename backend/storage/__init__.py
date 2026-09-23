"""
Storage package — KV store, graph store, and DynamoDB abstractions.
"""
from .graph_storage import (
    BaseGraphStorage,
    NetworkXStorage,
)
from .kv_storage import (
    BaseKVStorage,
    JsonKVStorage,
    StorageNameSpace,
    TextChunkSchema,
)
from .dynamodb_client import (
    DynamoDBClient,
    get_dynamodb_client,
)
from .dynamodb_workspaces import (
    list_workspaces_for_user,
)

# OpenSearch is optional — only import if available
try:
    from . import opensearch_vectors
except ImportError:
    opensearch_vectors = None

__all__ = [
    "BaseGraphStorage",
    "BaseKVStorage",
    "JsonKVStorage",
    "NetworkXStorage",
    "StorageNameSpace",
    "TextChunkSchema",
    "DynamoDBClient",
    "get_dynamodb_client",
    "list_workspaces_for_user",
    "opensearch_vectors",
]
