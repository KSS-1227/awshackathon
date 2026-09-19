"""
LLM and multimodal LLM client — async/sync wrappers with KV caching.
AWS Bedrock backend: Amazon Nova Pro for all text/vision, Titan Text V2 for embeddings.

Model architecture (two models, one client pool):
  1. apac.amazon.nova-pro-v1:0  — all text inference (Converse API) AND vision
     inference (Converse API with image block).  Used by model_if_cache(),
     multimodel_if_cache(), get_llm_response(), get_mmllm_response().
  2. amazon.titan-embed-text-v2:0 — 1024-dim embeddings (InvokeModel API).
     Used by embed_texts() / local_embedding().  Different API, different
     model, same boto3 bedrock-runtime client.

Bedrock boto3 is synchronous; all calls run in asyncio.run_in_executor()
so the FastAPI event loop is never blocked.
"""
import ast
import asyncio
import json
import re
from typing import Any

import boto3
import numpy as np
from botocore.exceptions import ClientError

from ..config.settings import (
    BEDROCK_EMBED_DIMENSIONS,
    BEDROCK_EMBED_MODEL_ID,
    BEDROCK_MM_MODEL_ID,
    BEDROCK_TEXT_MODEL_ID,
    AWS_REGION,
)
from ..storage.kv_storage import BaseKVStorage
from ..utils.base import compute_args_hash, logger, wrap_embedding_func_with_attrs

# ============================================================================
# Singleton Bedrock client pool
# Two logical slots but both point to the same bedrock-runtime endpoint.
# "embed" is kept separate so it can be routed differently if needed.
# ============================================================================

_CLIENTS: dict[str, Any] = {
    "text": None,
    "mm": None,
    "embed": None,
}


def _get_bedrock_client(is_multimodal: bool = False):
    """Return singleton boto3 bedrock-runtime client for inference (synchronous)."""
    key = "mm" if is_multimodal else "text"
    if _CLIENTS[key] is None:
        _CLIENTS[key] = boto3.client("bedrock-runtime", region_name=AWS_REGION)
        logger.info(
            "✓ Bedrock inference client initialised (region=%s, model=%s)",
            AWS_REGION,
            BEDROCK_MM_MODEL_ID if is_multimodal else BEDROCK_TEXT_MODEL_ID,
        )
    return _CLIENTS[key]


def _get_embed_client():
    """Return singleton boto3 bedrock-runtime client for Titan embeddings (synchronous)."""
    if _CLIENTS["embed"] is None:
        _CLIENTS["embed"] = boto3.client("bedrock-runtime", region_name=AWS_REGION)
        logger.info(
            "✓ Bedrock embed client initialised (region=%s, model=%s)",
            AWS_REGION,
            BEDROCK_EMBED_MODEL_ID,
        )
    return _CLIENTS["embed"]


# ============================================================================
# Rate-limit / throttle retry helper
# ============================================================================

async def _with_retry(async_fn, max_retries: int = 6, base_delay: float = 10.0):
    """Exponential backoff on Bedrock ThrottlingException.

    All other ClientErrors are re-raised immediately so callers see the
    real error rather than waiting through 6 retries on a bad model ID.
    """
    for attempt in range(max_retries + 1):
        try:
            return await async_fn()
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code != "ThrottlingException":
                raise
            if attempt == max_retries:
                raise
            wait = min(base_delay * (2 ** attempt), 120.0)
            logger.warning(
                "Bedrock throttled (attempt %d/%d) — retrying in %.1fs",
                attempt + 1, max_retries, wait,
            )
            await asyncio.sleep(wait)


# ============================================================================
# Shared Bedrock Converse helper — Nova Pro uses Converse API, not invoke_model
# ============================================================================

def _converse_bedrock(
    client,
    model_id: str,
    messages: list[dict],
    system: str | None,
    max_tokens: int,
) -> str:
    """Synchronous Bedrock Converse call. Runs inside run_in_executor.

    Nova Pro uses the Converse API (not invoke_model with anthropic_version).
    Messages must use the Converse content-block format.
    """
    kwargs: dict = {
        "modelId": model_id,
        "messages": messages,
        "inferenceConfig": {"maxTokens": max_tokens},
    }
    if system:
        kwargs["system"] = [{"text": system}]
    response = client.converse(**kwargs)
    return response["output"]["message"]["content"][0]["text"]


# ============================================================================
# Embeddings — Amazon Titan Text Embeddings V2 (InvokeModel, not Converse)
# 1024-dim, normalised, via invoke_model with {"inputText": ..., "dimensions": 1024}
# ============================================================================

def _invoke_titan_embed(texts: list[str]) -> np.ndarray:
    """Synchronous batch embedding call to Titan Text V2. Runs in executor."""
    client = _get_embed_client()
    vectors = []
    for text in texts:
        body = json.dumps({
            "inputText": text[:8000],   # Titan V2 max input length
            "dimensions": BEDROCK_EMBED_DIMENSIONS,
            "normalize": True,
        })
        response = client.invoke_model(
            modelId=BEDROCK_EMBED_MODEL_ID,
            body=body,
            accept="application/json",
            contentType="application/json",
        )
        result = json.loads(response["body"].read())
        vectors.append(result["embedding"])
    return np.array(vectors, dtype=np.float32)


async def embed_texts(texts: list[str]) -> np.ndarray:
    """Async wrapper — runs Titan embed in executor, returns (N, 1024) float32 array."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _invoke_titan_embed(texts))


# local_embedding keeps the same decorator signature so callers using
# wrap_embedding_func_with_attrs (e.g. text2graph) still work unchanged.
@wrap_embedding_func_with_attrs(
    embedding_dim=BEDROCK_EMBED_DIMENSIONS,
    max_token_size=8000,
)
async def local_embedding(texts: list[str]) -> np.ndarray:
    """Drop-in replacement for the old SentenceTransformer local_embedding.

    Calls Titan Text Embeddings V2 via Bedrock.  Returns (N, 1024) float32.
    The @wrap_embedding_func_with_attrs decorator exposes .embedding_dim and
    .max_token_size for callers that inspect those attributes.
    """
    return await embed_texts(texts)


# ============================================================================
# Text LLM — Amazon Nova Pro via Bedrock Converse API
# ============================================================================

async def model_if_cache(
    prompt: str,
    system_prompt: str | None = None,
    history_messages: list[dict] | None = None,
    **kwargs,
) -> str:
    """Text LLM call with optional KV caching. Identical interface to OpenAI version."""
    if history_messages is None:
        history_messages = []

    hashing_kv: BaseKVStorage | None = kwargs.pop("hashing_kv", None)
    max_tokens: int = kwargs.get("max_tokens", 4096)

    # Convert to Converse message format
    messages = [
        *history_messages,
        {"role": "user", "content": [{"text": prompt}]},
    ]

    args_hash = None
    if hashing_kv:
        args_hash = compute_args_hash(BEDROCK_TEXT_MODEL_ID, messages)
        cached = await hashing_kv.get_by_id(args_hash)
        if cached:
            return cached["return"]

    async def _call():
        client = _get_bedrock_client(is_multimodal=False)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: _converse_bedrock(client, BEDROCK_TEXT_MODEL_ID, messages, system_prompt, max_tokens),
        )

    content = await _with_retry(_call)

    if hashing_kv and args_hash:
        await hashing_kv.upsert({args_hash: {"return": content, "model": BEDROCK_TEXT_MODEL_ID}})
        await hashing_kv.index_done_callback()

    return content


async def get_llm_response(cur_prompt: str, system_content: str) -> str:
    """Async text LLM call — used by fusion helpers."""
    messages = [{"role": "user", "content": [{"text": cur_prompt}]}]

    async def _call():
        client = _get_bedrock_client(is_multimodal=False)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: _converse_bedrock(client, BEDROCK_TEXT_MODEL_ID, messages, system_content, 4096),
        )

    return await _with_retry(_call)


# ============================================================================
# Multimodal LLM — Amazon Nova Pro vision via Bedrock Converse API
# ============================================================================

async def multimodel_if_cache(
    user_prompt: str,
    img_base: str,
    system_prompt: str,
    history_messages: list[dict] | None = None,
    **kwargs,
) -> str:
    """Vision LLM call with optional KV caching.

    img_base must be a base64-encoded JPEG string (no data-URI prefix).
    Nova Pro Converse image block uses bytes directly — we decode from base64
    before passing to boto3 which re-encodes internally.
    """
    if history_messages is None:
        history_messages = []

    hashing_kv: BaseKVStorage | None = kwargs.pop("hashing_kv", None)
    max_tokens: int = kwargs.get("max_tokens", 4096)

    import base64 as _b64
    img_bytes = _b64.b64decode(img_base)

    user_message = {
        "role": "user",
        "content": [
            {
                "image": {
                    "format": "jpeg",
                    "source": {"bytes": img_bytes},
                },
            },
            {"text": user_prompt},
        ],
    }
    messages = [*history_messages, user_message]

    args_hash = None
    if hashing_kv:
        # Hash on text parts only — bytes not hashable by compute_args_hash
        hash_messages = [
            *history_messages,
            {"role": "user", "content": [{"text": user_prompt}]},
        ]
        args_hash = compute_args_hash(BEDROCK_MM_MODEL_ID, hash_messages)
        cached = await hashing_kv.get_by_id(args_hash)
        if cached:
            return cached["return"]

    async def _call():
        client = _get_bedrock_client(is_multimodal=True)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: _converse_bedrock(client, BEDROCK_MM_MODEL_ID, messages, system_prompt, max_tokens),
        )

    content = await _with_retry(_call)

    if hashing_kv and args_hash:
        await hashing_kv.upsert({args_hash: {"return": content, "model": BEDROCK_MM_MODEL_ID}})
        await hashing_kv.index_done_callback()

    return content


async def get_mmllm_response(cur_prompt: str, system_content: str, img_base: str) -> str:
    """Async vision LLM call — used by image_utils helpers."""
    import base64 as _b64
    img_bytes = _b64.b64decode(img_base)

    user_message = {
        "role": "user",
        "content": [
            {
                "image": {
                    "format": "jpeg",
                    "source": {"bytes": img_bytes},
                },
            },
            {"text": cur_prompt},
        ],
    }

    async def _call():
        client = _get_bedrock_client(is_multimodal=True)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: _converse_bedrock(client, BEDROCK_MM_MODEL_ID, [user_message], system_content, 4096),
        )

    return await _with_retry(_call)


# ============================================================================
# JSON helpers (unchanged)
# ============================================================================

def normalize_to_json(output: str) -> dict | None:
    output = output.strip()
    match = re.search(r"```(?:json)?\s*(.*?)```", output, re.DOTALL)
    if match:
        output = match.group(1)
    match = re.search(r"\{.*\}", output, re.DOTALL)
    if not match:
        logger.debug("No JSON object found: %s...", output[:100])
        return None
    json_str = match.group(0)
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        pass
    try:
        repaired = re.sub(r"\bTrue\b", "true", json_str)
        repaired = re.sub(r"\bFalse\b", "false", repaired)
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass
    try:
        return ast.literal_eval(json_str)
    except (ValueError, SyntaxError) as exc:
        logger.debug("JSON decode failed: %s", exc)
        return None


def normalize_to_json_list(output: str) -> list[Any]:
    cleaned = output.replace('\\"', '"').strip()
    match = re.search(r"\[\s*(\{.*?\})*?\s*]", cleaned, re.DOTALL)
    if not match:
        logger.warning("No valid JSON list fragment found")
        return []
    json_str = match.group(0)
    json_str = re.sub(r",\s*]", "]", json_str)
    json_str = re.sub(r",\s*}$", "}", json_str)
    try:
        data = json.loads(json_str)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        logger.warning("Full list parse failed, attempting item-by-item parsing...")
    items = []
    for item_match in re.finditer(r"\{.*?\}", json_str, re.DOTALL):
        try:
            items.append(json.loads(item_match.group(0)))
        except json.JSONDecodeError:
            continue
    return items
