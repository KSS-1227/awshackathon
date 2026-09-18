"""
Shared graph-construction helpers used by text2graph and img2graph.
"""
from collections import Counter

from ..config import settings as parameter
from ..core.prompt import GRAPH_FIELD_SEP, PROMPTS
from ..llm import model_if_cache
from ..storage.graph_storage import BaseGraphStorage
from ..utils.base import (
    clean_str,
    decode_tokens_by_tiktoken,
    encode_string_by_tiktoken,
    is_float_regex,
    logger,
    split_string_by_multi_markers,
)


async def _handle_single_entity_extraction(
    record_attributes: list[str],
    chunk_key: str,
):
    if len(record_attributes) < 4 or record_attributes[0] != '"entity"':
        return None
    entity_name = clean_str(record_attributes[1].upper())
    if not entity_name.strip():
        return None
    entity_type = clean_str(record_attributes[2].upper())
    entity_description = clean_str(record_attributes[3])
    return {
        "entity_name": entity_name,
        "entity_type": entity_type,
        "description": entity_description,
        "source_id":   chunk_key,
    }


async def _handle_entity_relation_summary(
    entity_or_relation_name: str,
    description: str,
) -> str:
    use_llm_func = model_if_cache
    llm_max_tokens = parameter.SUMMARY_CONTEXT_MAX_TOKENS
    summary_max_tokens = parameter.ENTITY_SUMMARY_MAX_TOKENS
    tokens = encode_string_by_tiktoken(description)
    if len(tokens) < summary_max_tokens:
        return description
    prompt_template = PROMPTS["summarize_entity_descriptions"]
    use_description = decode_tokens_by_tiktoken(tokens[:llm_max_tokens])
    context_base = {
        "entity_name":      entity_or_relation_name,
        "description_list": use_description.split(GRAPH_FIELD_SEP),
    }
    user_prompt = prompt_template.format(**context_base)
    logger.debug(f"Trigger summary: {entity_or_relation_name}")
    return await use_llm_func(user_prompt, max_tokens=summary_max_tokens)


async def _handle_single_relationship_extraction(
    record_attributes: list[str],
    chunk_key: str,
):
    if len(record_attributes) < 5 or record_attributes[0] != '"relationship"':
        return None
    source = clean_str(record_attributes[1].upper())
    target = clean_str(record_attributes[2].upper())
    edge_description = clean_str(record_attributes[3])
    weight = (
        float(record_attributes[-1]) if is_float_regex(record_attributes[-1]) else 1.0
    )
    return {
        "src_id":      source,
        "tgt_id":      target,
        "weight":      weight,
        "description": edge_description,
        "source_id":   chunk_key,
    }


def _supports_batch(graph_inst: BaseGraphStorage) -> bool:
    """True when the storage backend exposes the batch read/write API."""
    return (
        hasattr(graph_inst, "get_nodes_batch")
        and hasattr(graph_inst, "get_edges_batch")
        and hasattr(graph_inst, "upsert_nodes_batch")
        and hasattr(graph_inst, "upsert_edges_batch")
    )


# ---------------------------------------------------------------------------
# Batch path (CockroachGraphStorage) — one SELECT + one executemany per call
# ---------------------------------------------------------------------------

async def _merge_nodes_batch(
    all_nodes: dict[str, list[dict]],
    knowledge_graph_inst: BaseGraphStorage,
) -> list[dict]:
    """
    Merge and upsert all nodes in *all_nodes* using two round-trips:
      1. One batched SELECT to fetch any existing rows.
      2. One executemany INSERT … ON CONFLICT to write all merged rows.

    Returns the list of merged node dicts (same shape as the old per-node return).
    """
    import asyncio

    node_names = list(all_nodes.keys())

    # Single batched read for all prior state
    existing: dict[str, dict] = await knowledge_graph_inst.get_nodes_batch(node_names)

    # Compute merges in memory
    nodes_to_write: list[tuple[str, dict]] = []
    results: list[dict] = []

    async def _compute_one(entity_name: str, nodes_data: list[dict]) -> dict:
        already_node = existing.get(entity_name)
        already_entity_types = []
        already_source_ids = []
        already_description = []
        if already_node is not None:
            already_entity_types.append(already_node["entity_type"])
            already_source_ids.extend(
                split_string_by_multi_markers(already_node["source_id"], [GRAPH_FIELD_SEP])
            )
            already_description.append(already_node["description"])

        entity_type = max(
            Counter(
                [dp["entity_type"] for dp in nodes_data] + already_entity_types
            ).items(),
            key=lambda x: x[1],
        )[0]
        description = GRAPH_FIELD_SEP.join(
            sorted(set([dp["description"] for dp in nodes_data] + already_description))
        )
        source_id = GRAPH_FIELD_SEP.join(
            set([dp["source_id"] for dp in nodes_data] + already_source_ids)
        )
        description = await _handle_entity_relation_summary(entity_name, description)
        return {"entity_type": entity_type, "description": description, "source_id": source_id}

    # LLM summary calls can be parallelised; the rest is pure CPU/memory work
    merged_data_list = await asyncio.gather(*[
        _compute_one(name, data) for name, data in all_nodes.items()
    ])

    for entity_name, merged_data in zip(node_names, merged_data_list):
        nodes_to_write.append((entity_name, merged_data))
        result = dict(merged_data)
        result["entity_name"] = entity_name
        results.append(result)

    await knowledge_graph_inst.upsert_nodes_batch(nodes_to_write)
    return results


async def _merge_edges_batch(
    all_edges: dict[tuple[str, str], list[dict]],
    knowledge_graph_inst: BaseGraphStorage,
) -> None:
    """
    Merge and upsert all edges in *all_edges* using:
      1. One batched SELECT to fetch any existing edge rows.
      2. One batched SELECT to identify missing endpoint nodes.
      3. One executemany to insert any missing endpoint nodes.
      4. One executemany to upsert all merged edges.
    """
    import asyncio

    edge_keys = list(all_edges.keys())

    # Batched read for prior edge state
    existing_edges: dict[tuple[str, str], dict] = await knowledge_graph_inst.get_edges_batch(edge_keys)

    # Compute merges in memory
    edges_to_write: list[tuple[str, str, dict]] = []
    # Collect every node id that appears as an endpoint
    all_endpoint_ids: set[str] = set()
    for src, tgt in edge_keys:
        all_endpoint_ids.add(src)
        all_endpoint_ids.add(tgt)

    # Single batched read to find which endpoints already exist
    existing_nodes: dict[str, dict] = await knowledge_graph_inst.get_nodes_batch(list(all_endpoint_ids))

    async def _compute_one_edge(
        src_id: str, tgt_id: str, edges_data: list[dict]
    ) -> tuple[str, str, dict]:
        already_edge = existing_edges.get((src_id, tgt_id))
        already_weights = []
        already_source_ids = []
        already_description = []
        already_order = []
        if already_edge is not None:
            already_weights.append(already_edge["weight"])
            already_source_ids.extend(
                split_string_by_multi_markers(already_edge["source_id"], [GRAPH_FIELD_SEP])
            )
            already_description.append(already_edge["description"])
            already_order.append(already_edge.get("order", 1))

        order = min([dp.get("order", 1) for dp in edges_data] + already_order)
        weight = sum([dp["weight"] for dp in edges_data] + already_weights)
        description = GRAPH_FIELD_SEP.join(
            sorted(set([dp["description"] for dp in edges_data] + already_description))
        )
        source_id = GRAPH_FIELD_SEP.join(
            set([dp["source_id"] for dp in edges_data] + already_source_ids)
        )
        description = await _handle_entity_relation_summary((src_id, tgt_id), description)
        return src_id, tgt_id, {
            "weight":      weight,
            "description": description,
            "source_id":   source_id,
            "order":       order,
        }

    computed = await asyncio.gather(*[
        _compute_one_edge(src, tgt, data) for (src, tgt), data in all_edges.items()
    ])

    # Insert any missing endpoint nodes in one batch
    missing_nodes: list[tuple[str, dict]] = []
    # Use the merged source/description from the first edge that references each missing node
    endpoint_stub_data: dict[str, dict] = {}
    for src_id, tgt_id, edge_data in computed:
        for nid in (src_id, tgt_id):
            if nid not in existing_nodes and nid not in endpoint_stub_data:
                endpoint_stub_data[nid] = {
                    "source_id":   edge_data["source_id"],
                    "description": edge_data["description"],
                    "entity_type": '"UNKNOWN"',
                }
    for nid, stub in endpoint_stub_data.items():
        missing_nodes.append((nid, stub))

    if missing_nodes:
        await knowledge_graph_inst.upsert_nodes_batch(missing_nodes)

    edges_to_write = [(src, tgt, edata) for src, tgt, edata in computed]
    await knowledge_graph_inst.upsert_edges_batch(edges_to_write)


# ---------------------------------------------------------------------------
# Scalar path (NetworkXStorage / any storage without batch API)
# Kept for backward-compat with img2graph which still uses NetworkXStorage.
# ---------------------------------------------------------------------------

async def _merge_nodes_then_upsert(
    entity_name: str,
    nodes_data: list[dict],
    knwoledge_graph_inst: BaseGraphStorage,
):
    already_entitiy_types = []
    already_source_ids = []
    already_description = []
    already_node = await knwoledge_graph_inst.get_node(entity_name)
    if already_node is not None:
        already_entitiy_types.append(already_node["entity_type"])
        already_source_ids.extend(
            split_string_by_multi_markers(already_node["source_id"], [GRAPH_FIELD_SEP])
        )
        already_description.append(already_node["description"])
    entity_type = max(
        Counter(
            [dp["entity_type"] for dp in nodes_data] + already_entitiy_types
        ).items(),
        key=lambda x: x[1],
    )[0]
    description = GRAPH_FIELD_SEP.join(
        sorted(set([dp["description"] for dp in nodes_data] + already_description))
    )
    source_id = GRAPH_FIELD_SEP.join(
        set([dp["source_id"] for dp in nodes_data] + already_source_ids)
    )
    description = await _handle_entity_relation_summary(entity_name, description)
    node_data = {"entity_type": entity_type, "description": description, "source_id": source_id}
    await knwoledge_graph_inst.upsert_node(entity_name, node_data=node_data)
    node_data["entity_name"] = entity_name
    return node_data


async def _merge_edges_then_upsert(
    src_id: str,
    tgt_id: str,
    edges_data: list[dict],
    knwoledge_graph_inst: BaseGraphStorage,
):
    already_weights = []
    already_source_ids = []
    already_description = []
    already_order = []
    if await knwoledge_graph_inst.has_edge(src_id, tgt_id):
        already_edge = await knwoledge_graph_inst.get_edge(src_id, tgt_id)
        already_weights.append(already_edge["weight"])
        already_source_ids.extend(
            split_string_by_multi_markers(already_edge["source_id"], [GRAPH_FIELD_SEP])
        )
        already_description.append(already_edge["description"])
        already_order.append(already_edge.get("order", 1))
    order = min([dp.get("order", 1) for dp in edges_data] + already_order)
    weight = sum([dp["weight"] for dp in edges_data] + already_weights)
    description = GRAPH_FIELD_SEP.join(
        sorted(set([dp["description"] for dp in edges_data] + already_description))
    )
    source_id = GRAPH_FIELD_SEP.join(
        set([dp["source_id"] for dp in edges_data] + already_source_ids)
    )
    for need_insert_id in [src_id, tgt_id]:
        if not (await knwoledge_graph_inst.has_node(need_insert_id)):
            await knwoledge_graph_inst.upsert_node(
                need_insert_id,
                node_data={
                    "source_id": source_id,
                    "description": description,
                    "entity_type": '"UNKNOWN"',
                },
            )
    description = await _handle_entity_relation_summary((src_id, tgt_id), description)
    await knwoledge_graph_inst.upsert_edge(
        src_id,
        tgt_id,
        edge_data={
            "weight": weight,
            "description": description,
            "source_id": source_id,
            "order": order,
        },
    )
