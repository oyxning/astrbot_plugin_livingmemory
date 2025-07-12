# -*- coding: utf-8 -*-
"""
logic.py - 插件的核心业务逻辑引擎
包含 ReflectionEngine, RecallEngine, 和 ForgettingAgent 的实现。
"""

import asyncio
import time
import math
import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field, ValidationError

from astrbot.api import logger
from astrbot.api.provider import Provider
from ..storage.faiss_manager import FaissManager, Result


# --- 新的数据模型 ---
class EventType(str, Enum):
    FACT = "fact"
    PREFERENCE = "preference"
    GOAL = "goal"
    OPINION = "opinion"
    RELATIONSHIP = "relationship"
    OTHER = "other"


class Entity(BaseModel):
    name: str = Field(..., description="实体名称")
    type: str = Field(..., description="实体类型")


class MemoryEvent(BaseModel):
    # --- 系统生成字段 (不再由 LLM 提供) ---
    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()), description="唯一的事件ID"
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="事件创建的UTC时间戳",
    )

    # --- LLM 生成字段 ---
    importance_score: float = Field(
        ..., ge=0.0, le=1.0, description="记忆的重要性评分 (0.0-1.0)"
    )
    memory_content: str = Field(..., description="对事件的简洁、客观的描述")
    event_type: EventType = Field(default=EventType.OTHER, description="事件的分类")
    entities: List[Entity] = Field(
        default_factory=list, description="事件中涉及的关键实体"
    )

    # --- 系统关联字段 ---
    related_event_ids: List[str] = Field(
        default_factory=list, description="与此事件相关的其他事件ID"
    )

    # --- 原始上下文信息 ---
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="用于存储其他附加信息的灵活字段，例如来源会话、人格等",
    )


class MemoryEventList(BaseModel):
    events: List[MemoryEvent]


# --- 用于生成 Prompt Schema 的私有模型 ---
class _LLMEvent(BaseModel):
    importance_score: float = Field(..., ge=0.0, le=1.0)
    memory_content: str = Field(...)
    event_type: EventType = Field(default=EventType.OTHER)
    entities: List[Entity] = Field(default_factory=list)
    related_event_ids: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class _LLMEventList(BaseModel):
    events: List[_LLMEvent]


# --- 引擎实现 ---
class ReflectionEngine:
    """
    反思引擎：负责对会话历史进行反思，提取、评估并存储多个独立的、基于事件的记忆。
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
            llm_provider (Provider): 用于提取和评估的 LLM Provider。
            faiss_manager (FaissManager): 数据库管理器实例。
        """
        self.config = config
        self.llm_provider = llm_provider
        self.faiss_manager = faiss_manager
        logger.info("ReflectionEngine 初始化成功 (v2 - Event-based)。")

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
            # 1. 格式化历史记录
            history_text = self._format_history_for_summary(conversation_history)
            if not history_text:
                logger.debug("对话历史为空，跳过反思。")
                return

            # 2. 构建新的 Prompt，要求 LLM 以 JSON 格式返回事件列表
            summary_prompt = self._build_event_extraction_prompt(persona_prompt)

            user_prompt = f"下面是你需要分析的对话历史：\n{history_text}"

            # 3. 请求 LLM 提取事件
            logger.info(f"[{session_id}] 开始从对话历史中提取记忆事件...")

            # 假设 provider 支持 json_mode，如果不支持，prompt 的健壮性至关重要
            response = await self.llm_provider.text_chat(
                prompt=user_prompt,
                system_prompt=summary_prompt,
                json_mode=True,  # 启用 JSON 模式
            )

            response_text = response.completion_text.strip()
            if not response_text:
                logger.warning(f"[{session_id}] LLM 提取事件返回为空，任务中止。")
                return

            # 4. 解析和验证返回的 JSON
            try:
                memory_event_list = MemoryEventList.model_validate_json(response_text)
                logger.info(
                    f"[{session_id}] 成功解析 {len(memory_event_list.events)} 个记忆事件。"
                )
            except (ValidationError, json.JSONDecodeError) as e:
                logger.error(
                    f"[{session_id}] LLM 返回的 JSON 无效或不符合模型定义: {e}\n原始返回: {response_text}",
                    exc_info=True,
                )
                return

            # 5. 迭代、评估和存储每个事件
            threshold = self.config.get("importance_threshold", 0.5)
            stored_count = 0
            for event in memory_event_list.events:
                if event.importance_score >= threshold:
                    # 将整个 Event 对象序列化为 JSON 存入元数据
                    event_metadata = json.loads(event.model_dump_json())

                    await self.faiss_manager.add_memory(
                        content=event.memory_content,
                        importance=event.importance_score,
                        session_id=session_id,
                        persona_id=persona_id,
                        metadata=event_metadata,
                    )
                    stored_count += 1
                    logger.debug(
                        f"[{session_id}] 存储记忆事件 '{event.id}'，得分 {event.importance_score:.2f}"
                    )
                else:
                    logger.debug(
                        f"[{session_id}] 忽略记忆事件 '{event.id}'，得分 {event.importance_score:.2f} 低于阈值 {threshold}。"
                    )

            if stored_count > 0:
                logger.info(f"[{session_id}] 成功存储 {stored_count} 个新的记忆事件。")
            else:
                logger.info(f"[{session_id}] 没有新的记忆事件达到存储阈值。")

        except Exception as e:
            logger.error(
                f"[{session_id}] 在执行反思与存储任务时发生严重错误: {e}", exc_info=True
            )

    def _build_event_extraction_prompt(self, persona_prompt: Optional[str]) -> str:
        """构建用于提取记忆事件的系统 Prompt。"""

        # 1. 从 Pydantic 模型生成 JSON Schema，但要排除系统生成的字段
        schema = _LLMEventList.model_json_schema()

        # 2. 从配置或默认值获取基础 prompt
        base_prompt = self.config.get(
            "event_extraction_prompt",
            """
你是一个善于分析和总结的AI助手。你的任务是仔细阅读一段对话历史，并从中提取出多个独立的、有意义的记忆事件。
这些事件可以是关于事实、用户的偏好、目标、观点或你们之间关系的变化。

你需要为每个提取的事件评估一个重要性分数（0.0到1.0之间），分数越高代表该记忆越关键、越长久。

请严格按照下面提供的 JSON 格式返回你的分析结果，不要添加任何额外的解释或文字。
""",
        ).strip()

        persona_section = ""
        if persona_prompt:
            persona_section = f"\n**重要：**在分析和评估时，请代入以下人格。这会影响你对“重要性”的判断，但注意不要将人格设定本身记录为记忆。\n<persona>{persona_prompt}</persona>\n"

        # 3. 最终的 Prompt 组合
        full_prompt = f"""{base_prompt}
{persona_section}
**核心指令**
1.  **分析对话**: 从下面的对话历史中提取关键事件。
2.  **评估重要性**: 为每个事件打分 (0.0 - 1.0)。
3.  **格式化输出**: 必须返回一个符合以下 JSON Schema 的 JSON 对象。

**输出格式要求 (JSON Schema)**
```json
{json.dumps(schema, indent=2)}
```

**注意：你绝对不能在输出的 JSON 中包含 `id` 或 `timestamp` 字段。这些将由系统自动生成。**

**一个正确的输出示例**
```json
{{
  "events": [
    {{
      "importance_score": 0.8,
      "memory_content": "用户表示他最喜欢的编程语言是 Python，因为其简洁性和强大的库支持。",
      "event_type": "preference",
      "entities": [
        {{"name": "Python", "type": "Programming Language"}}
      ],
      "related_event_ids": [],
      "metadata": {{}}
    }},
    {{
      "importance_score": 0.9,
      "memory_content": "用户设定了一个目标，希望在本季度内完成他的个人项目'Project Phoenix'。",
      "event_type": "goal",
      "entities": [
        {{"name": "Project Phoenix", "type": "Project"}}
      ],
      "related_event_ids": [],
      "metadata": {{"priority": "high"}}
    }}
  ]
}}
```
"""
        return full_prompt

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
