"""会话管理路由：让多轮对话刷新页面、重启服务都不丢。

前端流程：
    1. 进入 AI 页面 → GET /api/sessions/latest 拿最近会话（没有就 POST 建一个）
    2. 发消息时带 session_id
    3. 后端把 user / assistant 两条消息都写进会话
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.session_store import (
    append_message, create_session, delete_session, get_messages, list_sessions,
)
from utils.logging_conf import get_logger

router = APIRouter(prefix="/api/sessions", tags=["session"])
logger = get_logger(__name__)


class MessageIn(BaseModel):
    role: str = Field(..., pattern="^(user|assistant)$")
    content: str = Field(..., max_length=8000)
    sources: Optional[List[Dict[str, Any]]] = None
    trace: Optional[List[Dict[str, Any]]] = None


class SessionOut(BaseModel):
    session_id: str


@router.post("", response_model=SessionOut)
async def new_session(title: str = "新会话"):
    sid = await create_session(title)
    return {"session_id": sid}


@router.get("")
async def sessions():
    return {"sessions": await list_sessions()}


@router.get("/latest")
async def latest_session():
    """取最近一个会话；没有就自动创建。前端进页面时调用。"""
    items = await list_sessions()
    if items:
        return {"session_id": items[0]["id"], "messages": await get_messages(items[0]["id"])}
    sid = await create_session()
    return {"session_id": sid, "messages": []}


@router.get("/{session_id}/messages")
async def session_messages(session_id: str):
    messages = await get_messages(session_id)
    if not messages:
        # 会话存在但无消息，与会话不存在都返回空；前端不区分
        pass
    return {"session_id": session_id, "messages": messages}


@router.post("/{session_id}/messages")
async def add_message(session_id: str, msg: MessageIn):
    await append_message(
        session_id, msg.role, msg.content, msg.sources, msg.trace
    )
    return {"ok": True}


@router.delete("/{session_id}")
async def remove_session(session_id: str):
    await delete_session(session_id)
    return {"ok": True}
