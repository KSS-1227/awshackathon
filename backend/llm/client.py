"""
LLM and multimodal LLM client — async/sync wrappers with KV caching.
AWS Bedrock backend using Claude Haiku 4.5 for both text and vision.

Replaces the OpenAI AsyncOpenAI client that was in the initial commit.
Interface is identical to the OpenAI version so all callers
(model_if_cache, multimodel_if_cache, get_llm_response, get_mmllm_response)
are unchanged — only the transport layer differs.

Bedrock boto3 is synchronous; async is achieved via asyncio.run_in_executor
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
    BEDROCK_MM_MODEL_ID,
    BEDROCK_TEXT_MODEL_ID,
    AWS_REGION,
    get_embed_model,
)
from ..storage.kv_storage import BaseKVStorage
from ..utils.base import compute_args_hash, logger, wrap_embedding_func_with_attrs

# ============================================================================
# Singleton Bedrock client pool
# ============================================================================

_CLIENTS: dict[str, Any] = {
    "text": None,
    "mm": None,
}


def _get_bedrock_client(is_multimodal: bool = False):
    """Return singleton boto3 bedrock-runtime client (synchronous).

    Bedrock boto3 is sync-only; callers wrap with run_in_executor.
    Text and multimodal share the same endpoint — both keys point to the
    same underlying client; the dict keeps the door open for future
    per-model endpoint routing.
    """
    key = "mm" if is_multimodal else "text"
    if _CLIENTS[key] is None:
        _CLIENTS[key] = boto3.client(
            "bedrock-runtime",
            region_name=AWS_REGION,
        )
        logger.info(
            "✓ Bedrock client initialised (region=%s, model=%s)",
            AWS_REGION,
            BEDROCK_MM_MODEL_ID if is_multimodal else BEDROCK_TEXT_MODEL_ID,
        )
    return _CLIENTS[key]


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
# Shared Bedrock invoke helper
# ============================================================================

def _invoke_bedrock(client, model_id: str, request_body: dict) -> str:
    """Synchronous Bedrock invoke_model call. Runs inside run_in_executor."""
    response = client.invoke_model(
        modelId=model_id,
        body=json.dumps(request_body),
    )
    body = json.loads(response["body"].read())
    return body["content"][0]["text"]


# ============================================================================
# Embedding (unchanged — local sentence-transformers, not Bedrock)
# ============================================================================

def _embedding_dim() -> int:
    model = get_embed_model()
    if hasattr(model, "get_embedding_dimension"):
        return model.get_embedding_dimension()
    return model.get_sentence_embedding_dimension()


def _embedding_max_seq() -> int:
    return get_embed_model().max_seq_length


@wrap_embedding_func_with_attrs(embedding_dim=-1, max_token_size=-1)
async def local_embedding(texts: list[str]) -> np.ndarray:
    if local_embedding.embedding_dim == -1:
        local_embedding.embedding_dim = _embedding_dim()
        local_embedding.max_token_size = _embedding_max_seq()
    return get_embed_model().encode(texts)


# ============================================================================
# Text LLM — Claude Haiku 4.5 via Bedrock
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

    messages = [*history_messages, {"role": "user", "content": prompt}]

    args_hash = None
    if hashing_kv:
        args_hash = compute_args_hash(BEDROCK_TEXT_MODEL_ID, messages)
        cached = await hashing_kv.get_by_id(args_hash)
        if cached:
            return cached["return"]

    request_body: dict = {
        "anthropic_version": "bedrock-2023-06-01",
        "max_tokens": kwargs.get("max_tokens", 4096),
        "messages": messages,
    }
    if system_prompt:
        request_body["system"] = system_prompt

    async def _call():
        client = _get_bedrock_client(is_multimodal=False)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, lambda: _invoke_bedrock(client, BEDROCK_TEXT_MODEL_ID, request_body)
        )

    content = await _with_retry(_call)

    if hashing_kv and args_hash:
        await hashing_kv.upsert({args_hash: {"return": content, "model": BEDROCK_TEXT_MODEL_ID}})
        await hashing_kv.index_done_callback()

    return content


async def get_llm_response(cur_prompt: str, system_content: str) -> str:
    """Async text LLM call — used by fusion helpers."""
    request_body = {
        "anthropic_version": "bedrock-2023-06-01",
        "max_tokens": 4096,
        "system": system_content,
        "messages": [{"role": "user", "content": cur_prompt}],
    }

    async def _call():
        client = _get_bedrock_client(is_multimodal=False)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, lambda: _invoke_bedrock(client, BEDROCK_TEXT_MODEL_ID, request_body)
        )

    return await _with_retry(_call)


# ============================================================================
# Multimodal LLM — Claude Haiku 4.5 vision via Bedrock
# ============================================================================

async def multimodel_if_cache(
    user_prompt: str,
    img_base: str,
    system_prompt: str,
    history_messages: list[dict] | None = None,
    **kwargs,
) -> str:
    """Vision LLM call with optional KV caching. Identical interface to OpenAI version.

    img_base must be a base64-encoded JPEG string (no data-URI prefix).
    This is what img2graph.py's _encode_image_base64() produces.
    """
    if history_messages is None:
        history_messages = []

    hashing_kv: BaseKVStorage | None = kwargs.pop("hashing_kv", None)

    # Bedrock Claude vision message format
    user_message = {
        "role": "user",
        "content": [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": img_base,
                },
            },
            {"type": "text", "text": user_prompt},
        ],
    }
    messages = [*history_messages, user_message]

    args_hash = None
    if hashing_kv:
        args_hash = compute_args_hash(BEDROCK_MM_MODEL_ID, messages)
        cached = await hashing_kv.get_by_id(args_hash)
        if cached:
            return cached["return"]

    request_body: dict = {
        "anthropic_version": "bedrock-2023-06-01",
        "max_tokens": kwargs.get("max_tokens", 4096),
        "system": system_prompt,
        "messages": messages,
    }

    async def _call():
        client = _get_bedrock_client(is_multimodal=True)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, lambda: _invoke_bedrock(client, BEDROCK_MM_MODEL_ID, request_body)
        )

    content = await _with_retry(_call)

    if hashing_kv and args_hash:
        await hashing_kv.upsert({args_hash: {"return": content, "model": BEDROCK_MM_MODEL_ID}})
        await hashing_kv.index_done_callback()

    return content


async def get_mmllm_response(cur_prompt: str, system_content: str, img_base: str) -> str:
    """Async vision LLM call — used by image_utils helpers."""
    user_message = {
        "role": "user",
        "content": [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": img_base,
                },
            },
            {"type": "text", "text": cur_prompt},
        ],
    }
    request_body = {
        "anthropic_version": "bedrock-2023-06-01",
        "max_tokens": 4096,
        "system": system_content,
        "messages": [user_message],
    }

    async def _call():
        client = _get_bedrock_client(is_multimodal=True)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, lambda: _invoke_bedrock(client, BEDROCK_MM_MODEL_ID, request_body)
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
