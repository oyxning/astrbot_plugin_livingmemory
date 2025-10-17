# -*- coding: utf-8 -*-
"""
server.py - LivingMemory WebUI backend
提供基于 FastAPI 的安全管理面板，实现记忆查询、筛选、详情与批量删除能力。
"""

import asyncio
import json
import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, TYPE_CHECKING

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from astrbot.api import logger

from ..storage.memory_repository import MemoryRepository

if TYPE_CHECKING:
    from ..storage.faiss_manager import FaissManager
    from ..main import SessionManager


class WebUIServer:
    """
    负责启动与管理 LivingMemory WebUI 服务的帮助类。
    """

    def __init__(
        self,
        config: Dict[str, Any],
        faiss_manager: "FaissManager",
        session_manager: Optional["SessionManager"] = None,
    ):
        self.config = config
        self.faiss_manager = faiss_manager
        self.session_manager = session_manager

        self.host = str(config.get("host", "127.0.0.1"))
        self.port = int(config.get("port", 8080))
        self.session_timeout = max(60, int(config.get("session_timeout", 3600)))
        self._access_password = str(config.get("access_password", "")).strip()

        self._tokens: Dict[str, float] = {}
        self._token_lock = asyncio.Lock()
        self._server: Optional[uvicorn.Server] = None
        self._server_task: Optional[asyncio.Task] = None

        self.repository: Optional[MemoryRepository] = None

        self._app = FastAPI(title="LivingMemory 控制台", version="1.1.0")
        self._setup_routes()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(self):
        """
        启动 WebUI 服务。
        """
        if self._server_task and not self._server_task.done():
            logger.warning("WebUI 服务已经在运行")
            return

        self._ensure_repository()

        config = uvicorn.Config(
            app=self._app,
            host=self.host,
            port=self.port,
            log_level="info",
            loop="asyncio",
            lifespan="on",
        )
        self._server = uvicorn.Server(config)
        self._server_task = asyncio.create_task(self._server.serve())

        for _ in range(50):
            if getattr(self._server, "started", False):
                logger.info(f"WebUI 已启动: http://{self.host}:{self.port}")
                return
            if self._server_task.done():
                error = self._server_task.exception()
                raise RuntimeError(f"WebUI 启动失败: {error}") from error
            await asyncio.sleep(0.1)

        logger.warning("WebUI 启动耗时较长，仍在后台启动中")

    async def stop(self):
        """
        停止 WebUI 服务。
        """
        if self._server:
            self._server.should_exit = True
        if self._server_task:
            await self._server_task
        self._server = None
        self._server_task = None
        logger.info("WebUI 已停止")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_repository(self):
        if not self.repository:
            self.repository = MemoryRepository(self.faiss_manager)

    def _setup_routes(self):
        """
        初始化 FastAPI 路由与静态资源。
        """
        static_dir = Path(__file__).resolve().parent.parent / "static"
        index_path = static_dir / "index.html"
        if not index_path.exists():
            logger.warning("未找到 WebUI 前端文件，静态资源目录为空")

        self._app.add_middleware(
            CORSMiddleware,
            allow_origins=[
                f"http://{self.host}:{self.port}",
                "http://localhost",
                "http://127.0.0.1",
            ],
            allow_methods=["*"],
            allow_headers=["*"],
            allow_credentials=True,
        )

        self._app.mount("/static", StaticFiles(directory=static_dir), name="static")

        @self._app.get("/", response_class=HTMLResponse)
        async def serve_index():
            if not index_path.exists():
                raise HTTPException(status.HTTP_404_NOT_FOUND, detail="前端文件缺失")
            return HTMLResponse(index_path.read_text(encoding="utf-8"))

        @self._app.post("/api/login")
        async def login(payload: Dict[str, Any]):
            password = str(payload.get("password", "")).strip()
            if not password:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="密码不能为空")
            if password != self._access_password:
                await asyncio.sleep(0.3)
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="认证失败")

            token = secrets.token_urlsafe(32)
            expires_at = time.time() + self.session_timeout
            async with self._token_lock:
                await self._cleanup_tokens_locked()
                self._tokens[token] = expires_at

            return {"token": token, "expires_in": self.session_timeout}

        @self._app.post("/api/logout")
        async def logout(token: str = Depends(self._auth_dependency())):
            async with self._token_lock:
                self._tokens.pop(token, None)
            return {"detail": "已退出登录"}

        @self._app.get("/api/memories")
        async def list_memories(
            request: Request,
            token: str = Depends(self._auth_dependency()),
        ):
            self._ensure_repository()
            query = request.query_params
            keyword = query.get("keyword", "").strip()
            status_filter = query.get("status", "all").strip() or "all"
            load_all = query.get("all", "false").lower() == "true"

            if load_all:
                page = 1
                page_size = 0
                offset = 0
            else:
                page = max(1, int(query.get("page", 1)))
                page_size = query.get("page_size")
                page_size = min(200, max(1, int(page_size))) if page_size else 50
                offset = (page - 1) * page_size

            repository = self._ensure_and_get_repository()
            try:
                total = await repository.count_memories(
                    status=status_filter, keyword=keyword
                )
                records = await repository.list_memories(
                    limit=0 if load_all else page_size,
                    offset=offset,
                    status=status_filter,
                    keyword=keyword,
                )
            except Exception as exc:
                logger.error(f"获取记忆列表失败: {exc}", exc_info=True)
                raise HTTPException(
                    status.HTTP_500_INTERNAL_SERVER_ERROR, detail="读取记忆失败"
                ) from exc

            items = [self._format_memory(record) for record in records]
            has_more = False if load_all else offset + len(items) < total

            return {
                "items": items,
                "page": page,
                "page_size": page_size if not load_all else len(items),
                "total": total,
                "has_more": has_more,
            }

        @self._app.get("/api/memories/{identifier}")
        async def memory_detail(
            identifier: str, token: str = Depends(self._auth_dependency())
        ):
            repository = self._ensure_and_get_repository()
            record = await repository.get_memory(identifier)
            if not record:
                raise HTTPException(status.HTTP_404_NOT_FOUND, detail="未找到记忆记录")
            return self._format_memory(record)

        @self._app.delete("/api/memories")
        async def delete_memories(
            payload: Dict[str, Any],
            token: str = Depends(self._auth_dependency()),
        ):
            repository = self._ensure_and_get_repository()
            doc_ids = payload.get("doc_ids") or payload.get("ids") or []
            memory_ids = payload.get("memory_ids") or []

            ids_to_delete = set()

            for doc_id in doc_ids:
                try:
                    ids_to_delete.add(int(doc_id))
                except (TypeError, ValueError):
                    continue

            for memory_id in memory_ids:
                record = await repository.get_memory(str(memory_id))
                if record:
                    ids_to_delete.add(int(record["id"]))

            if not ids_to_delete:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST, detail="需要提供待删除的记忆 ID"
                )

            try:
                await self.faiss_manager.delete_memories(sorted(ids_to_delete))
            except Exception as exc:
                logger.error(f"删除记忆失败: {exc}", exc_info=True)
                raise HTTPException(
                    status.HTTP_500_INTERNAL_SERVER_ERROR, detail="删除记忆失败"
                ) from exc

            return {"deleted_doc_count": len(ids_to_delete)}

        @self._app.get("/api/stats")
        async def stats(token: str = Depends(self._auth_dependency())):
            repository = self._ensure_and_get_repository()
            total = await repository.count_memories()
            status_counts = await repository.count_by_status()
            active_sessions = (
                self.session_manager.get_session_count()
                if self.session_manager
                else 0
            )

            return {
                "total_memories": total,
                "status_breakdown": status_counts,
                "active_sessions": active_sessions,
                "session_timeout": self.session_timeout,
            }

        @self._app.get("/api/health")
        async def health():
            return {"status": "ok"}

    def _ensure_and_get_repository(self) -> MemoryRepository:
        self._ensure_repository()
        assert self.repository is not None  # for mypy/static hints
        return self.repository

    def _auth_dependency(self):
        async def dependency(request: Request) -> str:
            token = self._extract_token(request)
            if not token:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="未授权")
            await self._validate_token(token)
            return token

        return dependency

    async def _validate_token(self, token: str):
        async with self._token_lock:
            await self._cleanup_tokens_locked()
            expiry = self._tokens.get(token)
            if not expiry:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="会话已失效")
            if expiry < time.time():
                self._tokens.pop(token, None)
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="会话已过期")
            self._tokens[token] = time.time() + self.session_timeout

    async def _cleanup_tokens_locked(self):
        now = time.time()
        expired = [token for token, expiry in self._tokens.items() if expiry < now]
        for token in expired:
            self._tokens.pop(token, None)

    def _extract_token(self, request: Request) -> str:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            return auth_header[7:].strip()
        cookie_token = request.cookies.get("auth_token")
        if cookie_token:
            return cookie_token.strip()
        custom_header = request.headers.get("X-Auth-Token", "")
        return custom_header.strip()

    def _format_memory(self, record: Dict[str, Any]) -> Dict[str, Any]:
        metadata = record.get("metadata", {}) or {}
        content = record.get("content") or metadata.get("memory_content") or ""
        memory_id = metadata.get("memory_id") or record.get("doc_uuid")
        event_type = metadata.get("event_type")
        status = metadata.get("status", "active")
        importance = metadata.get("importance")
        created_at = metadata.get("create_time") or record.get("created_at")
        last_access = (
            metadata.get("last_access_time")
            or metadata.get("last_updated_time")
            or record.get("updated_at")
        )

        preview = content.replace("\n", " ").strip()
        preview_short = preview if len(preview) <= 140 else f"{preview[:140]}…"

        return {
            "doc_id": record.get("id"),
            "doc_uuid": record.get("doc_uuid"),
            "memory_id": memory_id,
            "summary": preview_short or "(空内容)",
            "full_content": content,
            "memory_type": event_type,
            "importance": importance,
            "status": status,
            "created_at": self._format_timestamp(created_at),
            "last_access": self._format_timestamp(last_access),
            "source": "vector_store",
            "metadata": metadata,
            "raw": {
                "content": content,
                "metadata": metadata,
            },
            "raw_json": json.dumps(
                {"content": content, "metadata": metadata},
                ensure_ascii=False,
                indent=2,
            ),
        }

    @staticmethod
    def _format_timestamp(value: Any) -> Optional[str]:
        if not value:
            return None
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value).isoformat(sep=" ", timespec="seconds")
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat(
                    sep=" ", timespec="seconds"
                )
            except ValueError:
                return value
        return str(value)
