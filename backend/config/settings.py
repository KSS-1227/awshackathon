"""
Runtime configuration for the Enterprise Compliance Intelligence Platform.
...
"""
import logging
import os

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Load .env BEFORE any os.environ.get() calls so env vars are available
# regardless of import order (uvicorn reloader imports config before main.py).
from pathlib import Path as _Path
_project_root = _Path(__file__).parent.parent.parent  # backend/config/settings.py -> project_root
_backend_env = _project_root / "backend" / ".env"
load_dotenv(dotenv_path=str(_backend_env))

# ============ LLM Provider Configuration ============
# Controls which LLM backend is used for text and vision inference.
# Options: "bedrock" (AWS), "gemini" (Google, default for free tier)
# Default: "gemini" (switched from bedrock for cost/free-tier support)
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "gemini").lower()
MM_PROVIDER = os.environ.get("MM_PROVIDER", "gemini").lower()  # Can differ from LLM_PROVIDER for dual accounts
EMBEDDING_PROVIDER = os.environ.get("EMBEDDING_PROVIDER", "gemini").lower()

# ============ Text LLM Configuration ============
# LLM_PROVIDER = "gemini": Gemini API (free tier), OpenAI-compatible endpoint
# LLM_PROVIDER = "bedrock": AWS Bedrock (legacy fallback)
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_API_BASE = os.environ.get("LLM_API_BASE", "https://generativelanguage.googleapis.com/v1beta/openai/")
LLM_MODEL_NAME = os.environ.get("LLM_MODEL_NAME", "gemini-2.5-flash")

# AWS Bedrock text model (legacy fallback when LLM_PROVIDER=bedrock)
BEDROCK_TEXT_MODEL_ID = os.environ.get("BEDROCK_TEXT_MODEL_ID", "apac.amazon.nova-pro-v1:0")

# ============ Multimodal/Vision LLM Configuration ============
# MM_PROVIDER = "gemini": Gemini API (free tier), separate account for independent quota
# MM_PROVIDER = "bedrock": AWS Bedrock (legacy fallback)
MM_API_KEY = os.environ.get("MM_API_KEY", "")
MM_API_BASE = os.environ.get("MM_API_BASE", "https://generativelanguage.googleapis.com/v1beta/openai/")
MM_MODEL_NAME = os.environ.get("MM_MODEL_NAME", "gemini-2.5-flash")

# AWS Bedrock vision model (legacy fallback when MM_PROVIDER=bedrock)
BEDROCK_MM_MODEL_ID = os.environ.get("BEDROCK_MM_MODEL_ID", "apac.amazon.nova-pro-v1:0")

# ============ Embedding Model Configuration ============
# EMBEDDING_PROVIDER = "gemini": Gemini text-embedding-004 API (free tier)
# EMBEDDING_PROVIDER = "bedrock": AWS Bedrock Titan embeddings (legacy fallback)
# EMBEDDING_API_KEY: Can reuse LLM_API_KEY or specify separately for independent quota
EMBEDDING_API_KEY = os.environ.get("EMBEDDING_API_KEY", "") or LLM_API_KEY  # Falls back to LLM_API_KEY
EMBEDDING_API_BASE = os.environ.get("EMBEDDING_API_BASE", "https://generativelanguage.googleapis.com/v1beta/openai/")
EMBEDDING_MODEL_NAME = os.environ.get("EMBEDDING_MODEL_NAME", "text-embedding-004")
EMBEDDING_DIMENSIONS = int(os.environ.get("EMBEDDING_DIMENSIONS", "768"))  # Gemini embeddings are 768-dim

# AWS Bedrock embedding model (legacy fallback when EMBEDDING_PROVIDER=bedrock)
BEDROCK_EMBED_MODEL_ID = os.environ.get("BEDROCK_EMBED_MODEL_ID", "amazon.titan-embed-text-v2:0")
BEDROCK_EMBED_DIMENSIONS = int(os.environ.get("BEDROCK_EMBED_DIMENSIONS", "1024"))

# OPENAI_API_KEY is retained ONLY for Whisper audio transcription (no Gemini equivalent)
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
# (legacy Bedrock config moved to LLM_PROVIDER/MM_PROVIDER/EMBEDDING_PROVIDER sections above)


# ============ Directory Paths ============
INPUT_PDF_PATH = os.environ.get("INPUT_PDF_PATH", "data/input/2020.acl-main.45.pdf")
CACHE_PATH     = os.environ.get("CACHE_PATH",     "data/cache")
WORKING_DIR    = os.environ.get("WORKING_DIR",    "data/working")
OUTPUT_DIR     = os.environ.get("OUTPUT_DIR",     "data/output")
MMKG_NAME      = os.environ.get("MMKG_NAME",      "example_mmkg")

# ============ Processing Parameters ============
ENTITY_EXTRACT_MAX_GLEANING  = int(os.environ.get("ENTITY_EXTRACT_MAX_GLEANING",  "0"))
ENTITY_SUMMARY_MAX_TOKENS    = int(os.environ.get("ENTITY_SUMMARY_MAX_TOKENS",    "500"))
SUMMARY_CONTEXT_MAX_TOKENS   = int(os.environ.get("SUMMARY_CONTEXT_MAX_TOKENS",   "10000"))
# Default is False: PyMuPDF is fast enough for demo docs and requires no
# subprocess warm-up.  Set USE_MINERU=true in .env only when layout-aware
# parsing is visibly needed (e.g. complex multi-column PDFs with figures).
USE_MINERU = os.environ.get("USE_MINERU", "false").lower() in ("1", "true", "yes")

# ============ RAG Retrieval Configuration ============
class QueryParam:
    top_k: int = 5
    response_type: str = "Detailed System-like Response"
    local_max_token_for_local_context: int = 4000
    number_of_mmentities: int = 3
    local_max_token_for_text_unit: int = 4000

RETRIEVAL_THRESHOLD: float = 0.2

# ============ Audio / OpenAI Whisper Configuration ============
# Audio transcription uses the same OpenAI key as the LLM clients.

# ============ Auth / Supabase Configuration ============
SUPABASE_URL              = os.environ.get("SUPABASE_URL",              "")
SUPABASE_ANON_KEY         = os.environ.get("SUPABASE_ANON_KEY",         "")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
SUPABASE_JWT_SECRET       = os.environ.get("SUPABASE_JWT_SECRET",       "")

# Derived automatically — do NOT set this in the environment
SUPABASE_JWKS_URL = f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json"

# Comma-separated list of allowed CORS origins, e.g. "https://app.example.com,https://admin.example.com"
ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "")

# ============ DynamoDB Configuration ============
# DynamoDB table name for storing compliance platform data.
# Can be customized per environment via DYNAMODB_TABLE_NAME env var.
DYNAMODB_TABLE_NAME = os.environ.get("DYNAMODB_TABLE_NAME", "compliance-platform")

# AWS region where DynamoDB tables are located.
# Default: ap-south-1 (Mumbai region)
# Can be overridden via AWS_REGION env var.
AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")

# AWS access credentials for DynamoDB authentication.
# Optional: If not provided, IAM role-based authentication will be used.
# For local development or explicit credential usage, set these via env vars:
# - AWS_ACCESS_KEY_ID
# - AWS_SECRET_ACCESS_KEY
# WARNING: Do NOT hardcode credentials in this file. Always use environment variables.
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID", None)
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", None)

# ============ Vector Search Backend Configuration ============
# Controls where vector similarity search is performed.
# Options: "networkx" (default, brute-force cosine in Python)
#          "opensearch" (fast k-NN via Amazon OpenSearch)
# 
# When set to "opensearch", embeddings are indexed in OpenSearch for fast retrieval
# while still being stored in NetworkX node attributes for portability.
# Default is "networkx" to maintain existing behavior unless explicitly enabled.
VECTOR_SEARCH_BACKEND = os.environ.get("VECTOR_SEARCH_BACKEND", "networkx").lower()
