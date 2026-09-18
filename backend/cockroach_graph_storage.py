"""
CockroachDB-backed graph storage — drop-in replacement for NetworkXStorage.

Same interface as storage/graph_storage.py's NetworkXStorage, so it can be
swapped in without touching builder.py, text2graph.py, or fusion.py at all.
Those files only ever call the BaseGraphStorage methods below — they don't
know or care whether the graph lives in a local .graphml file or in
CockroachDB rows.

Requires: pip install "psycopg[binary,pool]"
Env var:  COCKROACH_DATABASE_URL="postgresql://user:pass@host:26257/defaultdb?sslmode=verify-full"
          (get this from the CockroachDB Cloud Console, free-tier cluster)

Usage — same call sites as NetworkXStorage, just swap the class:
    self.graph = CockroachGraphStorage(
        namespace="chunk_entity_relation",
        workspace_id=workspace_id,   # NEW — required, scopes every query
    )
"""
import os
from dataclasses import dataclass

import networkx as nx
import psycopg
from psycopg_pool import AsyncConnectionPool

from .storage.graph_storage import BaseGraphStorage

_DSN = os.environ.get("COCKROACH_DATABASE_URL")
_pool: AsyncConnectionPool | None = None


def _get_pool() -> AsyncConnectionPool:
    global _pool
    if _pool is None:
        if not _DSN:
            raise RuntimeError(
                "COCKROACH_DATABASE_URL is not set. Get a free-tier connection "
                "string from the CockroachDB Cloud Console and set it as an "
                "env var before using CockroachGraphStorage."
            )
        _pool = AsyncConnectionPool(_DSN, min_size=1, max_size=10, open=False)
    return _pool


@dataclass
class CockroachGraphStorage(BaseGraphStorage):
    workspace_id: str = None  # required — scopes every row so workspaces never mix

    def __post_init__(self):
        if not self.workspace_id:
            raise ValueError("CockroachGraphStorage requires workspace_id")
        self._dirty: bool = False  # set to True whenever upsert_node/upsert_edge writes

    async def _pool(self):
        pool = _get_pool()
        if pool.closed:  # psycopg_pool lazy-open
            await pool.open()
        return pool

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def has_node(self, node_id: str) -> bool:
        pool = await self._pool()
        async with pool.connection() as conn:
            row = await (await conn.execute(
                "SELECT 1 FROM graph_nodes WHERE workspace_id=%s AND node_id=%s",
                (self.workspace_id, node_id),
            )).fetchone()
            return row is not None

    async def has_edge(self, source_node_id: str, target_node_id: str) -> bool:
        pool = await self._pool()
        async with pool.connection() as conn:
            row = await (await conn.execute(
                "SELECT 1 FROM graph_edges WHERE workspace_id=%s AND source_id=%s AND target_id=%s",
                (self.workspace_id, source_node_id, target_node_id),
            )).fetchone()
            return row is not None

    async def get_node(self, node_id: str) -> dict | None:
        pool = await self._pool()
        async with pool.connection() as conn:
            row = await (await conn.execute(
                "SELECT entity_type, description, source_id FROM graph_nodes "
                "WHERE workspace_id=%s AND node_id=%s",
                (self.workspace_id, node_id),
            )).fetchone()
            if not row:
                return None
            return {"entity_type": row[0], "description": row[1], "source_id": row[2]}

    async def get_edge(self, source_node_id: str, target_node_id: str) -> dict | None:
        pool = await self._pool()
        async with pool.connection() as conn:
            row = await (await conn.execute(
                "SELECT description, weight, source_id FROM graph_edges "
                "WHERE workspace_id=%s AND source_id=%s AND target_id=%s",
                (self.workspace_id, source_node_id, target_node_id),
            )).fetchone()
            if not row:
                return None
            return {
                "description": row[0],
                "weight":      row[1],
                "source_id":   row[2] or "",
                "order":       1,          # order not stored in DB; default to 1
            }

    async def get_nodes_batch(self, node_ids: list[str]) -> dict[str, dict]:
        """Return a dict of {node_id: node_data} for every node_id that exists."""
        if not node_ids:
            return {}
        pool = await self._pool()
        async with pool.connection() as conn:
            rows = await (await conn.execute(
                "SELECT node_id, entity_type, description, source_id FROM graph_nodes "
                "WHERE workspace_id=%s AND node_id = ANY(%s)",
                (self.workspace_id, list(node_ids)),
            )).fetchall()
        return {
            row[0]: {"entity_type": row[1], "description": row[2], "source_id": row[3]}
            for row in rows
        }

    async def get_edges_batch(self, edge_keys: list[tuple[str, str]]) -> dict[tuple[str, str], dict]:
        """Return a dict of {(src, tgt): edge_data} for every edge that exists.

        Fetches all edges for the involved source nodes in one query, then
        filters to the exact (src, tgt) pairs requested.
        """
        if not edge_keys:
            return {}
        src_ids = list({src for src, _ in edge_keys})
        pool = await self._pool()
        async with pool.connection() as conn:
            rows = await (await conn.execute(
                "SELECT source_id, target_id, description, weight, source_id FROM graph_edges "
                "WHERE workspace_id=%s AND source_id = ANY(%s)",
                (self.workspace_id, src_ids),
            )).fetchall()
        edge_set = set(edge_keys)
        return {
            (row[0], row[1]): {
                "description": row[2],
                "weight":      row[3],
                "source_id":   row[4] or "",
                "order":       1,
            }
            for row in rows
            if (row[0], row[1]) in edge_set
        }

    async def upsert_nodes_batch(self, nodes: list[tuple[str, dict]]):
        """Upsert a list of (node_id, node_data) pairs in a single executemany call."""
        if not nodes:
            return
        params = [
            (
                self.workspace_id,
                node_id,
                data.get("entity_type"),
                data.get("description"),
                data.get("source_id"),
            )
            for node_id, data in nodes
        ]
        pool = await self._pool()
        async with pool.connection() as conn:
            await conn.executemany(
                """
                INSERT INTO graph_nodes (workspace_id, node_id, entity_type, description, source_id, updated_at)
                VALUES (%s, %s, %s, %s, %s, now())
                ON CONFLICT (workspace_id, node_id) DO UPDATE SET
                    entity_type = excluded.entity_type,
                    description = excluded.description,
                    source_id   = excluded.source_id,
                    updated_at  = now()
                """,
                params,
            )
        self._dirty = True

    async def upsert_edges_batch(self, edges: list[tuple[str, str, dict]]):
        """Upsert a list of (src, tgt, edge_data) pairs in a single executemany call."""
        if not edges:
            return
        params = [
            (
                self.workspace_id,
                src,
                tgt,
                data.get("description"),
                data.get("weight"),
            )
            for src, tgt, data in edges
        ]
        pool = await self._pool()
        async with pool.connection() as conn:
            await conn.executemany(
                """
                INSERT INTO graph_edges (workspace_id, source_id, target_id, description, weight, updated_at)
                VALUES (%s, %s, %s, %s, %s, now())
                ON CONFLICT (workspace_id, source_id, target_id) DO UPDATE SET
                    description = excluded.description,
                    weight      = excluded.weight,
                    updated_at  = now()
                """,
                params,
            )
        self._dirty = True

    async def get_node_edges(self, source_node_id: str):
        pool = await self._pool()
        async with pool.connection() as conn:
            rows = await (await conn.execute(
                "SELECT source_id, target_id FROM graph_edges "
                "WHERE workspace_id=%s AND (source_id=%s OR target_id=%s)",
                (self.workspace_id, source_node_id, source_node_id),
            )).fetchall()
            return [(r[0], r[1]) for r in rows] or None

    async def node_degree(self, node_id: str) -> int:
        edges = await self.get_node_edges(node_id)
        return len(edges) if edges else 0

    async def edge_degree(self, src_id: str, tgt_id: str) -> int:
        return await self.node_degree(src_id) + await self.node_degree(tgt_id)

    # ------------------------------------------------------------------
    # Writes — this is the actual "dynamic graph" behavior: UPSERT means
    # a second document's entities are added to the existing row set,
    # never overwriting the whole table.
    # ------------------------------------------------------------------

    async def upsert_node(self, node_id: str, node_data: dict[str, str]):
        pool = await self._pool()
        async with pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO graph_nodes (workspace_id, node_id, entity_type, description, source_id, updated_at)
                VALUES (%s, %s, %s, %s, %s, now())
                ON CONFLICT (workspace_id, node_id) DO UPDATE SET
                    entity_type = excluded.entity_type,
                    description = excluded.description,
                    source_id   = excluded.source_id,
                    updated_at  = now()
                """,
                (
                    self.workspace_id,
                    node_id,
                    node_data.get("entity_type"),
                    node_data.get("description"),
                    node_data.get("source_id"),
                ),
            )
        self._dirty = True

    async def upsert_edge(self, source_node_id: str, target_node_id: str, edge_data: dict[str, str]):
        pool = await self._pool()
        async with pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO graph_edges (workspace_id, source_id, target_id, description, weight, updated_at)
                VALUES (%s, %s, %s, %s, %s, now())
                ON CONFLICT (workspace_id, source_id, target_id) DO UPDATE SET
                    description = excluded.description,
                    weight      = excluded.weight,
                    updated_at  = now()
                """,
                (
                    self.workspace_id,
                    source_node_id,
                    target_node_id,
                    edge_data.get("description"),
                    edge_data.get("weight"),
                ),
            )
        self._dirty = True

    async def export_graphml(self, destination: str) -> None:
        """Write this workspace's CockroachDB graph as a NetworkX GraphML snapshot."""
        pool = await self._pool()
        async with pool.connection() as conn:
            node_rows = await (await conn.execute(
                "SELECT node_id, entity_type, description, source_id FROM graph_nodes WHERE workspace_id=%s",
                (self.workspace_id,),
            )).fetchall()
            edge_rows = await (await conn.execute(
                "SELECT source_id, target_id, description, weight FROM graph_edges WHERE workspace_id=%s",
                (self.workspace_id,),
            )).fetchall()

        graph = nx.Graph()
        for node_id, entity_type, description, source_id in node_rows:
            graph.add_node(node_id, entity_type=entity_type or "UNKNOWN", description=description or "", source_id=source_id or "")
        for source_id, target_id, description, weight in edge_rows:
            graph.add_edge(source_id, target_id, description=description or "", weight=float(weight) if weight is not None else 1.0)
        nx.write_graphml(graph, destination)

    async def replace_from_graphml(self, source: str) -> None:
        """Replace this workspace's database graph with a fused GraphML snapshot."""
        graph = nx.read_graphml(source)
        pool = await self._pool()

        node_params = [
            (
                self.workspace_id,
                node_id,
                data.get("entity_type", "UNKNOWN"),
                data.get("description", ""),
                data.get("source_id", ""),
            )
            for node_id, data in graph.nodes(data=True)
        ]
        edge_params = [
            (
                self.workspace_id,
                source_id,
                target_id,
                data.get("description", ""),
                data.get("weight", 1.0),
            )
            for source_id, target_id, data in graph.edges(data=True)
        ]

        async with pool.connection() as conn:
            await conn.execute(
                "DELETE FROM entity_embeddings WHERE workspace_id=%s",
                (self.workspace_id,),
            )
            await conn.execute(
                "DELETE FROM graph_edges WHERE workspace_id=%s",
                (self.workspace_id,),
            )
            await conn.execute(
                "DELETE FROM graph_nodes WHERE workspace_id=%s",
                (self.workspace_id,),
            )
            if node_params:
                await conn.executemany(
                    """
                    INSERT INTO graph_nodes (workspace_id, node_id, entity_type, description, source_id, updated_at)
                    VALUES (%s, %s, %s, %s, %s, now())
                    """,
                    node_params,
                )
            if edge_params:
                await conn.executemany(
                    """
                    INSERT INTO graph_edges (workspace_id, source_id, target_id, description, weight, updated_at)
                    VALUES (%s, %s, %s, %s, %s, now())
                    """,
                    edge_params,
                )

    async def index_done_callback(self):
        # CockroachDB already persisted every upsert individually — nothing batched to flush.
        # Only write a local GraphML snapshot when something actually changed this session,
        # so we don't pay the cost of a full table scan + file write on every save when the
        # graph is unchanged (e.g. all-cache hit on a re-upload).
        if not self._dirty:
            return
        if not self.storage_dir:
            return
        try:
            os.makedirs(self.storage_dir, exist_ok=True)
            await self.export_graphml(
                os.path.join(self.storage_dir, f"graph_{self.namespace}.graphml")
            )
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(
                "CockroachGraphStorage: could not write GraphML snapshot: %s", exc
            )
        finally:
            self._dirty = False
