"""
server.py - LivingMemory 外部 API 服务
基于 FastAPI 提供受 API Key 保护的外部调用接口，供第三方平台（如写书平台）
获取指定 ID 的记忆内容。

API 端点说明:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  GET    /api/v1/health                      - 健康检查（无需认证）
  GET    /api/v1/memories/{memory_id}        - 按 ID 获取单条记忆
  POST   /api/v1/memories/batch              - 批量获取多条记忆
  POST   /api/v1/memories/search             - 按关键词搜索记忆

认证方式（二选一）:
  - Header: X-API-Key: <your_api_key>
  - Header: Authorization: Bearer <your_api_key>

安全特性:
  - API Key 认证（必须）
  - IP 请求频率限制（60秒内最多30次）
  - 批量查询数量上限
"""

import asyncio
import secrets
import time
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from astrbot.api import logger


class ExternalAPIServer:
    """
    外部 API 服务器 — 为第三方平台提供记忆读取接口

    使用独立的 FastAPI 实例和端口，与 WebUI 完全隔离。
    调用方必须携带 API Key 才能访问记忆数据。
    """

    def __init__(
        self,
        memory_engine,
        config: dict[str, Any],
    ):
        """
        初始化外部 API 服务器

        Args:
            memory_engine: MemoryEngine 实例
            config: 外部 API 配置字典，包含:
                - enabled: 是否启用
                - host: 监听地址
                - port: 监听端口
                - api_key: API 密钥
                - max_batch_size: 单次批量查询上限
        """
        self.memory_engine = memory_engine
        self.config = config

        self.host = str(config.get("host", "127.0.0.1"))
        self.port = int(config.get("port", 8889))
        self.max_batch_size = max(1, min(int(config.get("max_batch_size", 100)), 1000))

        # API Key 管理
        self._api_key = str(config.get("api_key", "")).strip()
        self._key_generated = False
        if not self._api_key:
            self._api_key = secrets.token_urlsafe(32)
            self._key_generated = True
            logger.info(
                "外部 API 未设置密钥，已自动生成随机密钥: %s",
                self._api_key,
            )

        # 请求频率限制（IP 级别）
        self._rate_limit_window = 60  # 秒
        self._rate_limit_max = 30     # 窗口内最大请求数
        self._request_logs: dict[str, list[float]] = {}
        self._rate_lock = asyncio.Lock()

        self._server: uvicorn.Server | None = None
        self._server_task: asyncio.Task | None = None
        self._cleanup_task: asyncio.Task | None = None

        self._app = FastAPI(title="LivingMemory External API", version="1.0.0")
        self._setup_routes()

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    async def start(self):
        """启动外部 API 服务"""
        if self._server_task and not self._server_task.done():
            logger.warning("外部 API 服务已经在运行")
            return

        config = uvicorn.Config(
            app=self._app,
            host=self.host,
            port=self.port,
            log_level="warning",  # 外部 API 减少日志噪音
            loop="asyncio",
            lifespan="on",
        )
        self._server = uvicorn.Server(config)
        self._server_task = asyncio.create_task(self._server.serve())

        # 启动定期清理任务
        self._cleanup_task = asyncio.create_task(self._periodic_cleanup())

        # 等待服务启动
        for _ in range(50):
            if getattr(self._server, "started", False):
                logger.info(f"外部 API 已启动: http://{self.host}:{self.port}")
                if self._key_generated:
                    logger.info(
                        "外部 API 自动生成的密钥: %s（请妥善保存）", self._api_key
                    )
                return
            if self._server_task.done():
                error = self._server_task.exception()
                raise RuntimeError(f"外部 API 启动失败: {error}") from error
            await asyncio.sleep(0.1)

        logger.warning("外部 API 启动耗时较长，仍在后台启动中")

    async def stop(self):
        """停止外部 API 服务"""
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        if self._server:
            self._server.should_exit = True
        if self._server_task:
            await self._server_task
        self._server = None
        self._server_task = None
        self._cleanup_task = None
        logger.info("外部 API 已停止")

    # ------------------------------------------------------------------
    # 认证 & 限流
    # ------------------------------------------------------------------

    async def _authenticate(self, request: Request) -> None:
        """
        验证 API Key

        支持两种携带方式：
        1. X-API-Key: <key>
        2. Authorization: Bearer <key>
        """
        provided_key = request.headers.get("X-Api-Key", "").strip()
        if not provided_key:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                provided_key = auth_header[7:].strip()

        if not provided_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="缺少 API Key。请在 X-API-Key 或 Authorization: Bearer 头中提供。",
            )

        if provided_key != self._api_key:
            # 延迟响应防暴力破解
            await asyncio.sleep(0.5)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="API Key 无效，访问被拒绝。",
            )

    async def _check_rate_limit(self, client_ip: str) -> None:
        """检查 IP 级别请求频率限制"""
        async with self._rate_lock:
            now = time.time()
            requests = self._request_logs.get(client_ip, [])
            # 清理窗口外的旧记录
            cutoff = now - self._rate_limit_window
            requests = [t for t in requests if t > cutoff]
            self._request_logs[client_ip] = requests

            if len(requests) >= self._rate_limit_max:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"请求过于频繁，请 {self._rate_limit_window} 秒后再试。",
                )

            requests.append(now)

    async def _periodic_cleanup(self):
        """定期清理过期的速率限制记录"""
        while True:
            try:
                await asyncio.sleep(300)
                async with self._rate_lock:
                    now = time.time()
                    cutoff = now - self._rate_limit_window * 2
                    expired = [
                        ip
                        for ip, logs in self._request_logs.items()
                        if not any(t > cutoff for t in logs)
                    ]
                    for ip in expired:
                        del self._request_logs[ip]
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"外部 API 定期清理异常: {e}")

    # ------------------------------------------------------------------
    # 路由注册
    # ------------------------------------------------------------------

    def _setup_routes(self):
        """初始化 FastAPI 路由"""

        # CORS — 允许外部平台调用
        self._app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type", "Authorization", "X-Api-Key"],
            allow_credentials=True,
        )

        # === 健康检查（无需认证）===

        @self._app.get("/api/v1/health")
        async def health():
            return {"status": "ok", "version": "1.0.0", "service": "LivingMemory External API"}

        # === 按 ID 获取单条记忆 ===

        @self._app.get("/api/v1/memories/{memory_id}")
        async def get_memory_by_id(
            memory_id: int,
            request: Request,
        ):
            """获取指定 ID 的记忆内容"""
            await self._authenticate(request)
            await self._check_rate_limit(self._client_ip(request))

            try:
                memory = await self.memory_engine.get_memory(memory_id)
                if not memory:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"记忆 {memory_id} 不存在",
                    )

                return {
                    "success": True,
                    "data": {
                        "id": memory.get("id"),
                        "text": memory.get("text"),
                        "metadata": memory.get("metadata"),
                    },
                }
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"[外部API] 获取记忆失败 (id={memory_id}): {e}", exc_info=True)
                return JSONResponse(
                    status_code=500,
                    content={"success": False, "error": f"内部错误: {str(e)}"},
                )

        # === 批量获取记忆 ===

        @self._app.post("/api/v1/memories/batch")
        async def batch_get_memories(
            payload: dict[str, Any],
            request: Request,
        ):
            """
            批量获取记忆

            请求体: {"memory_ids": [1, 2, 3]}
            返回每个 ID 对应的记忆（不存在的 ID 返回 null）。
            """
            await self._authenticate(request)
            await self._check_rate_limit(self._client_ip(request))

            memory_ids = payload.get("memory_ids", [])
            if not isinstance(memory_ids, list) or not memory_ids:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="请提供 memory_ids 列表",
                )

            if len(memory_ids) > self.max_batch_size:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"单次最多查询 {self.max_batch_size} 条记忆",
                )

            # 去重并验证
            unique_ids: list[int] = []
            seen: set[int] = set()
            for mid in memory_ids:
                try:
                    mid_int = int(mid)
                    if mid_int not in seen:
                        seen.add(mid_int)
                        unique_ids.append(mid_int)
                except (TypeError, ValueError):
                    continue

            if not unique_ids:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="memory_ids 中没有有效的整数 ID",
                )

            try:
                memories: dict[int, dict[str, Any] | None] = {}
                for mid in unique_ids:
                    memory = await self.memory_engine.get_memory(mid)
                    if memory:
                        memories[mid] = {
                            "id": memory.get("id"),
                            "text": memory.get("text"),
                            "metadata": memory.get("metadata"),
                        }
                    else:
                        memories[mid] = None

                return {
                    "success": True,
                    "data": {
                        "memories": memories,
                        "requested_count": len(memory_ids),
                        "found_count": sum(1 for v in memories.values() if v is not None),
                    },
                }
            except Exception as e:
                logger.error(f"[外部API] 批量获取记忆失败: {e}", exc_info=True)
                return JSONResponse(
                    status_code=500,
                    content={"success": False, "error": f"内部错误: {str(e)}"},
                )

        # === 搜索记忆 ===

        @self._app.post("/api/v1/memories/search")
        async def search_memories(
            payload: dict[str, Any],
            request: Request,
        ):
            """
            搜索记忆

            请求体: {"query": "...", "k": 5, "session_id": "..."}
            """
            await self._authenticate(request)
            await self._check_rate_limit(self._client_ip(request))

            query = str(payload.get("query", "")).strip()
            if not query:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="查询内容不能为空",
                )

            k = min(50, max(1, int(payload.get("k", 10))))
            session_id = payload.get("session_id")
            persona_id = payload.get("persona_id")

            try:
                results = await self.memory_engine.search_memories(
                    query=query,
                    k=k,
                    session_id=session_id,
                    persona_id=persona_id,
                )

                formatted = []
                for result in results:
                    formatted.append(
                        {
                            "id": result.doc_id,
                            "content": result.content,
                            "score": round(float(result.final_score), 6),
                            "metadata": result.metadata,
                        }
                    )

                return {
                    "success": True,
                    "data": {
                        "items": formatted,
                        "total": len(formatted),
                    },
                }
            except Exception as e:
                logger.error(f"[外部API] 搜索记忆失败: {e}", exc_info=True)
                return JSONResponse(
                    status_code=500,
                    content={"success": False, "error": f"内部错误: {str(e)}"},
                )

    @staticmethod
    def _client_ip(request: Request) -> str:
        """从请求中提取客户端 IP"""
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        if request.client and request.client.host:
            return request.client.host
        return "unknown"
