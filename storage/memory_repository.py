# -*- coding: utf-8 -*-
"""
memory_repository.py - 统一封装 Faiss 文档存储，供 WebUI 与后台使用。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from astrbot.api import logger


class MemoryRepository:
    """
    基于 FaissVecDB 的统一记忆访问仓库，提供分页、统计、检索等能力。
    """

    def __init__(self, faiss_manager):
        self._faiss_manager = faiss_manager
        self._connection = faiss_manager.db.document_storage.connection

    # ------------------------------------------------------------------
    # 公共查询接口
    # ------------------------------------------------------------------

    async def count_memories(
        self,
        status: Optional[str] = None,
        keyword: Optional[str] = None,
    ) -> int:
        where_sql, params = self._build_filters(status, keyword)
        sql = "SELECT COUNT(*) FROM documents"
        if where_sql:
            sql += f" WHERE {where_sql}"

        async with self._connection.execute(sql, params) as cursor:
            row = await cursor.fetchone()
            return int(row[0]) if row and row[0] is not None else 0

    async def list_memories(
        self,
        limit: int,
        offset: int = 0,
        status: Optional[str] = None,
        keyword: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        where_sql, params = self._build_filters(status, keyword)
        sql = """
            SELECT id, doc_id, text, metadata, created_at, updated_at
            FROM documents
        """
        if where_sql:
            sql += f" WHERE {where_sql}"
        sql += " ORDER BY datetime(created_at) DESC, id DESC"

        if limit > 0:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])

        async with self._connection.execute(sql, params) as cursor:
            rows = await cursor.fetchall()

        return [self._row_to_record(row) for row in rows]

    async def get_memory(self, identifier: str) -> Optional[Dict[str, Any]]:
        """
        根据多种标识符获取记忆：
        - 主键 id（整型）
        - doc_id（Faiss 文档 UUID）
        - metadata.memory_id（记忆唯一标识）
        """
        # 尝试按照主键整数查询
        try:
            record = await self._get_by_pk(int(identifier))
            if record:
                return record
        except ValueError:
            pass

        # doc_id
        record = await self._get_by_doc_uuid(identifier)
        if record:
            return record

        # metadata.memory_id
        return await self._get_by_memory_uuid(identifier)

    async def count_by_status(self) -> Dict[str, int]:
        sql = """
            SELECT COALESCE(json_extract(metadata, '$.status'), 'active') AS status,
                   COUNT(*) AS count
            FROM documents
            GROUP BY status
        """
        async with self._connection.execute(sql) as cursor:
            rows = await cursor.fetchall()

        result = {"active": 0, "archived": 0, "deleted": 0}
        for row in rows:
            status, count = row[0] or "active", int(row[1])
            result[status] = count
        return result

    # ------------------------------------------------------------------
    # 内部查询帮助
    # ------------------------------------------------------------------

    def _build_filters(
        self, status: Optional[str], keyword: Optional[str]
    ) -> Tuple[str, List[Any]]:
        conditions: List[str] = []
        params: List[Any] = []

        if status and status != "all":
            conditions.append(
                "COALESCE(json_extract(metadata, '$.status'), 'active') = ?"
            )
            params.append(status)

        if keyword:
            like = f"%{keyword}%"
            conditions.append(
                "(text LIKE ? OR json_extract(metadata, '$.memory_content') LIKE ? "
                "OR doc_id LIKE ? OR json_extract(metadata, '$.memory_id') LIKE ?)"
            )
            params.extend([like, like, like, like])

        where_sql = " AND ".join(conditions)
        return where_sql, params

    async def _get_by_pk(self, pk: int) -> Optional[Dict[str, Any]]:
        async with self._connection.execute(
            """
            SELECT id, doc_id, text, metadata, created_at, updated_at
            FROM documents
            WHERE id = ?
            """,
            (pk,),
        ) as cursor:
            row = await cursor.fetchone()
        return self._row_to_record(row) if row else None

    async def _get_by_doc_uuid(self, doc_uuid: str) -> Optional[Dict[str, Any]]:
        async with self._connection.execute(
            """
            SELECT id, doc_id, text, metadata, created_at, updated_at
            FROM documents
            WHERE doc_id = ?
            """,
            (doc_uuid,),
        ) as cursor:
            row = await cursor.fetchone()
        return self._row_to_record(row) if row else None

    async def _get_by_memory_uuid(self, memory_uuid: str) -> Optional[Dict[str, Any]]:
        async with self._connection.execute(
            """
            SELECT id, doc_id, text, metadata, created_at, updated_at
            FROM documents
            WHERE json_extract(metadata, '$.memory_id') = ?
            """,
            (memory_uuid,),
        ) as cursor:
            row = await cursor.fetchone()
        return self._row_to_record(row) if row else None

    @staticmethod
    def _row_to_record(row) -> Dict[str, Any]:
        if not row:
            return {}
        metadata_raw = row[3] or "{}"
        try:
            metadata = json.loads(metadata_raw)
        except json.JSONDecodeError:
            logger.warning("解析记忆元数据失败，使用空字典替代")
            metadata = {}

        return {
            "id": row[0],
            "doc_uuid": row[1],
            "content": row[2],
            "metadata": metadata,
            "metadata_raw": metadata_raw,
            "created_at": row[4],
            "updated_at": row[5],
        }

