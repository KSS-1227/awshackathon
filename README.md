# Enterprise Compliance Intelligence Platform

**A Multi-Modal Knowledge Graph RAG Framework for Enterprise Compliance**

An end-to-end platform that transforms heterogeneous enterprise documents—PDFs, Word documents, Excel spreadsheets, audio recordings, and images—into unified multi-modal knowledge graphs. Powered by **GraphRAG retrieval**, **evidence scoring**, **citation verification**, **workspace multi-tenancy**, and an interactive **React 19 web dashboard**.

**LLM Providers**: Built on **Google Gemini** (default, free tier) with **AWS Bedrock fallback** and dual-account quota support for independent rate-limit scaling.

---

## Table of Contents

- [Key Features](#key-features)
- [Quick Start](#quick-start)
- [Architecture & Workflow](#architecture--workflow)
- [Technology Stack](#technology-stack)
- [LLM Providers & Configuration](#llm-providers--configuration)
- [Project Structure](#project-structure)
- [Environment Configuration](#environment-configuration)
- [Installation & Setup](#installation--setup)
- [Usage Instructions](#usage-instructions)
  - [Full-Stack Web Application](#1-running-the-full-stack-web-application)
  - [CLI Commands](#2-cli-usage)
  - [Standalone Visualization Server](#3-standalone-visualization-server)
  - [Docker Container](#4-docker-deployment)
  - [Evaluation & Benchmarks](#5-evaluation--benchmarks)
- [REST API Reference](#rest-api-reference)
- [Troubleshooting](#troubleshooting)
- [License](#license)

---

## Key Features

- **Multi-Modal Document Ingestion**: PDF, DOCX, XLSX, MP3/WAV audio (Whisper), PNG/JPG images, plain text with intelligent format detection and fallback parsers.
- **Dual PDF Extraction Engine**: Layout-aware MinerU parser with automatic PyMuPDF fallback for maximum compatibility.
- **Vision Processing**: Scene graph extraction from document figures, charts, and diagrams using Gemini vision API with automatic image format detection (PNG/JPEG).
- **Graph Fusion**: Merges text knowledge graphs and visual scene graphs into a unified MMKG via spectral clustering (DBSCAN + cosine similarity).
- **GraphRAG Retrieval**: Entity-level semantic search with local/global graph context, evidence confidence scoring, source citations, and multi-hop reasoning.
- **Workspace Multi-Tenancy**: Complete tenant isolation with Supabase Auth integration, JWT verification via JWKS, Role-Based Access Control (RBAC), and immutable audit trails.
- **Interactive Dashboard**: React 19 + TypeScript + `@xyflow/react` for force-directed graph exploration, workspace management, case tracking, and evidence visualization.
- **Flexible LLM Integration**: Provider-agnostic architecture supporting Gemini (primary, free tier), Bedrock (AWS fallback), and OpenAI-compatible endpoints with dual-account quota scaling.
- **Production-Ready**: Docker containerization (Python 3.12-slim), Render deployment (`render.yaml`), Vercel frontend support, and horizontal scalability.

---

## Quick Start

### Backend (5 minutes)

```bash
# Clone, create venv, install deps
git clone https://github.com/your-org/Multi-Modal-Graph.git
cd Multi-Modal-Graph
python -m venv venv
.\venv\Scripts\Activate.ps1  # Windows PowerShell
# or: source venv/bin/activate  # macOS/Linux
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env: set SUPABASE_*, LLM_API_KEY, LLM_MODEL_NAME, MM_API_KEY, etc.

# Start API server
python -m backend.api.run  # http://localhost:8000
```

### Frontend (5 minutes)

```bash
cd frontend
npm install
# Edit .env based on frontend/.env.example
npm run dev  # http://localhost:5173
```

### CLI Demo (1 minute)

```bash
# Extract entities from a PDF
python main.py -i data/input/sample.pdf

# Query the graph
python main.py -q "What are the main compliance risks?"
```

---

## Architecture & Workflow

```
```
                        [ Document Ingestion ]
    PDF / DOCX / XLSX / Audio (Whisper) / Images (PNG/JPG) / Text
                                  │
                  ┌───────────────┴───────────────┐
                  ▼                               ▼
          [ Text Processing ]            [ Visual Processing ]
      (MinerU/PyMuPDF + Chunking)   (Gemini Vision + Pillow)
                  │                               │
                  ▼                               ▼
         [ Text KG Construction ]       [ Scene Graph Extraction ]
      (Entity/Relation Extraction)     (Visual Entity Recognition)
                  │                               │
                  └───────────────┬───────────────┘
                                  ▼
                  [ Graph Fusion: Text + Visual ]
               (Spectral Clustering via DBSCAN)
                                  │
                                  ▼
          [ Unified Multi-Modal Knowledge Graph (MMKG) ]
                                  │
          ┌──────────────────────┼──────────────────────┐
          ▼                      ▼                      ▼
    [ Embeddings ]         [ Vector Storage ]    [ Auth & Storage ]
  (Gemini or Bedrock)   (NetworkX or OpenSearch) (Supabase + DB)
          │                     │                      │
          └──────────────────────┼──────────────────────┘
                                 ▼
                    [ GraphRAG Retrieval Engine ]
             (Entity Similarity + Context Building)
                                 │
          ┌──────────────────────┴──────────────────────┐
          ▼                                             ▼
    [ Evidence Scoring ]                        [ Citation Generation ]
    (Confidence Metrics)                      (Source Document Links)
          │                                             │
          └──────────────────────┬──────────────────────┘
                                 ▼
                    [ LLM Response Synthesis ]
                  (Answer + Evidence + Citations)
                                 │
                                 ▼
          [ Interactive Web Dashboard ]
         (React 19 + @xyflow/react Graph UI)
```

**Workflow Steps**:

1. **Ingestion**: Multi-format parsers (MinerU/PyMuPDF for PDF, `python-docx` for Word, `openpyxl` for Excel, Whisper for audio, Pillow for images).
2. **Text Processing**: Token-aware chunking → LLM entity/relation extraction → text knowledge graph construction.
3. **Visual Processing**: Image analysis via Gemini vision → scene graph extraction → visual entity recognition.
4. **Graph Fusion**: Spectral clustering (DBSCAN) aligns and merges text KG + visual scene graph → unified MMKG.
5. **Embeddings & Storage**: Entity descriptions embedded via Gemini (768-dim) or Bedrock Titan (1024-dim) → stored in NetworkX (default) or OpenSearch (k-NN for large graphs).
6. **Workspace Isolation**: All assets partitioned by workspace ID; guarded by Supabase JWT + RBAC middleware.
7. **GraphRAG Query**: Semantic entity search + local/global context retrieval → evidence collection → LLM synthesis → citations.
8. **Dashboard**: React UI displays interactive graph, evidence blocks, case management, and audit trails.

---

## LLM Providers & Configuration

### Provider Selection

The platform supports **three independent LLM axes**, each configurable to a different provider and account:

| Axis | Env Var | Purpose | Default |
|------|---------|---------|---------|
| **Text LLM** | `LLM_PROVIDER` | Entity extraction, RAG synthesis | `gemini` |
| **Vision/Multimodal** | `MM_PROVIDER` | Scene graph extraction, image analysis | `gemini` |
| **Embeddings** | `EMBEDDING_PROVIDER` | Entity vectorization (768-dim Gemini or 1024-dim Bedrock) | `gemini` |

**Supported Providers**:
- `gemini` — Google Gemini (free tier: 60 req/min, 1500 req/day per account)
- `bedrock` — AWS Bedrock (nova-pro-v1 text/vision, Titan embeddings)
- `openai` — OpenAI API (GPT-4o, text-embedding-3-large)

### Dual-Account Quota Scaling

To maximize free-tier capacity, use **two separate Gemini accounts**:

```env
# Account 1: Text extraction
LLM_PROVIDER=gemini
LLM_API_KEY=your_gemini_account_1_key

# Account 2: Vision processing (independent 60 req/min quota)
MM_PROVIDER=gemini
MM_API_KEY=your_gemini_account_2_key

# Embeddings: Reuse account 1
EMBEDDING_PROVIDER=gemini
EMBEDDING_API_KEY=your_gemini_account_1_key
```

This effectively doubles your rate limit (120 req/min combined) by partitioning text and vision workloads across accounts.

### Bedrock Fallback

To revert to AWS Bedrock (e.g., for comparison testing):

```env
LLM_PROVIDER=bedrock
MM_PROVIDER=bedrock
EMBEDDING_PROVIDER=bedrock

# AWS credentials
AWS_REGION=ap-south-1
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
```

**Dimension Note**: Bedrock Titan embeddings are 1024-dimensional; Gemini embeddings are 768-dimensional. The platform auto-configures OpenSearch indices to match. Switching providers may require re-indexing for OpenSearch users (NetworkX has no schema constraints).

### Vector Search Backend

**Default: NetworkX** (no infrastructure required)
- Brute-force cosine similarity
- Embedded in graph storage
- No external dependencies
- Sufficient for <10k entities

**Optional: OpenSearch** (managed k-NN vector search)
- Configure for large graphs (100k+ entities)
- AWS-hosted managed search
- Fast k-NN retrieval (<100ms)
- Requires IAM permissions (`indices:admin/*`, `indices:data/read/search`, `indices:data/write/index`)

```env
VECTOR_SEARCH_BACKEND=opensearch  # or: networkx (default)
OPENSEARCH_ENDPOINT=https://...
OPENSEARCH_REGION=ap-south-1
OPENSEARCH_INDEX_NAME=compliance-platform-vectors
```

---

## Technology Stack

### Backend
- **Framework**: Python 3.11+, FastAPI (ASGI), Uvicorn async web server
- **LLM Providers**: Google Gemini (primary, free tier), AWS Bedrock (fallback), OpenAI-compatible endpoints
- **Embeddings**: Gemini text-embedding-2 (768-dim) / Bedrock Titan (1024-dim) with local SentenceTransformer fallback
- **Vision**: Gemini via OpenAI-compatible endpoint with automatic image format detection (PNG/JPEG)
- **Audio**: OpenAI Whisper API for transcription
- **Graph & Math**: NetworkX 3.3+, scikit-learn (spectral clustering), NumPy, SciPy
- **PDF & Documents**: MinerU (layout-aware), PyMuPDF (fallback), `python-docx`, `openpyxl`, Pillow
- **Vector Search**: NetworkX (default) or OpenSearch 2.3+ (optional k-NN)
- **Authentication**: Supabase (managed PostgreSQL + Auth SaaS), PyJWT (JWT validation via JWKS)
- **Storage**: CockroachDB (persistent graphs/jobs) or local JSON/GraphML files
- **AWS** (optional): S3 (document backup), DynamoDB (job/audit logs), Bedrock (LLM fallback)

### Frontend
- **Framework**: React 19, TypeScript, Vite 6
- **Styling**: Tailwind CSS 4 + PostCSS + Lucide React icons
- **Graph Visualization**: `@xyflow/react` (React Flow 12) for force-directed layouts
- **State & Effects**: Framer Motion (animations), Supabase client (auth & real-time)
- **UI Utilities**: Class Variance Authority (cva), clsx, tailwind-merge

### Infrastructure & Deployment
- **Containerization**: Docker (Python 3.12-slim base image)
- **Backend Deployment**: Render.com (`render.yaml`), AWS Lambda, Heroku, self-hosted
- **Frontend Deployment**: Vercel, Netlify, AWS S3 + CloudFront
- **Code Quality**: Ruff (Python linter), Vitest (frontend testing), ESLint

---

## Project Structure

```
Multi-Modal-Graph/
├── backend/                        # Backend REST API & Knowledge Graph Engine (Python 3.11+)
│   ├── api/                        # FastAPI REST API Layer
│   │   ├── routes/                 # API Endpoint Modules
│   │   │   ├── cases.py            # Compliance case management endpoints
│   │   │   ├── graph.py            # Standalone/CLI graph retrieval
│   │   │   ├── query.py            # Legacy query endpoint (pre-workspace)
│   │   │   ├── report.py           # Legacy reporting (pre-workspace)
│   │   │   ├── storage.py          # Backend health & status endpoints
│   │   │   ├── upload.py           # Legacy upload (pre-workspace)
│   │   │   ├── workspace_graph.py  # Workspace-scoped graph endpoints
│   │   │   ├── workspace_query.py  # Workspace-scoped GraphRAG queries
│   │   │   ├── workspace_report.py # Workspace-scoped compliance reports
│   │   │   └── workspace_upload.py # Workspace-scoped multi-modal document ingestion
│   │   └── main.py                 # FastAPI app initialization & CORS config
│   ├── auth/                       # Authentication & Multi-Tenancy (Supabase + JWT)
│   │   ├── middleware/             # JWT validation middleware
│   │   ├── migrations/             # Database schema migrations (SQL)
│   │   ├── rbac/                   # Role-based access control engine
│   │   ├── routes/                 # Auth, workspace, profile, audit endpoints
│   │   ├── services/               # Supabase auth & workspace management services
│   │   ├── dependencies.py         # FastAPI dependency providers
│   │   ├── supabase_client.py      # Supabase client singleton
│   │   └── workspace.py            # Workspace context helpers
│   ├── compliance/                 # Compliance & Evidence Engines
│   │   ├── citation_engine.py      # Citation extraction & linking
│   │   └── evidence_engine.py      # Evidence scoring & verification
│   ├── config/                     # Configuration Management
│   │   ├── settings.py             # Env var parsing, LLM provider selection, defaults
│   │   └── __init__.py             # Re-exports for module access
│   ├── core/                       # Core Constants & Prompts
│   │   ├── prompt.py               # LLM prompt templates (entity extraction, RAG)
│   │   └── __init__.py
│   ├── graph/                      # Multi-Modal Knowledge Graph Construction
│   │   ├── fusion.py               # Text KG + Visual KG fusion (spectral clustering)
│   │   ├── img2graph.py            # Image scene graph extraction (vision LLM)
│   │   ├── text2graph.py           # Text entity/relation extraction pipeline
│   │   └── utils.py                # Graph algorithms & matrix operations
│   ├── ingestion/                  # Multi-Format Document Parsers
│   │   ├── audio_preprocessing.py  # Whisper audio transcription
│   │   ├── docx_preprocessing.py   # Microsoft Word (.docx) extraction
│   │   ├── excel_preprocessing.py  # Microsoft Excel (.xlsx) extraction
│   │   ├── image_preprocessing.py  # Image format detection & preprocessing
│   │   ├── image_utils.py          # Image compression & MIME type detection
│   │   └── pdf_preprocessing.py    # MinerU (layout-aware) + PyMuPDF fallback
│   ├── llm/                        # LLM Provider Interface (Provider-Agnostic)
│   │   └── client.py               # Gemini/Bedrock/OpenAI branching + async retries + caching
│   ├── retrieval/                  # GraphRAG Query Engine
│   │   └── query.py                # Semantic entity search + context building + synthesis
│   ├── services/                   # Business Logic & Orchestration
│   │   ├── document_service.py     # Document lifecycle management
│   │   ├── multidocument_service.py # Multi-document batch fusion & indexing
│   │   ├── query_service.py        # Query orchestration (retrieval + evidence + synthesis)
│   │   └── workspace_document_service.py # Workspace-scoped document service
│   ├── storage/                    # Persistence Layer
│   │   ├── embeddings_storage.py   # Embedding attribute management
│   │   ├── graph_storage.py        # NetworkX GraphML persistence
│   │   ├── kv_storage.py           # Key-value JSON cache
│   │   ├── opensearch_vectors.py   # OpenSearch k-NN vector indexing
│   │   └── migrations/             # Database schema migrations
│   ├── utils/                      # Shared Utilities
│   │   └── base.py                 # Token counting, hashing, logging
│   ├── visualization/              # Standalone Flask Graph Explorer
│   │   ├── graph_explorer.html     # D3 force-directed visualization template
│   │   └── server.py               # Flask visualization server
│   ├── builder.py                  # MMKGBuilder: orchestrator for full pipeline
│   └── __init__.py                 # Package re-exports
│
├── frontend/                       # React 19 Web Dashboard (TypeScript + Vite)
│   ├── src/
│   │   ├── api/                    # Backend API client wrappers (fetch/axios)
│   │   ├── auth/                   # Supabase auth & token management
│   │   ├── components/             # React UI components + @xyflow/react graph canvas
│   │   ├── hooks/                  # Custom React hooks (workspace guard, auth checks)
│   │   ├── pages/                  # Application views (Dashboard, Graph, Query, Cases, Reports)
│   │   ├── App.tsx                 # Root router & layout
│   │   └── main.tsx                # Entry point
│   ├── package.json                # Dependencies & build scripts
│   ├── tailwind.config.js          # Tailwind CSS configuration
│   ├── vite.config.ts              # Vite dev server & proxy config
│   └── tsconfig.json               # TypeScript configuration
│
├── data/                           # Runtime Data Directories (gitignored)
│   ├── input/                      # Source documents for ingestion
│   ├── output/                     # Final GraphML + embeddings (gitignored)
│   ├── cache/                      # LLM response cache (gitignored)
│   └── working/                    # Intermediate build artifacts (gitignored)
│
├── eval/                           # Evaluation & Benchmarking Suite
│   ├── evaluate.py                 # Main evaluation runner
│   └── gold_sample.json            # Ground-truth QA reference set
│
├── examples/                       # Examples & Documentation
│   ├── docqa_example.py            # End-to-end Q&A evaluation demo
│   ├── example_input/              # Sample academic PDF + QA dataset
│   └── paper/                      # Research paper & architecture diagrams
│
├── tests/                          # Automated Test Suite (Backend)
│   └── (test files for backend modules)
│
├── .env.example                    # Backend environment variables template (Gemini defaults)
├── .gitignore                      # Git ignore patterns
├── ARCHITECTURE_REFACTOR.md        # Documentation of recent architecture improvements
├── Dockerfile                      # Container build specification (Python 3.12-slim)
├── LICENSE                         # Apache 2.0 License
├── main.py                         # CLI entry point for graph building/querying
├── pyproject.toml                  # Python package config, build tools, linter settings
├── render.yaml                     # Render.com deployment manifest
├── requirements.txt                # Python backend dependencies (pinned versions)
└── README.md                       # This file
```

---

## Environment Configuration

### Backend Environment Variables (`.env`)

**Overview**: The platform uses **provider-agnostic LLM branching** to support Gemini (default), Bedrock (AWS fallback), and OpenAI-compatible endpoints. Configure which provider to use via environment variables.

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

Then edit `.env` with your API keys and service credentials.

#### LLM Provider Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `LLM_PROVIDER` | No | `gemini` | Text LLM provider: `gemini`, `bedrock`, `openai` |
| `LLM_API_KEY` | **Yes** | — | API key for selected LLM provider (e.g., Gemini API key) |
| `LLM_API_BASE` | No | `https://generativelanguage.googleapis.com/v1beta/openai/` | Endpoint URL (Gemini OpenAI-compatible) |
| `LLM_MODEL_NAME` | No | `gemini-3.6-flash` | Model identifier (Gemini, Bedrock, or OpenAI) |
| `MM_PROVIDER` | No | `gemini` | Vision/multimodal provider (can differ from `LLM_PROVIDER`) |
| `MM_API_KEY` | No | `${LLM_API_KEY}` | Separate API key for vision (supports dual-account quota scaling) |
| `MM_API_BASE` | No | `https://generativelanguage.googleapis.com/v1beta/openai/` | Vision endpoint URL |
| `MM_MODEL_NAME` | No | `gemini-3.6-flash` | Vision model identifier |
| `EMBEDDING_PROVIDER` | No | `gemini` | Embeddings provider |
| `EMBEDDING_API_KEY` | No | `${LLM_API_KEY}` | Separate embedding API key (or reuse text account) |
| `EMBEDDING_API_BASE` | No | `https://generativelanguage.googleapis.com/v1beta/openai/` | Embeddings endpoint |
| `EMBEDDING_MODEL_NAME` | No | `gemini-embedding-2` | Embedding model (Gemini, Bedrock Titan, OpenAI) |
| `EMBEDDING_DIMENSIONS` | No | `768` | Embedding vector dimension (768 for Gemini, 1024 for Bedrock) |

#### Bedrock AWS Credentials (when using `*_PROVIDER=bedrock`)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `AWS_REGION` | When Bedrock used | — | AWS region (e.g., `ap-south-1`) |
| `AWS_ACCESS_KEY_ID` | When Bedrock used | — | AWS IAM access key |
| `AWS_SECRET_ACCESS_KEY` | When Bedrock used | — | AWS IAM secret key |

#### Authentication & Supabase

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SUPABASE_URL` | **Yes** | — | Supabase project URL (e.g., `https://xxxx.supabase.co`) |
| `SUPABASE_ANON_KEY` | **Yes** | — | Supabase anonymous/public API key |
| `SUPABASE_SERVICE_ROLE_KEY` | **Yes** | — | Supabase service role key (for backend admin operations) |
| `SUPABASE_JWT_SECRET` | **Yes** | — | Secret used to verify JWT tokens from Supabase |

#### Audio & Additional LLM Services

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENAI_API_KEY` | When using Whisper | — | OpenAI API key for Whisper audio transcription (no Gemini equivalent) |

#### Database & Persistence

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `COCKROACH_DATABASE_URL` | No | (local JSON mode) | CockroachDB connection string for persistent graph storage |
| `DYNAMODB_TABLE_NAME` | When using DynamoDB | — | DynamoDB table name for audit logs / job state |

#### Vector Search Backend

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `VECTOR_SEARCH_BACKEND` | No | `networkx` | Vector search: `networkx` (default) or `opensearch` |
| `OPENSEARCH_ENDPOINT` | When using OpenSearch | — | OpenSearch domain endpoint (e.g., `https://search-xxx.us-east-1.es.amazonaws.com`) |
| `OPENSEARCH_REGION` | When using OpenSearch | — | AWS region hosting OpenSearch (e.g., `ap-south-1`) |
| `OPENSEARCH_INDEX_NAME` | When using OpenSearch | `compliance-platform-vectors` | Index name for vector storage |

#### Document Storage

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DOCUMENT_STORAGE_BACKEND` | No | `local` | Storage backend: `local` (disk) or `s3` (AWS S3) |
| `S3_DOCUMENT_BUCKET` | When using S3 | — | S3 bucket name for raw document backup |

#### Processing Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `INPUT_PDF_PATH` | No | `data/input/` | Directory for source documents |
| `WORKING_DIR` | No | `data/working` | Temporary build directory |
| `CACHE_PATH` | No | `data/cache` | LLM response cache directory |
| `OUTPUT_DIR` | No | `data/output` | Final GraphML + embedding outputs |
| `USE_MINERU` | No | `false` | Enable MinerU layout-aware PDF parsing (`true`/`false`) |
| `MMKG_NAME` | No | `example_mmkg` | Default knowledge graph output filename |
| `ENTITY_EXTRACT_MAX_GLEANING` | No | `0` | Max LLM re-attempts for entity extraction |
| `ENTITY_SUMMARY_MAX_TOKENS` | No | `500` | Max tokens per entity summary |
| `SUMMARY_CONTEXT_MAX_TOKENS` | No | `10000` | Max context tokens for LLM summaries |
| `RETRIEVAL_THRESHOLD` | No | `0.2` | Embedding similarity threshold for entity matching |

#### API & Web Server

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ALLOWED_ORIGINS` | No | `http://localhost:5173,http://localhost:3000` | CORS allowed origins (comma-separated) |



### Frontend Environment Variables (`frontend/.env`)

Copy `frontend/.env.example` to `frontend/.env`:

```bash
cp frontend/.env.example frontend/.env
```

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `VITE_SUPABASE_URL` | **Yes** | — | Supabase project URL |
| `VITE_SUPABASE_ANON_KEY` | **Yes** | — | Supabase anon key |
| `VITE_API_BASE` | No | `""` (proxied in dev) | Deployed backend URL (e.g. `https://api.example.com`). Leave empty in development to use Vite's dev proxy to `http://localhost:8000`. |

---

## Installation & Setup

### Prerequisites

- **Python**: Version `3.11` or higher (`3.12` recommended for Docker builds).
- **Node.js**: Version `18.0+` (`npm` included).
- **Supabase Account**: Active project with JWT auth configured.
- **LLM API Keys**: At least one of:
  - Google Gemini API key (recommended for free tier: 60 req/min, 1500 req/day)
  - AWS Bedrock access + credentials (for fallback or comparison)
  - OpenAI API key (for Whisper audio or alternative)

---

### Backend Setup

1. **Clone repository**:
   ```bash
   git clone https://github.com/your-org/Multi-Modal-Graph.git
   cd Multi-Modal-Graph
   ```

2. **Create virtual environment**:
   ```bash
   python -m venv venv
   # Activate:
   .\venv\Scripts\Activate.ps1          # Windows PowerShell
   # or:
   source venv/bin/activate             # macOS/Linux
   ```

3. **Install dependencies**:
   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

4. **(Optional) Install MinerU for advanced PDF parsing**:
   ```bash
   pip install -U "mineru[all]"
   ```
   If not installed or `USE_MINERU=false`, the backend falls back to PyMuPDF automatically.

5. **Configure environment**:
   ```bash
   cp .env.example .env
   # Edit .env: Add Supabase credentials, LLM API keys, etc.
   ```

   **Minimal config for quick testing**:
   ```env
   # Gemini (free tier)
   LLM_PROVIDER=gemini
   LLM_API_KEY=your_gemini_key_here
   MM_API_KEY=your_gemini_key_or_account_2_here
   EMBEDDING_API_KEY=your_gemini_key_here
   
   # Supabase (required for web app)
   SUPABASE_URL=your_project_url
   SUPABASE_ANON_KEY=your_anon_key
   SUPABASE_SERVICE_ROLE_KEY=your_service_role_key
   SUPABASE_JWT_SECRET=your_jwt_secret
   
   # CORS for local dev
   ALLOWED_ORIGINS=http://localhost:5173,http://localhost:8000
   ```

6. **Initialize database** (optional, for CockroachDB):
   ```bash
   psql "$COCKROACH_DATABASE_URL" -f backend/auth/migrations/001_auth_schema.sql
   ```
   (If not using CockroachDB, local JSON-based storage is used automatically.)

---

### Frontend Setup

1. **Navigate to frontend**:
   ```bash
   cd frontend
   ```

2. **Install Node dependencies**:
   ```bash
   npm install
   ```

3. **Configure frontend environment**:
   ```bash
   cp .env.example .env  # (if it exists)
   # Edit .env:
   VITE_SUPABASE_URL=your_supabase_url
   VITE_SUPABASE_ANON_KEY=your_anon_key
   VITE_API_BASE=  # Leave empty for dev proxy to http://localhost:8000
   ```

---

## Prerequisites

- **Python**: Version `3.11` or higher (`3.12` recommended for containerized builds).
- **Node.js**: Version `18.0.0` or higher (`npm` included).
- **Supabase Account**: An active Supabase project with JWT authentication enabled.
- **OpenAI API Key**: An active key with access to `gpt-4o` and audio models.

---

### Backend Setup

1. **Clone the repository**:
   ```bash
   git clone https://github.com/your-org/InnovaHack.git
   cd InnovaHack
   ```

2. **Create and activate a virtual environment**:
   ```bash
   python -m venv venv
   # On macOS/Linux:
   source venv/bin/activate
   # On Windows (PowerShell):
   .\venv\Scripts\Activate.ps1
   ```

3. **Install Python dependencies**:
   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

4. **(Optional) Install MinerU for advanced PDF layout extraction**:
   ```bash
   pip install -U "mineru[all]"
   ```
   *Note: If MinerU is not installed or `USE_MINERU=false`, the backend seamlessly falls back to PyMuPDF (`pymupdf`).*

### CockroachDB migration

Set `COCKROACH_DATABASE_URL` to your cluster connection string, then apply
[`backend/storage/migrations/001_cockroachdb_schema.sql`](backend/storage/migrations/001_cockroachdb_schema.sql)
before starting graph ingestion:

```bash
psql "$COCKROACH_DATABASE_URL" -f backend/storage/migrations/001_cockroachdb_schema.sql
```

You can run the same SQL file in the CockroachDB Cloud SQL shell. If using an
MCP client during development, connect that client to the cluster and execute
the migration through the server's discovered SQL tool; do not expose the
database URL to the frontend.

### CockroachDB Cloud MCP

Use CockroachDB Cloud's managed **streamable HTTP** MCP server from a real MCP
client (for example Cursor or Claude Code). Configure the client with the
cluster ID and either OAuth or a service-account API key:

```json
{
  "mcpServers": {
    "cockroachdb-cloud": {
      "type": "http",
      "url": "https://cockroachlabs.cloud/mcp",
      "headers": {
        "mcp-cluster-id": "<cluster-id>",
        "Authorization": "Bearer <service-account-api-key>"
      }
    }
  }
}
```

The client performs the MCP initialization and tool discovery handshake; do
not replace this with a raw HTTP SQL request. The tool names are discovered
from the connected server so they are not hard-coded in this repository.

5. **Configure environment variables**:
   Create `.env` based on `.env.example` and set your API keys and Supabase credentials.

---

### Frontend Setup

1. **Navigate to the frontend directory**:
   ```bash
   cd frontend
   ```

2. **Install Node dependencies**:
   ```bash
   npm install
   ```

3. **Configure frontend environment variables**:
   Create `frontend/.env` based on `frontend/.env.example`.

---

## Usage Instructions

### 1. Running the Full-Stack Web Application

#### Start the FastAPI Backend Server:
```bash
# From project root:
python -m backend.api.run
```
- API Base URL: `http://localhost:8000`
- Interactive OpenAPI Docs: `http://localhost:8000/docs`
- ReDoc Docs: `http://localhost:8000/redoc`

#### Start the React Frontend Dashboard:
```bash
# From frontend/ directory:
npm run dev
```
- Open `http://localhost:5173` in your browser.

---

### 2. CLI Usage

The root `main.py` provides a CLI for command-line graph indexing, querying, and visualization:

```bash
# Build knowledge graph from a document (PDF, DOCX, XLSX, MP3, WAV, PNG)
python main.py -i data/input/compliance_report.pdf

# Query the knowledge graph via GraphRAG
python main.py -q "What are the primary compliance risks described in the document?"

# Force rebuild ignoring cache
python main.py -i data/input/compliance_report.pdf -f

# Specify custom working and output directories
python main.py -i data/input/compliance_report.pdf -w data/working -o data/output

# Run with verbose debugging logs
python main.py -i data/input/compliance_report.pdf -v
```

#### CLI Flag Reference

| Flag | Short | Description |
|------|-------|-------------|
| `--input` | `-i` | Input file path (`.pdf`, `.docx`, `.xlsx`, `.mp3`, `.wav`, `.png`, etc.) |
| `--query` | `-q` | RAG query string |
| `--serve` | `-s` | Launch standalone Flask visualization server |
| `--working-dir` | `-w` | Override working directory |
| `--output-dir` | `-o` | Override output directory |
| `--mmkg-name` | `-m` | Override knowledge graph output name |
| `--force` | `-f` | Force rebuild (bypass cache) |
| `--verbose` | `-v` | Enable verbose logging |
| `--port` | — | Server port for visualization server (default: `5000`) |

---

### 3. Standalone Visualization Server

To run the legacy/standalone Flask graph visualizer UI:

```bash
python main.py -s --port 5000
# Access interactive graph explorer at http://localhost:5000
```

---

### 4. Docker Deployment

Build and run the containerized backend using Docker:

```bash
# Build the Docker image
docker build -t innova-backend .

# Run the container
docker run -d -p 8000:8000 --env-file .env --name innova-backend-container innova-backend
```

---

### 5. Evaluation & Benchmarks

Run the built-in document Q&A evaluation benchmark:

```bash
# Run docqa evaluation script
python examples/docqa_example.py

# Run general evaluation pipeline
python eval/evaluate.py
```

---

## REST API Reference

The backend exposes a full suite of REST endpoints guarded by JWT authentication (`Bearer <token>`).

### Public Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/` | System status & app details |
| `GET` | `/health` | Service health & dependency check |
| `POST` | `/api/auth/register` | Register a new user |
| `POST` | `/api/auth/login` | Login user & issue session token |

### Protected Workspace Endpoints (`Bearer <token>` required)

| Category | Method | Endpoint | Description |
|----------|--------|----------|-------------|
| **Workspaces** | `GET` | `/api/workspaces` | List accessible user workspaces |
| | `POST` | `/api/workspaces` | Create a new workspace |
| | `GET` | `/api/workspaces/{id}` | Get workspace details |
| | `GET` | `/api/workspaces/{id}/audit` | Retrieve workspace audit logs |
| **Ingestion** | `POST` | `/api/workspace/upload` | Ingest document into workspace & trigger KG build |
| **GraphRAG** | `POST` | `/api/workspace/query` | Execute workspace GraphRAG query |
| | `GET` | `/api/workspace/graph` | Fetch graph nodes/edges for visual explorer |
| | `POST` | `/api/workspace/report` | Generate compliance report with citations & evidence |
| **Cases** | `GET` | `/api/cases` | List workspace compliance cases |
| | `POST` | `/api/cases` | Create a new compliance case |
| | `GET` | `/api/cases/{id}` | Retrieve case details & attached evidence |
| **Storage** | `GET` | `/api/storage/status` | Storage health & backend persistence status |

---

## Troubleshooting

### Common Issues

**1. LLM API Errors (401 Unauthorized, 403 Forbidden)**
- Verify API keys are correct in `.env`
- Check provider quotas (Gemini: 60 req/min free tier)
- For Gemini, ensure both accounts are separate (use different emails for dual-account setup)
- For Bedrock, verify AWS credentials and IAM permissions

**2. "No module named 'mineru'" errors**
- MinerU is optional. Set `USE_MINERU=false` in `.env` to use PyMuPDF fallback
- If you need MinerU: `pip install -U "mineru[all]"` may require 2+ GB disk space

**3. OpenSearch 403 Forbidden errors**
- OpenSearch operations require IAM permissions: `indices:admin/*`, `indices:data/write/*`, `indices:data/read/search`
- Use `VECTOR_SEARCH_BACKEND=networkx` (default) instead of OpenSearch if permissions unavailable
- Or request AWS admin to grant permissions on the OpenSearch domain

**4. Supabase JWT validation failures**
- Ensure `SUPABASE_JWT_SECRET` matches your Supabase project secret
- Verify `SUPABASE_URL` and `SUPABASE_ANON_KEY` are from the same project
- Check that authentication is enabled in Supabase settings

**5. Embedding dimension mismatch**
- Bedrock Titan: 1024 dimensions
- Gemini embeddings: 768 dimensions
- OpenSearch indices must be created with matching dimensions
- Switching providers may require re-indexing (use `VECTOR_SEARCH_BACKEND=networkx` to avoid)

**6. CORS errors in frontend**
- Ensure `ALLOWED_ORIGINS` in `.env` includes your frontend URL
- For local dev, add: `http://localhost:5173,http://localhost:3000`

**7. Document upload fails**
- Verify input document format is supported (PDF, DOCX, XLSX, MP3, WAV, PNG, JPG)
- Check `INPUT_PDF_PATH` directory exists and is readable
- Ensure disk space available in `WORKING_DIR` and `OUTPUT_DIR`

### Getting Help

- **See logs**: Run with `-v` flag for verbose output: `python main.py -i doc.pdf -v`
- **Check API status**: `curl http://localhost:8000/health`
- **Review OpenAPI docs**: `http://localhost:8000/docs` (FastAPI Swagger UI)
- **Inspect network**: Use browser DevTools or `curl` to check backend responses

---



This project is licensed under the Apache 2.0 License - see the [LICENSE](LICENSE) file for details.
