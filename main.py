# -*- coding: utf-8 -*-
"""
main.py - LivingMemory 插件主文件
负责插件注册、初始化所有引擎、绑定事件钩子以及管理生命周期。
"""

import asyncio
import os
from typing import Optional, Dict, Any

# AstrBot API
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.event.filter import PermissionType, permission_type
from astrbot.api.star import Context, Star, register, StarTools
from astrbot.api.provider import (
    LLMResponse,
    ProviderRequest,
    Provider,
)
from astrbot.core.provider.provider import EmbeddingProvider

from astrbot.api import logger
from astrbot.core.db.vec_db.faiss_impl.vec_db import FaissVecDB

# 插件内部模块
from .storage.faiss_manager import FaissManager
from .core.logic import RecallEngine, ReflectionEngine, ForgettingAgent
from .core.utils import get_persona_id, format_memories_for_injection

# 简易会话管理器，用于跟踪对话历史和轮次
# key: session_id, value: {"history": [], "round_count": 0}
session_manager = {}


@register(
    "LivingMemory",
    "lxfight",
    "一个拥有动态生命周期的智能长期记忆插件。",
    "1.0.0",
    "https://github.com/lxfight/astrbot_plugin_livingmemory",
)
class LivingMemoryPlugin(Star):
    def __init__(self, context: Context, config: Dict[str, Any]):
        super().__init__(context)
        self.config = config
        self.context = context

        # 初始化状态
        self.embedding_provider: Optional[EmbeddingProvider] = None
        self.llm_provider: Optional[Provider] = None
        self.db: Optional[FaissVecDB] = None
        self.faiss_manager: Optional[FaissManager] = None
        self.recall_engine: Optional[RecallEngine] = None
        self.reflection_engine: Optional[ReflectionEngine] = None
        self.forgetting_agent: Optional[ForgettingAgent] = None

        # 启动异步初始化流程
        asyncio.create_task(self._initialize_plugin())

    async def _initialize_plugin(self):
        """
        执行插件的异步初始化。
        """
        logger.info("开始异步初始化 LivingMemory 插件...")
        try:
            # 1. 初始化 Provider
            self._initialize_providers()
            if not self.embedding_provider or not self.llm_provider:
                logger.error("Provider 初始化失败，插件无法正常工作。")
                return

            # 2. 初始化数据库和管理器
            data_dir = StarTools.get_data_dir()
            db_path = os.path.join(data_dir, "livingmemory.db")
            index_path = os.path.join(data_dir, "livingmemory.index")
            self.db = FaissVecDB(db_path, index_path, self.embedding_provider)
            await self.db.initialize()
            logger.info(f"数据库已初始化。数据目录: {data_dir}")

            self.faiss_manager = FaissManager(self.db)

            # 3. 初始化三大核心引擎
            self.recall_engine = RecallEngine(
                self.config.get("recall_engine", {}), self.faiss_manager
            )
            self.reflection_engine = ReflectionEngine(
                self.config.get("reflection_engine", {}),
                self.llm_provider,
                self.faiss_manager,
            )
            self.forgetting_agent = ForgettingAgent(
                self.config.get("forgetting_agent", {}), self.faiss_manager
            )

            # 4. 启动后台任务
            await self.forgetting_agent.start()

            logger.info("LivingMemory 插件初始化成功！")

        except Exception as e:
            logger.critical(
                f"LivingMemory 插件初始化过程中发生严重错误: {e}", exc_info=True
            )

    def _initialize_providers(self):
        """
        初始化 Embedding 和 LLM provider。
        """
        # 初始化 Embedding Provider
        emb_id = self.config.get("provider_settings", {}).get("embedding_provider_id")
        if emb_id:
            self.embedding_provider = self.context.get_provider_by_id(emb_id)
            if self.embedding_provider:
                logger.info(f"成功从配置加载 Embedding Provider: {emb_id}")

        if not self.embedding_provider:
            self.embedding_provider = (
                self.context.provider_manager.embedding_provider_insts[0]
            )
            logger.info(
                f"未指定 Embedding Provider，使用默认的: {self.embedding_provider.provider_config.get('id')}"
            )

        if not self.embedding_provider:
            # 如果没有指定 Embedding Provider，则无法继续
            self.embedding_provider = None
            logger.error("未指定 Embedding Provider，插件将无法使用。")

        # 初始化 LLM Provider
        llm_id = self.config.get("provider_settings", {}).get("llm_provider_id")
        if llm_id:
            self.llm_provider = self.context.get_provider_by_id(llm_id)
            if self.llm_provider:
                logger.info(f"成功从配置加载 LLM Provider: {llm_id}")
        else:
            self.llm_provider = self.context.get_using_provider()
            logger.info("使用 AstrBot 当前默认的 LLM Provider。")

    @filter.on_llm_request()
    async def handle_memory_recall(self, event: AstrMessageEvent, req: ProviderRequest):
        """
        [事件钩子] 在 LLM 请求前，查询并注入长期记忆。
        """
        if not self.recall_engine:
            logger.debug("回忆引擎尚未初始化，跳过记忆召回。")
            return

        try:
            session_id = (
                await self.context.conversation_manager.get_curr_conversation_id(
                    event.unified_msg_origin
                )
            )
            # 根据配置决定是否进行过滤
            filtering_config = self.config.get("filtering_settings", {})
            use_persona_filtering = filtering_config.get("use_persona_filtering", True)
            use_session_filtering = filtering_config.get("use_session_filtering", True)

            persona_id = await get_persona_id(self.context, event)

            recall_session_id = session_id if use_session_filtering else None
            recall_persona_id = persona_id if use_persona_filtering else None

            # 使用 RecallEngine 进行智能回忆
            recalled_memories = await self.recall_engine.recall(
                req.prompt, recall_session_id, recall_persona_id
            )

            if recalled_memories:
                # 格式化并注入记忆
                memory_str = format_memories_for_injection(recalled_memories)
                req.system_prompt = memory_str + "\n" + req.system_prompt
                logger.info(
                    f"[{session_id}] 成功向 System Prompt 注入 {len(recalled_memories)} 条记忆。"
                )

            # 管理会话历史
            if session_id not in session_manager:
                session_manager[session_id] = {"history": [], "round_count": 0}
            session_manager[session_id]["history"].append(
                {"role": "user", "content": req.prompt}
            )

        except Exception as e:
            logger.error(f"处理 on_llm_request 钩子时发生错误: {e}", exc_info=True)

    @filter.on_llm_response()
    async def handle_memory_reflection(
        self, event: AstrMessageEvent, resp: LLMResponse
    ):
        """
        [事件钩子] 在 LLM 响应后，检查是否需要进行反思和记忆存储。
        """
        if not self.reflection_engine or resp.role != "assistant":
            logger.debug("反思引擎尚未初始化或响应不是助手角色，跳过反思。")
            return

        try:
            session_id = (
                await self.context.conversation_manager.get_curr_conversation_id(
                    event.unified_msg_origin
                )
            )
            if not session_id or session_id not in session_manager:
                return

            # 添加助手响应到历史并增加轮次计数
            current_session = session_manager[session_id]
            current_session["history"].append(
                {"role": "assistant", "content": resp.completion_text}
            )
            current_session["round_count"] += 1

            # 检查是否满足总结条件
            trigger_rounds = self.config.get("reflection_engine", {}).get(
                "summary_trigger_rounds", 10
            )
            logger.debug(
                f"[{session_id}] 当前轮次: {current_session['round_count']}, 触发轮次: {trigger_rounds}"
            )
            if current_session["round_count"] >= trigger_rounds:
                logger.info(
                    f"[{session_id}] 对话达到 {trigger_rounds} 轮，启动反思任务。"
                )

                history_to_reflect = list(current_session["history"])
                # 重置会话
                session_manager[session_id] = {"history": [], "round_count": 0}

                persona_id = await get_persona_id(self.context, event)

                # 获取人格提示词
                persona_prompt = None
                filtering_config = self.config.get("filtering_settings", {})
                if filtering_config.get("use_persona_filtering", True) and persona_id:
                    list_personas = self.context.provider_manager.personas
                    # 获取当前人格的提示词
                    for persona_obj in list_personas:
                        if persona_obj.get("name") == persona_id:
                            persona_prompt = persona_obj.get("prompt")
                            break

                # 创建后台任务进行反思和存储
                logger.debug(
                    f"正在处理反思任务，session_id: {session_id}, persona_id: {persona_id}"
                )
                asyncio.create_task(
                    self.reflection_engine.reflect_and_store(
                        conversation_history=history_to_reflect,
                        session_id=session_id,
                        persona_id=persona_id,
                        persona_prompt=persona_prompt,
                    )
                )

        except Exception as e:
            logger.error(f"处理 on_llm_response 钩子时发生错误: {e}", exc_info=True)

    # --- 命令处理 ---
    @filter.command_group("lmem")
    def lmem_group(self):
        """长期记忆管理命令组 /lmem"""
        pass

    @permission_type(PermissionType.ADMIN)
    @lmem_group.command("status")
    async def lmem_status(self, event: AstrMessageEvent):
        """[管理员] 查看当前记忆库的状态。"""
        if not self.faiss_manager or not self.faiss_manager.db:
            yield event.plain_result("记忆库尚未初始化。")
            return

        count = await self.faiss_manager.db.count_documents()
        yield event.plain_result(f"📊 LivingMemory 记忆库状态：\n- 总记忆数: {count}")

    @permission_type(PermissionType.ADMIN)
    @lmem_group.command("search")
    async def lmem_search(self, event: AstrMessageEvent, query: str, k: int = 3):
        """[管理员] 手动搜索记忆。"""
        if not self.recall_engine:
            yield event.plain_result("回忆引擎尚未初始化。")
            return

        results = await self.recall_engine.recall(query, k=k)
        if not results:
            yield event.plain_result(f"未能找到与 '{query}' 相关的记忆。")
            return

        response = f"与 '{query}' 最相关的 {len(results)} 条记忆：\n"
        for i, res in enumerate(results):
            response += f"{i + 1}. ID: {res.data['id']}, 最终得分: {res.similarity:.4f}\n   内容: {res.data['text']}\n"

        yield event.plain_result(response)

    @permission_type(PermissionType.ADMIN)
    @lmem_group.command("forget")
    async def lmem_forget(self, event: AstrMessageEvent, doc_id: int):
        """[管理员] 强制删除一条指定 ID 的记忆。"""
        if not self.faiss_manager:
            yield event.plain_result("记忆库尚未初始化。")
            return

        try:
            await self.faiss_manager.delete_memories([doc_id])
            yield event.plain_result(f"已成功删除 ID 为 {doc_id} 的记忆。")
        except Exception as e:
            yield event.plain_result(f"删除记忆失败: {e}")

    @permission_type(PermissionType.ADMIN)
    @lmem_group.command("run_forgetting_agent")
    async def run_forgetting_agent(self, event: AstrMessageEvent):
        """[管理员] 手动触发一次遗忘代理的清理任务。"""
        if not self.forgetting_agent:
            yield event.plain_result("遗忘代理尚未初始化。")
            return

        yield event.plain_result("正在后台手动触发遗忘代理任务...")

        # 使用 create_task 以避免阻塞当前事件循环
        async def run_and_notify():
            try:
                await self.forgetting_agent._prune_memories()
                await self.context.send_message(
                    event.unified_msg_origin, "遗忘代理任务执行完毕。"
                )
            except Exception as e:
                await self.context.send_message(
                    event.unified_msg_origin, f"遗忘代理任务执行失败: {e}"
                )

        asyncio.create_task(run_and_notify())

    async def terminate(self):
        """
        插件停止时的清理逻辑。
        """
        logger.info("LivingMemory 插件正在停止...")
        if self.forgetting_agent:
            await self.forgetting_agent.stop()
        if self.db:
            await self.db.close()
        logger.info("LivingMemory 插件已成功停止。")
