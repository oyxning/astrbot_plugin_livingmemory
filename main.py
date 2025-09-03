# -*- coding: utf-8 -*-
"""
main.py - LivingMemory 插件主文件
负责插件注册、初始化所有引擎、绑定事件钩子以及管理生命周期。
"""

import asyncio
import os
import json
from datetime import datetime, timezone
from typing import Optional, Dict, Any

# AstrBot API
from astrbot.api.event import filter, AstrMessageEvent,MessageChain
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
from .core.engines.recall_engine import RecallEngine
from .core.engines.reflection_engine import ReflectionEngine
from .core.engines.forgetting_agent import ForgettingAgent
from .core.retrieval import SparseRetriever
from .core.utils import get_persona_id, format_memories_for_injection, get_now_datetime

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
        self.sparse_retriever: Optional[SparseRetriever] = None
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

            # 2.5. 初始化稀疏检索器
            sparse_config = self.config.get("sparse_retriever", {})
            if sparse_config.get("enabled", True):
                self.sparse_retriever = SparseRetriever(db_path, sparse_config)
                await self.sparse_retriever.initialize()
            else:
                self.sparse_retriever = None

            # 3. 初始化三大核心引擎
            self.recall_engine = RecallEngine(
                self.config.get("recall_engine", {}), 
                self.faiss_manager,
                self.sparse_retriever
            )
            self.reflection_engine = ReflectionEngine(
                self.config.get("reflection_engine", {}),
                self.llm_provider,
                self.faiss_manager,
            )
            self.forgetting_agent = ForgettingAgent(
                self.context,
                self.config.get("forgetting_agent", {}),
                self.faiss_manager,
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
            # 检查是否有可用的embedding provider
            embedding_providers = self.context.provider_manager.embedding_provider_insts
            if embedding_providers:
                self.embedding_provider = embedding_providers[0]
                logger.info(
                    f"未指定 Embedding Provider，使用默认的: {self.embedding_provider.provider_config.get('id')}"
                )
            else:
                # 如果没有可用的embedding provider，则无法继续
                self.embedding_provider = None
                logger.error("没有可用的 Embedding Provider，插件将无法使用。")

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
                self.context, req.prompt, recall_session_id, recall_persona_id
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
                
                async def reflection_task():
                    try:
                        await self.reflection_engine.reflect_and_store(
                            conversation_history=history_to_reflect,
                            session_id=session_id,
                            persona_id=persona_id,
                            persona_prompt=persona_prompt,
                        )
                    except Exception as e:
                        logger.error(f"反思任务执行失败: {e}", exc_info=True)
                
                asyncio.create_task(reflection_task())

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

        results = await self.recall_engine.recall(self.context, query, k=k)
        if not results:
            yield event.plain_result(f"未能找到与 '{query}' 相关的记忆。")
            return

        response_parts = [f"为您找到 {len(results)} 条相关记忆："]
        tz = get_now_datetime(self.context).tzinfo  # 获取当前时区

        for res in results:
            metadata = res.data.get("metadata", {})
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except json.JSONDecodeError:
                    metadata = {}

            def format_timestamp(ts):
                if not ts:
                    return "未知"
                try:
                    dt_utc = datetime.fromtimestamp(float(ts), tz=timezone.utc)
                    dt_local = dt_utc.astimezone(tz)
                    return dt_local.strftime("%Y-%m-%d %H:%M:%S")
                except (ValueError, TypeError):
                    return "未知"

            create_time_str = format_timestamp(metadata.get("create_time"))
            last_access_time_str = format_timestamp(metadata.get("last_access_time"))

            importance_score = metadata.get("importance", 0.0)
            event_type = metadata.get("event_type", "未知")

            card = (
                f"ID: {res.data['id']}\n"
                f"记 忆 度: {res.similarity:.2f}\n"
                f"重 要 性: {importance_score:.2f}\n"
                f"记忆类型: {event_type}\n\n"
                f"内容: {res.data['text']}\n\n"
                f"创建于: {create_time_str}\n"
                f"最后访问: {last_access_time_str}"
            )
            response_parts.append(card)

        response = "\n\n".join(response_parts)
        yield event.plain_result(response)

    @permission_type(PermissionType.ADMIN)
    @lmem_group.command("forget")
    async def lmem_forget(self, event: AstrMessageEvent, doc_id: int):
        """[管理员] 强制删除一条指定整数 ID 的记忆。"""
        if not self.faiss_manager:
            yield event.plain_result("记忆库尚未初始化。")
            return

        try:
            await self.faiss_manager.delete_memories([doc_id])
            yield event.plain_result(f"已成功删除 ID 为 {doc_id} 的记忆。")
        except Exception as e:
            yield event.plain_result(f"删除记忆时发生错误: {e}")

    @permission_type(PermissionType.ADMIN)
    @lmem_group.command("run_forgetting_agent")
    async def run_forgetting_agent(self, event: AstrMessageEvent):
        """[管理员] 手动触发一次遗忘代理的清理任务。"""
        if not self.forgetting_agent:
            yield event.plain_result("遗忘代理尚未初始化。")
            return

        yield event.plain_result("正在后台手动触发遗忘代理任务...")
        try:
            logger.debug("1")
            await self.forgetting_agent._prune_memories()
            await self.context.send_message(
                event.unified_msg_origin, MessageChain().message("遗忘代理任务执行完毕。")
            )
        except Exception as e:
            logger.error(f"遗忘代理任务执行失败: {e}", exc_info=True)
            await self.context.send_message(
                event.unified_msg_origin, MessageChain().message(f"遗忘代理任务执行失败: {e}")
            )

    @permission_type(PermissionType.ADMIN)
    @lmem_group.command("sparse_rebuild")
    async def lmem_sparse_rebuild(self, event: AstrMessageEvent):
        """[管理员] 重建稀疏检索索引。"""
        if not self.sparse_retriever:
            yield event.plain_result("稀疏检索器未启用。")
            return

        yield event.plain_result("正在重建稀疏检索索引...")
        try:
            await self.sparse_retriever.rebuild_index()
            yield event.plain_result("稀疏检索索引重建完成。")
        except Exception as e:
            logger.error(f"重建稀疏索引失败: {e}", exc_info=True)
            yield event.plain_result(f"重建稀疏索引失败: {e}")

    @permission_type(PermissionType.ADMIN)
    @lmem_group.command("search_mode")
    async def lmem_search_mode(self, event: AstrMessageEvent, mode: str):
        """[管理员] 设置检索模式。
        
        用法: /lmem search_mode <mode>
        
        模式:
          hybrid - 混合检索（默认）
          dense - 纯密集检索
          sparse - 纯稀疏检索
        """
        valid_modes = ["hybrid", "dense", "sparse"]
        if mode not in valid_modes:
            yield event.plain_result(f"无效的模式，请使用: {', '.join(valid_modes)}")
            return

        if not self.recall_engine:
            yield event.plain_result("回忆引擎尚未初始化。")
            return

        # 更新配置
        self.recall_engine.config["retrieval_mode"] = mode
        yield event.plain_result(f"检索模式已设置为: {mode}")

    @permission_type(PermissionType.ADMIN)
    @lmem_group.command("sparse_test")
    async def lmem_sparse_test(self, event: AstrMessageEvent, query: str, k: int = 5):
        """[管理员] 测试稀疏检索功能。"""
        if not self.sparse_retriever:
            yield event.plain_result("稀疏检索器未启用。")
            return

        try:
            results = await self.sparse_retriever.search(query=query, limit=k)
            
            if not results:
                yield event.plain_result(f"未找到与 '{query}' 相关的记忆。")
                return

            response_parts = [f"🔍 稀疏检索结果 ({len(results)} 条):"]
            
            for i, res in enumerate(results, 1):
                response_parts.append(f"\n{i}. [ID: {res.doc_id}] Score: {res.score:.3f}")
                response_parts.append(f"   内容: {res.content[:100]}{'...' if len(res.content) > 100 else ''}")
                
                # 显示元数据
                metadata = res.metadata
                if metadata.get("event_type"):
                    response_parts.append(f"   类型: {metadata['event_type']}")
                if metadata.get("importance"):
                    response_parts.append(f"   重要性: {metadata['importance']:.2f}")

            yield event.plain_result("\n".join(response_parts))

        except Exception as e:
            logger.error(f"稀疏检索测试失败: {e}", exc_info=True)
            yield event.plain_result(f"稀疏检索测试失败: {e}")

    @permission_type(PermissionType.ADMIN)
    @lmem_group.command("edit")
    async def lmem_edit(self, event: AstrMessageEvent, memory_id: str, field: str, value: str, reason: str = ""):
        """[管理员] 编辑记忆内容或元数据。
        
        用法: /lmem edit <id> <字段> <值> [原因]
        
        字段:
          content - 记忆内容
          importance - 重要性评分 (0.0-1.0)
          type - 事件类型 (FACT/PREFERENCE/GOAL/OPINION/RELATIONSHIP/OTHER)
          status - 状态 (active/archived/deleted)
        
        示例:
          /lmem edit 123 content 这是新的记忆内容 修正了错误信息
          /lmem edit 123 importance 0.9 提高重要性
          /lmem edit 123 type PREFERENCE 重新分类
          /lmem edit 123 status archived 项目已完成
        """
        if not self.faiss_manager:
            yield event.plain_result("记忆库尚未初始化。")
            return

        try:
            # 解析 memory_id 为整数或字符串
            try:
                memory_id_int = int(memory_id)
                memory_id_to_use = memory_id_int
            except ValueError:
                memory_id_to_use = memory_id

            # 解析字段和值
            updates = {}
            
            if field == "content":
                updates["content"] = value
            elif field == "importance":
                try:
                    updates["importance"] = float(value)
                    if not 0.0 <= updates["importance"] <= 1.0:
                        yield event.plain_result("❌ 重要性评分必须在 0.0 到 1.0 之间")
                        return
                except ValueError:
                    yield event.plain_result("❌ 重要性评分必须是数字")
                    return
            elif field == "type":
                valid_types = ["FACT", "PREFERENCE", "GOAL", "OPINION", "RELATIONSHIP", "OTHER"]
                if value not in valid_types:
                    yield event.plain_result(f"❌ 无效的事件类型，必须是: {', '.join(valid_types)}")
                    return
                updates["event_type"] = value
            elif field == "status":
                valid_statuses = ["active", "archived", "deleted"]
                if value not in valid_statuses:
                    yield event.plain_result(f"❌ 无效的状态，必须是: {', '.join(valid_statuses)}")
                    return
                updates["status"] = value
            else:
                yield event.plain_result(f"❌ 未知的字段 '{field}'，支持的字段: content, importance, type, status")
                return

            # 执行更新
            result = await self.faiss_manager.update_memory(
                memory_id=memory_id_to_use,
                update_reason=reason or f"更新{field}",
                **updates
            )

            if result["success"]:
                response_parts = [f"✅ {result['message']}"]
                
                if result["updated_fields"]:
                    response_parts.append("\n📋 已更新的字段:")
                    for f in result["updated_fields"]:
                        response_parts.append(f"  - {f}")
                
                # 如果更新了内容，显示预览
                if "content" in updates and len(updates["content"]) > 100:
                    response_parts.append(f"\n📝 内容预览: {updates['content'][:100]}...")
                
                yield event.plain_result("\n".join(response_parts))
            else:
                yield event.plain_result(f"❌ 更新失败: {result['message']}")

        except Exception as e:
            logger.error(f"编辑记忆时发生错误: {e}", exc_info=True)
            yield event.plain_result(f"编辑记忆时发生错误: {e}")

    @permission_type(PermissionType.ADMIN)
    @lmem_group.command("update")
    async def lmem_update(self, event: AstrMessageEvent, memory_id: str):
        """[管理员] 交互式编辑记忆。
        
        用法: /lmem update <id>
        
        会引导你逐步选择要更新的字段。
        """
        if not self.faiss_manager:
            yield event.plain_result("记忆库尚未初始化。")
            return

        try:
            # 解析 memory_id
            try:
                memory_id_int = int(memory_id)
                docs = await self.faiss_manager.db.document_storage.get_documents(ids=[memory_id_int])
            except ValueError:
                docs = await self.faiss_manager.db.document_storage.get_documents(
                    metadata_filters={"memory_id": memory_id}
                )

            if not docs:
                yield event.plain_result(f"未找到ID为 {memory_id} 的记忆。")
                return

            doc = docs[0]
            metadata = (
                json.loads(doc["metadata"])
                if isinstance(doc["metadata"], str)
                else doc["metadata"]
            )

            # 显示当前记忆信息
            response = f"📝 记忆 {memory_id} 的当前信息:\n\n"
            response += f"内容: {doc['content'][:100]}{'...' if len(doc['content']) > 100 else ''}\n\n"
            response += f"重要性: {metadata.get('importance', 'N/A')}\n"
            response += f"类型: {metadata.get('event_type', 'N/A')}\n"
            response += f"状态: {metadata.get('status', 'active')}\n\n"
            response += "请回复要更新的字段编号:\n"
            response += "1. 内容\n"
            response += "2. 重要性\n"
            response += "3. 事件类型\n"
            response += "4. 状态\n"
            response += "0. 取消"

            yield event.plain_result(response)

            # 这里应该等待用户回复，但由于命令系统的限制，
            # 我们只能引导用户使用 /lmem edit 命令
            yield event.plain_result(f"\n请使用 /lmem edit {memory_id} <字段> <值> [原因] 来更新记忆")

        except Exception as e:
            logger.error(f"查看记忆时发生错误: {e}", exc_info=True)
            yield event.plain_result(f"查看记忆时发生错误: {e}")

    @permission_type(PermissionType.ADMIN)
    @lmem_group.command("history")
    async def lmem_history(self, event: AstrMessageEvent, memory_id: str):
        """[管理员] 查看记忆的更新历史。"""
        if not self.faiss_manager or not self.faiss_manager.db:
            yield event.plain_result("记忆库尚未初始化。")
            return

        try:
            # 解析 memory_id
            try:
                memory_id_int = int(memory_id)
                docs = await self.faiss_manager.db.document_storage.get_documents(ids=[memory_id_int])
            except ValueError:
                docs = await self.faiss_manager.db.document_storage.get_documents(
                    metadata_filters={"memory_id": memory_id}
                )

            if not docs:
                yield event.plain_result(f"未找到ID为 {memory_id} 的记忆。")
                return

            doc = docs[0]
            metadata = (
                json.loads(doc["metadata"])
                if isinstance(doc["metadata"], str)
                else doc["metadata"]
            )

            response_parts = [f"📝 记忆 {memory_id} 的详细信息:"]
            response_parts.append(f"\n内容: {doc['content']}")
            
            # 基本信息
            response_parts.append(f"\n📊 基本信息:")
            response_parts.append(f"- 重要性: {metadata.get('importance', 'N/A')}")
            response_parts.append(f"- 类型: {metadata.get('event_type', 'N/A')}")
            response_parts.append(f"- 状态: {metadata.get('status', 'active')}")
            
            # 时间信息
            tz = get_now_datetime(self.context).tzinfo
            create_time = metadata.get('create_time')
            if create_time:
                dt = datetime.fromtimestamp(create_time, tz=timezone.utc)
                dt_local = dt.astimezone(tz)
                response_parts.append(f"- 创建时间: {dt_local.strftime('%Y-%m-%d %H:%M:%S')}")
            
            # 更新历史
            update_history = metadata.get('update_history', [])
            if update_history:
                response_parts.append(f"\n🔄 更新历史 ({len(update_history)} 次):")
                for i, update in enumerate(update_history[-5:], 1):  # 只显示最近5次
                    timestamp = update.get('timestamp')
                    if timestamp:
                        dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
                        dt_local = dt.astimezone(tz)
                        time_str = dt_local.strftime('%Y-%m-%d %H:%M:%S')
                    else:
                        time_str = "未知"
                    
                    response_parts.append(f"\n{i}. {time_str}")
                    response_parts.append(f"   原因: {update.get('reason', 'N/A')}")
                    response_parts.append(f"   字段: {', '.join(update.get('fields', []))}")
            else:
                response_parts.append("\n🔄 暂无更新记录")

            yield event.plain_result("\n".join(response_parts))

        except Exception as e:
            logger.error(f"查看记忆历史时发生错误: {e}", exc_info=True)
            yield event.plain_result(f"查看记忆历史时发生错误: {e}")

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
