# -*- coding: utf-8 -*-
"""
logic.py - 插件的核心业务逻辑引擎
包含 ReflectionEngine, RecallEngine, 和 ForgettingAgent 的实现。
"""

import asyncio
import time
import math
import json
from typing import List, Dict, Any, Optional

from astrbot.api import logger
from astrbot.api.provider import Provider
from ..storage.faiss_manager import FaissManager, Result


class ReflectionEngine:
    """
    反思引擎：负责对会话历史进行反思，生成、评估并存储高质量的记忆。
    """

    def __init__(
        self,
        config: Dict[str, Any],
        llm_provider: Provider,
        faiss_manager: FaissManager,
    ):
        """
        初始化反思引擎。

        Args:
            config (Dict[str, Any]): 插件配置中 'reflection_engine' 部分的字典。
            llm_provider (Provider): 用于总结和评估的 LLM Provider。
            faiss_manager (FaissManager): 数据库管理器实例。
        """
        self.config = config
        self.llm_provider = llm_provider
        self.faiss_manager = faiss_manager
        logger.info("ReflectionEngine 初始化成功。")

    async def reflect_and_store(
        self,
        conversation_history: List[Dict[str, str]],
        session_id: str,
        persona_id: Optional[str] = None,
        persona_prompt: Optional[str] = None,
    ):
        """
        执行完整的反思、评估和存储流程。
        这是一个后台任务，不应阻塞主流程。
        """
        try:
            # 1. 格式化历史记录用于总结
            history_text = self._format_history_for_summary(conversation_history)
            if not history_text:
                logger.debug("对话历史为空，跳过反思。")
                return

            # 2. 请求 LLM 进行总结
            logger.info(f"[{session_id}] 开始总结对话历史...")
            summary_prompt = self.config.get("summary_prompt", "")

            # 构建带有可选人格的 system prompt
            if persona_prompt:
                summary_prompt += f"请注意，你需要代入以下人格进行总结。注意人格内容不是记忆内容，不能混淆在记忆中。\n人格设定如下：\n<persona>{persona_prompt}</persona>"

            summary_response = await self.llm_provider.text_chat(
                prompt=history_text,
                system_prompt=summary_prompt,
            )
            summary_text = summary_response.completion_text.strip()
            if not summary_text:
                logger.warning(f"[{session_id}] LLM 总结返回为空，任务中止。")
                return
            logger.info(f"[{session_id}] 对话总结完成，长度: {len(summary_text)}。")
            logger.debug(f"[{session_id}] 总结内容: {summary_text}")

            # 3. 请求 LLM 评估重要性
            logger.info(f"[{session_id}] 开始评估记忆重要性...")
            evaluation_prompt_template = self.config.get("evaluation_prompt", "")
            evaluation_prompt = evaluation_prompt_template.format(
                memory_content=summary_text
            )
            if persona_prompt:
                evaluation_prompt += f"\n请注意，你需要代入以下人格进行评估。\n人格设定如下：\n<persona>{persona_prompt}</persona>"

            evaluation_response = await self.llm_provider.text_chat(
                prompt=evaluation_prompt
            )

            try:
                importance_score = float(evaluation_response.completion_text.strip())
            except (ValueError, TypeError):
                logger.warning(
                    f"[{session_id}] LLM 重要性评估返回格式错误: '{evaluation_response.completion_text}'，将使用默认值 0.0。"
                )
                importance_score = 0.0
            logger.info(
                f"[{session_id}] 记忆重要性评估完成，得分: {importance_score:.2f}。"
            )

            # 4. 根据阈值决定是否存储
            threshold = self.config.get("importance_threshold", 0.5)
            if importance_score >= threshold:
                await self.faiss_manager.add_memory(
                    content=summary_text,
                    importance=importance_score,
                    session_id=session_id,
                    persona_id=persona_id,
                )
                logger.info(
                    f"[{session_id}] 记忆得分高于阈值 {threshold}，已成功存入数据库。"
                )
            else:
                logger.info(f"[{session_id}] 记忆得分低于阈值 {threshold}，已被忽略。")

        except Exception as e:
            logger.error(
                f"[{session_id}] 在执行反思与存储任务时发生严重错误: {e}", exc_info=True
            )

    def _format_history_for_summary(self, history: List[Dict[str, str]]) -> str:
        """
        将对话历史列表格式化为单个字符串。

        Args:
            history (List[Dict[str, str]]): 对话历史。

        Returns:
            str: 格式化后的字符串。
        """
        if not history:
            return ""

        # 过滤掉非 user 和 assistant 的角色
        filtered_history = [
            msg for msg in history if msg.get("role") in ["user", "assistant"]
        ]

        return "\n".join(
            [f"{msg['role']}: {msg['content']}" for msg in filtered_history]
        )


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
            return self._rerank_by_weighted_score(initial_results)
        else:  # strategy == "similarity"
            logger.debug("使用 'similarity' 策略，直接返回结果。")
            return initial_results

    def _rerank_by_weighted_score(self, results: List[Result]) -> List[Result]:
        """
        根据相似度、重要性和新近度对结果进行加权重排。
        """
        sim_w = self.config.get("similarity_weight", 0.6)
        imp_w = self.config.get("importance_weight", 0.2)
        rec_w = self.config.get("recency_weight", 0.2)

        reranked_results = []
        current_time = time.time()

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
            hours_since_access = (current_time - last_access) / 3600
            # 使用指数衰减，半衰期约为24小时
            recency_score = math.exp(-0.028 * hours_since_access)

            # 计算最终加权分
            final_score = (
                similarity_score * sim_w
                + importance_score * imp_w
                + recency_score * rec_w
            )

            # 注意：原始的 similarity 分数被替换为我们的加权分数
            reranked_results.append(Result(similarity=final_score, data=res.data))

        # 按最终得分降序排序
        reranked_results.sort(key=lambda x: x.similarity, reverse=True)

        return reranked_results


class ForgettingAgent:
    """
    遗忘代理：作为一个后台任务，定期清理陈旧的、不重要的记忆，模拟人类的遗忘曲线。
    """

    def __init__(self, config: Dict[str, Any], faiss_manager: FaissManager):
        """
        初始化遗忘代理。

        Args:
            config (Dict[str, Any]): 插件配置中 'forgetting_agent' 部分的字典。
            faiss_manager (FaissManager): 数据库管理器实例。
        """
        self.config = config
        self.faiss_manager = faiss_manager
        self._task: Optional[asyncio.Task] = None
        logger.info("ForgettingAgent 初始化成功。")

    async def start(self):
        """启动后台遗忘任务。"""
        if not self.config.get("enabled", True):
            logger.info("遗忘代理未启用，不启动后台任务。")
            return

        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run_periodically())
            logger.info("遗忘代理后台任务已启动。")

    async def stop(self):
        """停止后台遗忘任务。"""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                logger.info("遗忘代理后台任务已成功取消。")
        self._task = None

    async def _run_periodically(self):
        """后台任务的循环体。"""
        interval_hours = self.config.get("check_interval_hours", 24)
        interval_seconds = interval_hours * 3600
        logger.info(f"遗忘代理将每 {interval_hours} 小时运行一次。")

        while True:
            try:
                await asyncio.sleep(interval_seconds)
                logger.info("开始执行每日记忆清理任务...")
                await self._prune_memories()
                logger.info("每日记忆清理任务执行完毕。")
            except asyncio.CancelledError:
                logger.info("遗忘代理任务被取消。")
                break
            except Exception as e:
                logger.error(f"遗忘代理后台任务发生错误: {e}", exc_info=True)
                # 即使出错，也等待下一个周期，避免快速失败刷屏
                await asyncio.sleep(60)

    async def _prune_memories(self):
        """执行一次完整的记忆衰减和修剪。"""
        all_memories = await self.faiss_manager.get_all_memories_for_forgetting()
        if not all_memories:
            logger.info("数据库中没有记忆，无需清理。")
            return

        retention_days = self.config.get("retention_days", 90)
        decay_rate = self.config.get("importance_decay_rate", 0.005)
        current_time = time.time()

        memories_to_update = []
        ids_to_delete = []

        for mem in all_memories:
            metadata = json.loads(mem["metadata"])

            # 1. 重要性衰减
            create_time = metadata.get("create_time", current_time)
            days_since_creation = (current_time - create_time) / (24 * 3600)

            # 线性衰减
            decayed_importance = metadata.get("importance", 0.5) - (
                days_since_creation * decay_rate
            )
            metadata["importance"] = max(0, decayed_importance)  # 确保不为负

            mem["metadata"] = metadata  # 更新内存中的 metadata
            memories_to_update.append(mem)

            # 2. 识别待删除项
            retention_seconds = retention_days * 24 * 3600
            is_old = (current_time - create_time) > retention_seconds
            is_unimportant = metadata["importance"] < 0.1  # 硬编码一个低重要性阈值

            if is_old and is_unimportant:
                ids_to_delete.append(mem["id"])

        # 3. 执行数据库操作
        if memories_to_update:
            await self.faiss_manager.update_memories_metadata(memories_to_update)
            logger.info(f"更新了 {len(memories_to_update)} 条记忆的重要性得分。")

        if ids_to_delete:
            await self.faiss_manager.delete_memories(ids_to_delete)
            logger.info(f"删除了 {len(ids_to_delete)} 条陈旧且不重要的记忆。")
