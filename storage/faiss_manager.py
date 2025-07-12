# -*- coding: utf-8 -*-
"""
FaissManager - 高级数据库管理器
封装 FaissVecDB，提供支持动态记忆生命周期的接口。
"""

import time
import json
from typing import List, Dict, Any, Optional
import numpy as np

from astrbot.core.db.vec_db.faiss_impl.vec_db import FaissVecDB, Result


class FaissManager:
    """
    一个高级管理器，封装了 FaissVecDB 的操作，并增加了对动态记忆生命周期的支持，
    包括重要性、新近度和访问时间的管理。
    """

    def __init__(self, db: FaissVecDB):
        """
        初始化 FaissManager。

        Args:
            db (FaissVecDB): 已经实例化的 FaissVecDB 对象。
        """
        self.db = db

    async def add_memory(
        self,
        content: str,
        importance: float,
        session_id: str,
        persona_id: Optional[str] = None,
        extra_meta: Optional[Dict[str, Any]] = None,
    ) -> int:
        """
        添加一条新的记忆到数据库。

        Args:
            content (str): 记忆的文本内容。
            importance (float): 记忆的重要性评分 (0.0 to 1.0)。
            session_id (str): 当前的会话 ID。
            persona_id (Optional[str], optional): 当前的人格 ID. Defaults to None.
            extra_meta (Optional[Dict[str, Any]], optional): 其他额外的元数据. Defaults to None.

        Returns:
            int: 插入的记忆在数据库中的主键 ID。
        """
        current_timestamp = int(time.time())
        metadata = {
            "importance": importance,
            "create_time": current_timestamp,
            "last_access_time": current_timestamp,
            "session_id": session_id,
            "persona_id": persona_id,
        }
        if extra_meta:
            metadata.update(extra_meta)

        inserted_id = await self.db.insert(content=content, metadata=metadata)
        return inserted_id

    async def search_memory(
        self,
        query: str,
        k: int,
        session_id: Optional[str] = None,
        persona_id: Optional[str] = None,
    ) -> List[Result]:
        """
        根据查询文本智能检索最相关的记忆。

        Args:
            query (str): 查询文本。
            k (int): 希望返回的记忆数量。
            session_id (Optional[str], optional): 会话 ID 过滤器. Defaults to None.
            persona_id (Optional[str], optional): 人格 ID 过滤器. Defaults to None.

        Returns:
            List[Result]: 检索到的记忆列表。
        """
        metadata_filters = {}
        if session_id:
            metadata_filters["session_id"] = session_id
        if persona_id:
            metadata_filters["persona_id"] = persona_id

        # 从数据库检索，fetch_k 可以设置得比 k 大，以便有更多结果用于后续处理
        results = await self.db.retrieve(
            query=query, k=k, fetch_k=k * 2, metadata_filters=metadata_filters
        )

        if results:
            # 更新被访问记忆的 last_access_time
            accessed_ids = [res.data["id"] for res in results]
            await self.update_memory_access_time(accessed_ids)

        return results

    async def update_memory_access_time(self, doc_ids: List[int]):
        """
        批量更新一组记忆的最后访问时间。

        Args:
            doc_ids (List[int]): 需要更新的文档 ID 列表。
        """
        if not doc_ids:
            return

        current_timestamp = int(time.time())

        # 从数据库中获取现有的元数据
        docs = await self.db.document_storage.get_documents(
            ids=doc_ids, metadata_filters={}
        )

        for doc in docs:
            try:
                metadata = (
                    json.loads(doc["metadata"])
                    if isinstance(doc["metadata"], str)
                    else doc["metadata"]
                )
                metadata["last_access_time"] = current_timestamp

                # 更新数据库
                await self.db.document_storage.connection.execute(
                    "UPDATE documents SET metadata = ? WHERE id = ?",
                    (json.dumps(metadata), doc["id"]),
                )
            except (json.JSONDecodeError, KeyError) as e:
                # 记录日志或处理错误
                print(f"Error updating metadata for doc_id {doc['id']}: {e}")
                continue

        await self.db.document_storage.connection.commit()

    async def get_all_memories_for_forgetting(
        self, batch_size: int = 100
    ) -> List[Dict[str, Any]]:
        """
        获取所有记忆及其元数据，用于遗忘代理的处理。
        使用分页以避免一次性加载过多数据。

        Args:
            batch_size (int): 每次从数据库获取的记录数。

        Returns:
            List[Dict[str, Any]]: 包含所有记忆数据的列表。
        """
        all_docs = []
        offset = 0
        while True:
            docs = await self.db.document_storage.get_all_documents(
                limit=batch_size, offset=offset
            )
            if not docs:
                break
            all_docs.extend(docs)
            offset += batch_size
        return all_docs

    async def update_memories_metadata(self, memories: List[Dict[str, Any]]):
        """
        批量更新记忆的元数据。
        """
        if not memories:
            return

        async with self.db.document_storage.connection.cursor() as cursor:
            for mem in memories:
                await cursor.execute(
                    "UPDATE documents SET metadata = ? WHERE id = ?",
                    (json.dumps(mem["metadata"]), mem["id"]),
                )
        await self.db.document_storage.connection.commit()

    async def delete_memories(self, doc_ids: List[int]):
        """
        批量删除一组记忆。

        Args:
            doc_ids (List[int]): 需要删除的文档 ID 列表。
        """
        if not doc_ids:
            return

        # 从 Faiss 中删除
        self.db.embedding_storage.index.remove_ids(np.array(doc_ids))
        await self.db.embedding_storage.save_index()

        # 从 SQLite 中删除
        # TODO 没有实现，先不管
        # await self.db.document_storage.delete_documents(doc_ids)
