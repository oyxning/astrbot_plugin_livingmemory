# -*- coding: utf-8 -*-
"""
reflection_engine.py - 反思引擎
负责对会话历史进行反思，提取、评估并存储多个独立的、基于事件的记忆。
"""

import json
from typing import List, Dict, Any, Optional

from pydantic import ValidationError

from astrbot.api import logger
from astrbot.api.provider import Provider
from ...storage.faiss_manager import FaissManager
from ..utils import extract_json_from_response
from ..models import (
    MemoryEvent,
    _LLMExtractionEventList,
    _LLMScoreEvaluation,
)


class ReflectionEngine:
    """
    反思引擎：负责对会话历史进行反思，提取、评估并存储多个独立的、基于事件的记忆。
    采用两阶段流程：1. 批量提取事件 2. 批量评估分数
    """

    def __init__(
        self,
        config: Dict[str, Any],
        llm_provider: Provider,
        faiss_manager: FaissManager,
    ):
        self.config = config
        self.llm_provider = llm_provider
        self.faiss_manager = faiss_manager
        logger.info("ReflectionEngine 初始化成功。")

    async def _extract_events(
        self, history_text: str, persona_prompt: Optional[str]
    ) -> List[MemoryEvent]:
        """第一阶段：从对话历史中批量提取记忆事件。"""
        system_prompt = self._build_event_extraction_prompt(persona_prompt)
        user_prompt = f"下面是你需要分析的对话历史：\n{history_text}"

        response = await self.llm_provider.text_chat(
            prompt=user_prompt, system_prompt=system_prompt, json_mode=True
        )

        json_text = extract_json_from_response(response.completion_text.strip())
        if not json_text:
            logger.warning("LLM 提取事件返回为空。")
            return []

        try:
            extracted_data = _LLMExtractionEventList.model_validate_json(json_text)
            # 转换为 MemoryEvent 对象列表
            # 注意：LLM 返回的是 _LLMExtractionEvent，其 id 字段对应 MemoryEvent 的 temp_id
            memory_events = []
            for event in extracted_data.events:
                event_dict = event.model_dump()
                # 将 'id' 字段重命名为 'temp_id' 以匹配 MemoryEvent 模型
                if "id" in event_dict:
                    event_dict["temp_id"] = event_dict.pop("id")
                memory_events.append(MemoryEvent(**event_dict))
            return memory_events
        except (ValidationError, json.JSONDecodeError) as e:
            logger.error(
                f"事件提取阶段JSON解析失败: {e}\n原始返回: {response.completion_text.strip()}",
                exc_info=True,
            )
            return []

    async def _evaluate_scores(
        self, events: List[MemoryEvent], persona_prompt: Optional[str]
    ) -> Dict[str, float]:
        """第二阶段：对一批记忆事件进行批量评分。"""
        if not events:
            return {}

        system_prompt = self._build_evaluation_prompt(persona_prompt)

        # 构建批量评估的输入
        memories_to_evaluate = [
            {"id": event.temp_id, "content": event.memory_content} for event in events
        ]
        user_prompt = json.dumps(
            {"memories": memories_to_evaluate}, ensure_ascii=False, indent=2
        )

        response = await self.llm_provider.text_chat(
            prompt=user_prompt, system_prompt=system_prompt, json_mode=True
        )

        json_text = extract_json_from_response(response.completion_text.strip())
        if not json_text:
            logger.warning("LLM 评估分数返回为空。")
            return {}

        try:
            evaluated_data = _LLMScoreEvaluation.model_validate_json(json_text)
            return evaluated_data.scores
        except (ValidationError, json.JSONDecodeError) as e:
            logger.error(
                f"分数评估阶段JSON解析失败: {e}\n原始返回: {response.completion_text.strip()}",
                exc_info=True,
            )
            return []

    async def reflect_and_store(
        self,
        conversation_history: List[Dict[str, str]],
        session_id: str,
        persona_id: Optional[str] = None,
        persona_prompt: Optional[str] = None,
    ):
        """执行完整的两阶段反思、评估和存储流程。"""
        try:
            history_text = self._format_history_for_summary(conversation_history)
            if not history_text:
                logger.debug("对话历史为空，跳过反思。")
                return

            # --- 第一阶段：提取事件 ---
            logger.info(f"[{session_id}] 阶段1：开始批量提取记忆事件...")
            extracted_events = await self._extract_events(history_text, persona_prompt)
            if not extracted_events:
                logger.info(f"[{session_id}] 未能从对话中提取任何记忆事件。")
                return
            logger.info(f"[{session_id}] 成功提取 {len(extracted_events)} 个记忆事件。")

            # --- 第二阶段：评估分数 ---
            logger.info(f"[{session_id}] 阶段2：开始批量评估事件重要性...")
            scores = await self._evaluate_scores(extracted_events, persona_prompt)
            logger.info(f"[{session_id}] 成功收到 {len(scores)} 个评分。")

            # --- 第三阶段：合并与存储 ---
            threshold = self.config.get("importance_threshold", 0.5)
            stored_count = 0
            for event in extracted_events:
                score = scores.get(event.temp_id)
                if score is None:
                    logger.warning(
                        f"[{session_id}] 事件 '{event.temp_id}' 未找到对应的评分，跳过。"
                    )
                    continue

                event.importance_score = score

                if event.importance_score >= threshold:
                    # MemoryEvent 的 id 将由存储后端自动生成，这里不需要手动创建
                    # 我们只需要传递完整的元数据
                    event_metadata = json.loads(event.model_dump_json())

                    # add_memory 返回的是新插入记录的整数 ID
                    inserted_id = await self.faiss_manager.add_memory(
                        content=event.memory_content,
                        importance=event.importance_score,
                        session_id=session_id,
                        persona_id=persona_id,
                        metadata=event_metadata,
                    )
                    stored_count += 1
                    logger.debug(
                        f"[{session_id}] 存储记忆事件 (ID: {inserted_id})，得分 {event.importance_score:.2f}"
                    )
                else:
                    logger.debug(
                        f"[{session_id}] 忽略记忆事件 '{event.temp_id}'，得分 {event.importance_score:.2f} 低于阈值 {threshold}。"
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
        """构建用于第一阶段事件提取的系统 Prompt。"""
        schema = _LLMExtractionEventList.model_json_schema()
        base_prompt = self.config.get(
            "event_extraction_prompt",
            "你是一个善于分析和总结的AI助手。你的任务是仔细阅读一段对话历史，并从中提取出多个独立的、有意义的记忆事件。这些事件可以是关于事实、用户的偏好、目标、观点或你们之间关系的变化。请严格按照指定的 JSON 格式返回你的分析结果，不要包含任何评分信息，也不要添加任何额外的解释或文字。",
        ).strip()
        persona_section = (
            f"\n**重要：**在分析时请代入以下人格：\n<persona>{persona_prompt}</persona>\n"
            if persona_prompt
            else ""
        )

        return f"""{base_prompt}
{persona_section}
**核心指令**
1.  **分析对话**: 从下面的对话历史中提取关键事件。
2.  **格式化输出**: 必须返回一个符合以下 JSON Schema 的 JSON 对象。为每个事件生成一个临时的、唯一的 `temp_id` 字符串。

**输出格式要求 (JSON Schema)**
```json
{json.dumps(schema, indent=2)}
```
"""

    def _build_evaluation_prompt(self, persona_prompt: Optional[str]) -> str:
        """构建用于第二阶段批量评分的系统 Prompt。"""
        schema = _LLMScoreEvaluation.model_json_schema()
        base_prompt = self.config.get(
            "evaluation_prompt",
            "请评估以下记忆条目的重要性，对于未来的对话有多大的参考价值？请给出一个 0.0 到 1.0 之间的分数，其中 1.0 代表极其重要，0.0 代表毫无价值。请只返回数字，不要包含任何其他文本。",
        ).strip()
        persona_section = (
            f"\n**重要：**在评估时请代入以下人格，这会影响你对“重要性”的判断：\n<persona>{persona_prompt}</persona>\n"
            if persona_prompt
            else ""
        )

        return f"""{base_prompt}
{persona_section}
**核心指令**
1.  **分析输入**: 输入是一个包含多个记忆事件的 JSON 对象，每个事件都有一个 `temp_id` 和内容。
2.  **评估重要性**: 对列表中的每一个事件，评估其对于未来对话的长期参考价值，给出一个 0.0 到 1.0 之间的分数。
3.  **格式化输出**: 必须返回一个符合以下 JSON Schema 的 JSON 对象，key 是对应的 `temp_id`，value 是你给出的分数。

**输出格式要求 (JSON Schema)**
```json
{json.dumps(schema, indent=2)}
```

**一个正确的输出示例**
```json
{{
  "scores": {{
    "event_1": 0.8,
    "user_preference_1": 0.9,
    "project_goal_alpha": 0.95
  }}
}}
```
"""

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
