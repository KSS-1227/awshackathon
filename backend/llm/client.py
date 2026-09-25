"""
LLM and multimodal LLM client — provider-branched (Bedrock/Gemini/OpenAI-compatible).

Supports multiple LLM backends:
  - Bedrock (AWS): Nova Pro for text/vision, Titan for embeddings (legacy)
  - Gemini (Google): via OpenAI-compatible endpoint, text-embedding-004 for embeddings (default, free tier)
  - OpenAI-compatible: Any provider with OpenAI API (e.g., local LM Studio)

Provider branching:
  - LLM_PROVIDER: text LLM backend (default: "gemini")
  - MM_PROVIDER: vision LLM backend (default: "gemini", can differ for dual accounts)
  - EMBEDDING_PROVIDER: embeddings backend (default: "gemini")

All calls are async via asyncio.run_in_executor() so FastAPI event loop is never blocked.
"""
import ast
import asyncio
import base64
import json
import logging
import re
from typing import Any

import boto3
import httpx
import numpy as np
from botocore.exceptions import ClientError

from ..config.settings import (
    AWS_REGION,
    BEDROCK_EMBED_DIMENSIONS,
    BEDROCK_EMBED_MODEL_ID,
    BEDROCK_MM_MODEL_ID,
    BEDROCK_TEXT_MODEL_ID,
    EMBEDDING_API_BASE,
    EMBEDDING_API_KEY,
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL_NAME,
    EMBEDDING_PROVIDER,
    LLM_API_BASE,
    LLM_API_KEY,
    LLM_MODEL_NAME,
    LLM_PROVIDER,
    MM_API_BASE,
    MM_API_KEY,
    MM_MODEL_NAME,
    MM_PROVIDER,
)
from ..storage.kv_storage import BaseKVStorage
from ..utils.base import compute_args_hash, logger, wrap_embedding_func_with_attrs

# ============================================================================
# Singleton Bedrock client pool (for LLM_PROVIDER=bedrock fallback)
# ============================================================================

_BEDROCK_CLIENTS: dict[str, Any] = {
    "text": None,
    "mm": None,
    "embed": None,
}


def _get_bedrock_client(is_multimodal: bool = False):
    """Return singleton boto3 bedrock-runtime client."""
    key = "mm" if is_multimodal else "text"
    if _BEDROCK_CLIENTS[key] is None:
        _BEDROCK_CLIENTS[key] = boto3.client("bedrock-runtime", region_name=AWS_REGION)
        logger.info(
            "✓ Bedrock inference client initialized (region=%s, model=%s)",
            AWS_REGION,
            BEDROCK_MM_MODEL_ID if is_multimodal else BEDROCK_TEXT_MODEL_ID,
        )
    return _BEDROCK_CLIENTS[key]


def _get_bedrock_embed_client():
    """Return singleton boto3 bedrock-runtime client for embeddings."""
    if _BEDROCK_CLIENTS["embed"] is None:
        _BEDROCK_CLIENTS["embed"] = boto3.client("bedrock-runtime", region_name=AWS_REGION)
        logger.info(
            "✓ Bedrock embed client initialized (region=%s, model=%s)",
            AWS_REGION,
            BEDROCK_EMBED_MODEL_ID,
        )
    return _BEDROCK_CLIENTS["embed"]


# ============================================================================
# OpenAI-compatible client helper (for LLM_PROVIDER=gemini/openai)
# ============================================================================

async def _call_openai_compatible(
    api_key: str,
    api_base: str,
    model: str,
    messages: list[dict],
    system: str | None = None,
    max_tokens: int = 4096,
    is_vision: bool = False,
) -> str:
    """Generic OpenAI-compatible API call (works with Gemini, OpenAI, etc.)."""
    if not api_key:
        raise ValueError(f"API key missing for {api_base}")
    
    # Prepend system message if provided
    if system:
        messages = [{"role": "system", "content": system}] + messages
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    
    # Add temperature for more deterministic responses
    payload["temperature"] = 0.7
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        url = f"{api_base.rstrip('/')}/chat/completions"
        response = await client.post(url, json=payload, headers=headers)
        
        if response.status_code != 200:
            # Log error details
            error_body = response.text
            logger.warning(
                "API error %d for %s: %s",
                response.status_code,
                url,
                error_body[:200],
            )
        
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]


# ============================================================================
# Bedrock Converse helper (legacy)
# ============================================================================

def _converse_bedrock(
    client,
    model_id: str,
    messages: list[dict],
    system: str | None,
    max_tokens: int,
) -> str:
    """Synchronous Bedrock Converse call."""
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
# Rate-limit / throttle retry helper
# ============================================================================

async def _with_retry(async_fn, max_retries: int = 3, base_delay: float = 1.0):
    """Exponential backoff for transient errors."""
    for attempt in range(max_retries + 1):
        try:
            return await async_fn()
        except (httpx.HTTPError, ClientError) as exc:
            if attempt == max_retries:
                raise
            wait = min(base_delay * (2 ** attempt), 30.0)
            logger.warning(
                "Request failed (attempt %d/%d) — retrying in %.1fs: %s",
                attempt + 1, max_retries, wait, str(exc)[:100],
            )
            await asyncio.sleep(wait)


# ============================================================================
# Text LLM — Provider-branched (Bedrock/Gemini/OpenAI-compatible)
# ============================================================================

async def model_if_cache(
    prompt: str,
    system_prompt: str | None = None,
    history_messages: list[dict] | None = None,
    **kwargs,
) -> str:
    """Text LLM call with optional KV caching. Supports multiple providers."""
    if history_messages is None:
        history_messages = []

    hashing_kv: BaseKVStorage | None = kwargs.pop("hashing_kv", None)
    max_tokens: int = kwargs.get("max_tokens", 2048)  # Increased from 4096 for Gemini thinking tokens

    messages = [
        *history_messages,
        {"role": "user", "content": prompt},
    ]

    cache_key = f"{LLM_PROVIDER}:{LLM_MODEL_NAME}"
    args_hash = None
    if hashing_kv:
        args_hash = compute_args_hash(cache_key, messages)
        cached = await hashing_kv.get_by_id(args_hash)
        if cached:
            return cached["return"]

    async def _call():
        if LLM_PROVIDER == "bedrock":
            # Convert to Bedrock Converse format
            client = _get_bedrock_client(is_multimodal=False)
            loop = asyncio.get_event_loop()
            bedrock_messages = [
                {"role": "user", "content": [{"text": m["content"]}]} if m["role"] == "user" else
                {"role": "assistant", "content": [{"text": m["content"]}]}
                for m in messages
            ]
            return await loop.run_in_executor(
                None,
                lambda: _converse_bedrock(client, BEDROCK_TEXT_MODEL_ID, bedrock_messages, system_prompt, max_tokens),
            )
        else:  # gemini, openai, or other OpenAI-compatible
            return await _call_openai_compatible(
                api_key=LLM_API_KEY,
                api_base=LLM_API_BASE,
                model=LLM_MODEL_NAME,
                messages=messages,
                system=system_prompt,
                max_tokens=max_tokens,
            )

    content = await _with_retry(_call)

    if hashing_kv and args_hash:
        await hashing_kv.upsert({args_hash: {"return": content, "model": cache_key}})
        await hashing_kv.index_done_callback()

    return content


async def get_llm_response(cur_prompt: str, system_content: str) -> str:
    """Async text LLM call — used by fusion helpers."""
    return await model_if_cache(cur_prompt, system_content)


# ============================================================================
# Multimodal/Vision LLM — Provider-branched (Bedrock/Gemini/OpenAI-compatible)
# ============================================================================

async def multimodel_if_cache(
    user_prompt: str,
    img_base: str,
    system_prompt: str,
    history_messages: list[dict] | None = None,
    **kwargs,
) -> str:
    """Vision LLM call with optional KV caching. Supports multiple providers."""
    if history_messages is None:
        history_messages = []

    hashing_kv: BaseKVStorage | None = kwargs.pop("hashing_kv", None)
    max_tokens: int = kwargs.get("max_tokens", 4096)  # Increased from 4096 to 4096 for Gemini thinking tokens in vision

    cache_key = f"{MM_PROVIDER}:{MM_MODEL_NAME}"
    args_hash = None
    if hashing_kv:
        # Hash on text parts only — bytes not easily hashable
        hash_messages = [*history_messages, {"role": "user", "content": user_prompt}]
        args_hash = compute_args_hash(cache_key, hash_messages)
        cached = await hashing_kv.get_by_id(args_hash)
        if cached:
            return cached["return"]

    async def _call():
        if MM_PROVIDER == "bedrock":
            # Bedrock needs base64 decoded to bytes
            img_bytes = base64.b64decode(img_base)
            bedrock_messages = [
                *[
                    {
                        "role": "user",
                        "content": [{"text": m.get("content", "")}]
                    } if m["role"] == "user" else
                    {
                        "role": "assistant",
                        "content": [{"text": m.get("content", "")}]
                    }
                    for m in history_messages
                ],
                {
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
                },
            ]
            client = _get_bedrock_client(is_multimodal=True)
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None,
                lambda: _converse_bedrock(client, BEDROCK_MM_MODEL_ID, bedrock_messages, system_prompt, max_tokens),
            )
        else:  # gemini, openai, or other OpenAI-compatible
            # OpenAI-compatible needs image as data URL
            # Detect image format from base64 data (PNG starts with iVBORw, JPEG with /9j/)
            if img_base.startswith("iVBORw"):
                mime_type = "image/png"
            elif img_base.startswith("/9j/"):
                mime_type = "image/jpeg"
            else:
                mime_type = "image/jpeg"  # Default to JPEG
            
            messages = [
                *history_messages,
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime_type};base64,{img_base}",
                            },
                        },
                        {
                            "type": "text",
                            "text": user_prompt,
                        },
                    ],
                },
            ]
            return await _call_openai_compatible(
                api_key=MM_API_KEY,
                api_base=MM_API_BASE,
                model=MM_MODEL_NAME,
                messages=messages,
                system=system_prompt,
                max_tokens=max_tokens,
                is_vision=True,
            )

    content = await _with_retry(_call)

    if hashing_kv and args_hash:
        await hashing_kv.upsert({args_hash: {"return": content, "model": cache_key}})
        await hashing_kv.index_done_callback()

    return content


async def get_mmllm_response(cur_prompt: str, system_content: str, img_base: str) -> str:
    """Async vision LLM call — used by image_utils helpers."""
    return await multimodel_if_cache(cur_prompt, img_base, system_content)


# ============================================================================
# Embeddings — Provider-branched (Bedrock Titan / Gemini text-embedding-004)
# ============================================================================

def _invoke_titan_embed(texts: list[str]) -> np.ndarray:
    """Synchronous batch embedding call to Bedrock Titan Text V2."""
    client = _get_bedrock_embed_client()
    vectors = []
    for text in texts:
        body = json.dumps({
            "inputText": text[:8000],
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


async def _call_gemini_embed(texts: list[str]) -> np.ndarray:
    """Async embedding call to Gemini via native API (not OpenAI-compatible)."""
    if not EMBEDDING_API_KEY:
        raise ValueError("EMBEDDING_API_KEY not configured")
    
    # Gemini embeddings use native API, not OpenAI-compatible endpoint
    # Format: https://generativelanguage.googleapis.com/v1beta/models/{model}:embedContent
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{EMBEDDING_MODEL_NAME}:embedContent?key={EMBEDDING_API_KEY}"
    
    headers = {
        "Content-Type": "application/json",
    }
    
    vectors = []
    for text in texts:
        # Gemini native API format
        payload = {
            "content": {
                "parts": [{"text": text[:8192]}],  # Gemini embeddings support up to 8192 tokens
            },
            "output_dimensionality": EMBEDDING_DIMENSIONS,
        }
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            # Gemini native format: data["embedding"]["values"]
            vectors.append(data["embedding"]["values"])
    
    return np.array(vectors, dtype=np.float32)


async def embed_texts(texts: list[str]) -> np.ndarray:
    """Provider-branched embedding call. Returns (N, embedding_dim) float32 array."""
    if EMBEDDING_PROVIDER == "bedrock":
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: _invoke_titan_embed(texts))
    elif EMBEDDING_PROVIDER in ("gemini", "openai"):
        return await _call_gemini_embed(texts)
    else:
        raise ValueError(f"Unknown EMBEDDING_PROVIDER: {EMBEDDING_PROVIDER}")


# Dynamically set embedding_dim based on provider
_EMBEDDING_DIM = EMBEDDING_DIMENSIONS if EMBEDDING_PROVIDER == "gemini" else BEDROCK_EMBED_DIMENSIONS
_MAX_TOKEN_SIZE = 2048 if EMBEDDING_PROVIDER == "gemini" else 8000

@wrap_embedding_func_with_attrs(
    embedding_dim=_EMBEDDING_DIM,
    max_token_size=_MAX_TOKEN_SIZE,
)
async def local_embedding(texts: list[str]) -> np.ndarray:
    """Provider-branched embedding function.
    
    Returns:
        (N, embedding_dim) float32 array where embedding_dim depends on EMBEDDING_PROVIDER:
        - Gemini: 768-dim
        - Bedrock Titan: 1024-dim
    """
    return await embed_texts(texts)


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
