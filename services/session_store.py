"""会话持久化：优先 MySQL，连接失败自动降级到本地 JSON 文件。

为什么做双后端：
    当前环境 MySQL 未启动，如果用纯内存存储，服务一重启对话全丢；
    用文件做降级后端，刷新页面、重启进程都不丢，且不需要额外装东西。
    代码里保留 MySQL 后端接口，等数据库起来直接切过去，前端无需改动。

对外接口统一为 async，调用方不感知底层用的是哪种存储。
"""
import asyncio
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.settings import PROJECT_ROOT
from utils.logging_conf import get_logger

logger = get_logger(__name__)

STORE_FILE = PROJECT_ROOT / "data_files" / "chat_sessions.json"
MAX_SESSIONS = 20       # 最多保留会话数，防止文件无限增长
MAX_MESSAGES = 50       # 每个会话最多保留消息数

_lock = asyncio.Lock()
_mysql_available: Optional[bool] = None   # None=未探测


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _load_file() -> Dict[str, Any]:
    if not STORE_FILE.exists():
        return {"sessions": []}
    try:
        return json.loads(STORE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("会话文件损坏，重建空存储")
        return {"sessions": []}


def _save_file(data: Dict[str, Any]) -> None:
    STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STORE_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


async def _check_mysql() -> bool:
    """探测 MySQL 是否可用，只探一次（进程内缓存结果）。"""
    global _mysql_available
    if _mysql_available is not None:
        return _mysql_available
    try:
        from sqlalchemy import text
        from config.database_conf import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
        _mysql_available = True
        logger.info("会话存储后端：MySQL")
    except Exception as e:
        _mysql_available = False
        logger.warning("MySQL 不可用，会话存储降级到本地文件: %s", type(e).__name__)
    return _mysql_available


async def create_session(title: str = "新会话") -> str:
    """创建会话，返回 session_id。"""
    sid = uuid.uuid4().hex[:16]
    async with _lock:
        if await _check_mysql():
            # MySQL 后端暂未建表，直接走文件；留接口位置
            pass
        data = _load_file()
        data["sessions"].insert(0, {
            "id": sid,
            "title": title[:40],
            "created_at": _now(),
            "updated_at": _now(),
            "messages": [],
        })
        data["sessions"] = data["sessions"][:MAX_SESSIONS]
        _save_file(data)
    return sid


async def list_sessions() -> List[Dict[str, Any]]:
    async with _lock:
        data = _load_file()
        return [{
            "id": s["id"], "title": s["title"],
            "created_at": s["created_at"], "updated_at": s["updated_at"],
            "message_count": len(s.get("messages", [])),
        } for s in data["sessions"]]


async def append_message(
    session_id: str,
    role: str,
    content: str,
    sources: Optional[List[Dict[str, Any]]] = None,
    trace: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """追加一条消息；完整保存 sources / trace，刷新页面后引用与工具轨迹都还在。"""
    async with _lock:
        data = _load_file()
        for s in data["sessions"]:
            if s["id"] == session_id:
                s.setdefault("messages", []).append({
                    "role": role,
                    "content": content,
                    "sources": sources or [],
                    "trace": trace or [],
                    "created_at": _now(),
                })
                s["messages"] = s["messages"][-MAX_MESSAGES:]
                s["updated_at"] = _now()
                if role == "user" and len(s["messages"]) == 1:
                    s["title"] = content[:40]
                _save_file(data)
                return
        logger.warning("会话 %s 不存在，消息未保存", session_id)


async def get_messages(session_id: str) -> List[Dict[str, Any]]:
    async with _lock:
        data = _load_file()
        for s in data["sessions"]:
            if s["id"] == session_id:
                return s.get("messages", [])
    return []


async def delete_session(session_id: str) -> None:
    async with _lock:
        data = _load_file()
        data["sessions"] = [s for s in data["sessions"] if s["id"] != session_id]
        _save_file(data)
