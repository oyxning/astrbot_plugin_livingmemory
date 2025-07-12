# -*- coding: utf-8 -*-
"""
recall_engine.py - 回忆引擎
负责根据用户查询，使用多策略智能召回最相关的记忆。
"""

import json
import math
from typing import List, Dict, Any, Optional

from astrbot.api import logger
from astrbot.api.star import Context
from ...storage.faiss_manager import FaissManager, Result
from ..utils import get_now_datetime


class RecallEngine:
    """
    回忆引擎：负责根据用户查询，使用多策略智能召回最相关的记忆。
    """

    def __init__(self, config: Dict[str, Any], faiss_manager: FaissManager):
        """
        初始化回忆引擎。

        Args:
            config (Dict[str, Any]): 插件配置中 'recall_engine' 部分的字典。
            faiss_manager (FaissManager): 数据库管理器实例。
        """
        self.config = config
        self.faiss_manager = faiss_manager
        logger.info("RecallEngine 初始化成功。")

    async def recall(
        self,
        context: Context,
        query: str,
        session_id: Optional[str] = None,
        persona_id: Optional[str] = None,
        k: Optional[int] = None,
    ) -> List[Result]:
        """
        执行回忆流程，检索并可能重排记忆。

        Args:
            query (str): 用户查询文本。
            session_id (Optional[str], optional): 当前会话 ID. Defaults to None.
            persona_id (Optional[str], optional): 当前人格 ID. Defaults to None.
            k (Optional[int], optional): 希望返回的记忆数量，如果为 None 则从配置中读取.

        Returns:
            List[Result]: 最终返回给上层应用的记忆列表。
        """
        top_k = k if k is not None else self.config.get("top_k", 5)

        # 1. 基础检索
        initial_results = await self.faiss_manager.search_memory(
            query=query, k=top_k, session_id=session_id, persona_id=persona_id
        )

        if not initial_results:
            return []

        # 2. 多维度重排
        strategy = self.config.get("recall_strategy", "weighted")
        if strategy == "weighted":
            logger.debug("使用 'weighted' 策略进行重排...")
            return self._rerank_by_weighted_score(context, initial_results)
        else:  # strategy == "similarity"
            logger.debug("使用 'similarity' 策略，直接返回结果。")
            return initial_results

    def _rerank_by_weighted_score(
        self, context: Context, results: List[Result]
    ) -> List[Result]:
        """
        根据相似度、重要性和新近度对结果进行加权重排。
        """
        sim_w = self.config.get("similarity_weight", 0.6)
        imp_w = self.config.get("importance_weight", 0.2)
        rec_w = self.config.get("recency_weight", 0.2)

        reranked_results = []
        current_time = get_now_datetime(context).timestamp()

        for res in results:
            metadata = res.data.get("metadata", {})
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except json.JSONDecodeError:
                    metadata = {}

            # 归一化各项得分 (0-1)
            similarity_score = res.similarity
            importance_score = metadata.get("importance", 0.0)

            # 计算新近度得分
            last_access = metadata.get("last_access_time", current_time)
            # 增加健壮性检查，以防 last_access 是字符串
            if isinstance(last_access, str):
                try:
                    last_access = float(last_access)
                except (ValueError, TypeError):
                    last_access = current_time

            hours_since_access = (current_time - last_access) / 3600
            # 使用指数衰减，半衰期约为24小时
            recency_score = math.exp(-0.028 * hours_since_access)

            # 计算最终加权分
            final_score = (
                similarity_score * sim_w
                + importance_score * imp_w
                + recency_score * rec_w
            )

            # 直接修改现有 Result 对象的 similarity 分数
            res.similarity = final_score
            reranked_results.append(res)

        # 按最终得分降序排序
        reranked_results.sort(key=lambda x: x.similarity, reverse=True)

        return reranked_results
