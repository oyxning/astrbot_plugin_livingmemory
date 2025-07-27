# -*- coding: utf-8 -*-
import aiosqlite
from typing import List, Dict, Any


class GraphStorageSQLite:
    """
    使用 SQLite 关系表来管理知识图谱数据。
    """

    def __init__(self, connection: aiosqlite.Connection):
        """
        直接接收一个已建立的 aiosqlite 连接，以确保所有操作都在同一事务空间内。
        """
        self.conn = connection

    async def initialize_schema(self):
        """在数据库中创建图相关的表和索引。"""
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS graph_nodes (
                entity_id TEXT PRIMARY KEY NOT NULL,
                name TEXT NOT NULL,
                type TEXT NOT NULL
            )
        """)
        await self.conn.execute("""
            CREATE TABLE IF NOT EXISTS graph_edges (
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                relation_type TEXT NOT NULL,
                memory_internal_id INTEGER NOT NULL,
                FOREIGN KEY (memory_internal_id) REFERENCES memories(id) ON DELETE CASCADE
            )
        """)
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_edge_source ON graph_edges (source_id)"
        )
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_edge_target ON graph_edges (target_id)"
        )
        await self.conn.commit()

    async def add_memory_graph(self, internal_id: int, payload: Dict[str, Any]):
        """从 knowledge_graph_payload 创建节点和边。"""
        # 使用事务确保要么全部成功，要么全部失败
        async with self.conn.cursor() as cursor:
            # 1. 添加或更新节点 (INSERT OR IGNORE 避免重复)
            nodes_to_insert = []
            if "event_entity" in payload and payload["event_entity"]:
                nodes_to_insert.append(
                    (
                        payload["event_entity"]["event_id"],
                        payload["event_entity"].get(
                            "event_type", "Event"
                        ),  # name can be event_type
                        "Event",
                    )
                )
            for entity in payload.get("entities", []):
                nodes_to_insert.append(
                    (entity["entity_id"], entity["name"], entity["type"])
                )

            if nodes_to_insert:
                await cursor.executemany(
                    "INSERT OR IGNORE INTO graph_nodes (entity_id, name, type) VALUES (?, ?, ?)",
                    nodes_to_insert,
                )

            # 2. 添加关系边
            edges_to_insert = []
            for rel in payload.get("relationships", []):
                if len(rel) == 3:
                    edges_to_insert.append((rel[0], rel[1], rel[2], internal_id))

            if edges_to_insert:
                await cursor.executemany(
                    "INSERT INTO graph_edges (source_id, relation_type, target_id, memory_internal_id) VALUES (?, ?, ?, ?)",
                    edges_to_insert,
                )
        await self.conn.commit()

    async def find_related_memory_ids(
        self, entity_id: str, max_depth: int = 2
    ) -> List[int]:
        """
        使用 SQL 递归查询 (Recursive CTE) 从一个实体出发，查找相关联的记忆 internal_id。
        """
        query = """
        WITH RECURSIVE graph_walk(source, target, depth, mem_id) AS (
            SELECT source_id, target_id, 1, memory_internal_id FROM graph_edges WHERE source_id = :entity_id
            UNION ALL
            SELECT source_id, target_id, 1, memory_internal_id FROM graph_edges WHERE target_id = :entity_id
            UNION
            SELECT e.source_id, e.target_id, w.depth + 1, e.memory_internal_id
            FROM graph_edges e
            JOIN graph_walk w ON (e.source_id = w.target OR e.target_id = w.source)
            WHERE w.depth < :max_depth
        )
        SELECT DISTINCT mem_id FROM graph_walk;
        """
        async with self.conn.execute(
            query, {"entity_id": entity_id, "max_depth": max_depth}
        ) as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]

    async def add_correction_link(
        self, new_event_id: str, old_event_id: str, memory_internal_id: int
    ):
        """为记忆更新添加 CORRECTS 关系。"""
        await self.conn.execute(
            "INSERT INTO graph_edges (source_id, relation_type, target_id, memory_internal_id) VALUES (?, 'CORRECTS', ?, ?)",
            (new_event_id, old_event_id, memory_internal_id),
        )
        await self.conn.commit()
