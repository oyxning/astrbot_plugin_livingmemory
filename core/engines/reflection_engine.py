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
        system_prompt = self._build_event_extraction_prompt()
        persona_section = (
            f"\n**重要：**在分析时请代入以下人格，但是应该秉持着记录互动者的原则：\n<persona>{persona_prompt}</persona>\n"
            if persona_prompt
            else ""
        )
        user_prompt = f"{persona_section}下面是你需要分析的对话历史：\n{history_text}"

        response = await self.llm_provider.text_chat(
            prompt=user_prompt, system_prompt=system_prompt, json_mode=True
        )

        json_text = extract_json_from_response(response.completion_text.strip())
        if not json_text:
            logger.warning("LLM 提取事件返回为空。")
            return []
        logger.debug(f"提取到的记忆事件: {json_text}")

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

        system_prompt = self._build_evaluation_prompt()

        # 构建批量评估的输入
        memories_to_evaluate = [
            {"id": event.temp_id, "content": event.memory_content} for event in events
        ]
        persona_section = (
            f"\n**重要：**在评估时请代入以下人格，这会影响你对“重要性”的判断：\n<persona>{persona_prompt}</persona>\n"
            if persona_prompt
            else ""
        )
        user_prompt = persona_section + json.dumps(
            {"memories": memories_to_evaluate}, ensure_ascii=False, indent=2
        )

        response = await self.llm_provider.text_chat(
            prompt=user_prompt, system_prompt=system_prompt, json_mode=True
        )

        json_text = extract_json_from_response(response.completion_text.strip())
        if not json_text:
            logger.warning("LLM 评估分数返回为空。")
            return {}
        logger.debug(
            f"评估分数: {json_text}，对应内容{[event.temp_id for event in events]}。"
        )

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

    def _build_event_extraction_prompt(self) -> str:
        """构建用于第一阶段事件提取的系统 Prompt。"""
        schema = _LLMExtractionEventList.model_json_schema()
        base_prompt = self.config.get(
            "event_extraction_prompt",
            "### 角色\n你是一个善于分析和总结的AI助手。你的核心人设是从你自身的视角出发，记录与用户的互动和观察。\n\n### 指令/任务\n1.  **仔细阅读**并理解下面提供的“对话历史”。\n2.  从**你（AI）的视角**出发，提取出多个独立的、有意义的记忆事件。事件必须准确描述，参考上下文。事件必须是完整的，具有前因后果的。**不允许编造事件**，**不允许改变事件**，**详细描述事件的所有信息**\n3.  **核心要求**：\n    *   **第一人称视角**：所有事件都必须以“我”开头进行描述，例如“我告诉用户...”、“我观察到...”、“我被告知...”。\n    *   **使用具体名称**：直接使用对话中出现的人物昵称，**严禁**使用“用户”、“开发者”等通用词汇。\n    *   **记录互动者**：必须明确记录与你互动的用户名称。\n    *   **事件合并**：如果多条连续的对话构成一个完整的独立事件，应将其总结概括为一条记忆。\n4.**严禁**包含任何评分、额外的解释或说明性文字。\n    直接输出结果，不要有任何引言或总结。\n\n### 上下文\n*   在对话历史中，名为“AstrBot”的发言者就是**你自己**。\n*   记忆事件是：你与用户互动事的事件描述，详细记录谁、在何时、何地、做了什么、发生了什么。\n\n 'memory_content' 字段必须包含完整的事件描述，不能省略任何细节。\n\n单个系列事件必须详细记录在一个memory_content 中，形成完整的具有前因后果的事件记忆。\n\n",
        ).strip()

        return f"""{base_prompt}
            **核心指令**
            1.  **分析对话**: 从下面的对话历史中提取关键事件。
            2.  **格式化输出**: 必须返回一个符合以下 JSON Schema 的 JSON 对象。为每个事件生成一个临时的、唯一的 `temp_id` 字符串。

            **输出格式要求 (JSON Schema)**
            ```json
            {json.dumps(schema, indent=2)}
            ```
            """

    def _build_evaluation_prompt(self) -> str:
        """构建用于第二阶段批量评分的系统 Prompt。"""
        schema = _LLMScoreEvaluation.model_json_schema()
        base_prompt = self.config.get(
            "evaluation_prompt",
            "### 角色\n你是一个专门评估记忆价值的AI分析模型。你的判断标准是该记忆对于与特定用户构建长期、个性化、有上下文的对话有多大的帮助。\n\n### 指令/任务\n1.  **评估核心价值**：仔细阅读“记忆内容”，评估其对于未来对话的长期参考价值。\n2.  **输出分数**：给出一个介于 0.0 到 1.0 之间的浮点数分数。\n3.  **格式要求**：**只返回数字**，严禁包含任何额外的文本、解释或理由。\n\n### 上下文\n评分时，请参考以下价值标尺：\n*   **高价值 (0.8 - 1.0)**：包含用户的核心身份信息、明确且长期的个人偏好/厌恶、设定的目标、重要的关系或事实。这些信息几乎总能在未来的互动中被引用。\n    *   例如：用户的昵称、职业、关键兴趣点、对AI的称呼、重要的人生目标。\n*   **中等价值 (0.4 - 0.7)**：包含用户的具体建议、功能请求、对某事的观点或一次性的重要问题。这些信息在短期内或特定话题下很有用，但可能随着时间推移或问题解决而失去价值。\n    *   例如：对某个功能的反馈、对特定新闻事件的看法、报告了一个具体的bug。\n*   **低价值 (0.1 - 0.3)**：包含短暂的情绪表达、日常问候、或非常具体且不太可能重复的上下文。这些信息很少有再次利用的机会。\n    *   例如：一次性的惊叹、害怕的反应、普通的“你好”、“晚安”。\n*   **无价值 (0.0)**：信息完全是瞬时的、无关紧要的，或者不包含任何关于用户本人的可复用信息。\n    *   例如：观察到另一个机器人说了话、对一句无法理解的话的默认回应。\n\n### 问题\n请评估以下“记忆内容”的重要性，对于未来的对话有多大的参考价值？\n\n---\n\n**记忆内容**：\n{memory_content}\n\n",
        ).strip()

        return f"""{base_prompt}
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
