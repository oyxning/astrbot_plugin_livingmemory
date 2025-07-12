# -*- coding: utf-8 -*-
"""
utils.py - 插件的辅助工具函数
"""

import json
from typing import List, Optional

from astrbot.api.star import Context
from astrbot.api.event import AstrMessageEvent
from ..storage.faiss_manager import Result
from .constants import MEMORY_INJECTION_HEADER, MEMORY_INJECTION_FOOTER


async def get_persona_id(context: Context, event: AstrMessageEvent) -> Optional[str]:
    """
    获取当前会话的人格 ID。
    如果当前会话没有特定人格，则返回 AstrBot 的默认人格。
    """
    try:
        session_id = await context.conversation_manager.get_curr_conversation_id(
            event.unified_msg_origin
        )
        conversation = await context.conversation_manager.get_conversation(
            event.unified_msg_origin, session_id
        )
        persona_id = conversation.persona_id if conversation else None

        # 如果无人格或明确设置为None，则使用全局默认人格
        if not persona_id or persona_id == "[%None]":
            default_persona = context.provider_manager.selected_default_persona
            persona_id = default_persona["name"] if default_persona else None

        return persona_id
    except Exception:
        # 在某些情况下（如无会话），获取可能会失败，返回 None
        return None


def format_memories_for_injection(memories: List[Result]) -> str:
    """
    将检索到的记忆列表格式化为单个字符串，以便注入到 System Prompt。
    """
    if not memories:
        return ""

    header = f"{MEMORY_INJECTION_HEADER}\n"
    footer = f"\n{MEMORY_INJECTION_FOOTER}"

    formatted_entries = []
    for mem in memories:
        try:
            # 元数据可能是字符串或字典，需要处理
            metadata_raw = mem.data.get("metadata", "{}")
            metadata = (
                json.loads(metadata_raw)
                if isinstance(metadata_raw, str)
                else metadata_raw
            )

            content = mem.data.get("text", "内容缺失")
            importance = metadata.get("importance", 0.0)

            entry = f"- [重要性: {importance:.2f}] {content}"
            formatted_entries.append(entry)
        except (json.JSONDecodeError, AttributeError):
            # 如果元数据解析失败或格式不正确，则跳过此条记忆
            continue

    if not formatted_entries:
        return ""

    body = "\n".join(formatted_entries)

    return f"{header}{body}{footer}"
