"""AI 问答入口：模型密钥只存在于服务端，前端只认这个地址。

链路：参数校验 → rag_chain.prepare（检索+拼装）→ rag_chain.stream_answer（生成）
      → SSE 事件流（sources / delta / done / error）

SSE 事件是自己定义的协议，不再转发 OpenAI 原始 chunk：
    {"type":"sources","sources":[{index,news_id,title,category,score},...]}
    {"type":"delta","content":"..."}
    {"type":"done","finish_reason":"stop"|"no_evidence"}
    {"type":"error","message":"..."}
理由：引用必须在正文开始前单独送达，且来源要由检索结果反查，不能塞在文本里。
"""
import json
import time
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from config.settings import settings
from services.agent import run_agent
from services.llm import friendly_model_error
from services.rag_chain import prepare, stream_answer
from utils.logging_conf import get_logger

router = APIRouter(prefix="/api/ai", tags=["ai"])
logger = get_logger(__name__)


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(..., max_length=8000)


class ChatRequest(BaseModel):
    # 模型名不允许前端指定：否则等于把成本控制权交出去
    messages: list[ChatMessage] = Field(..., min_length=1, max_length=30)
    category: Optional[str] = Field(default=None, max_length=50, description="可选分类过滤")


def _split_messages(payload: list[dict]) -> tuple[str, list[dict]]:
    """从消息列表里取出当前问题，其余作为历史。"""
    last_user = -1
    for i, m in enumerate(payload):
        if m["role"] == "user" and m["content"].strip():
            last_user = i
    if last_user < 0:
        raise HTTPException(status_code=400, detail="消息中缺少用户问题")
    return payload[last_user]["content"].strip(), payload[:last_user]


@router.post("/chat")
async def chat(req: ChatRequest):
    payload = [m.model_dump() for m in req.messages]
    question, history = _split_messages(payload)
    logger.info(
        "chat 请求 model=%s 历史=%d 分类=%s question=%r",
        settings.ai_chat_model, len(history), req.category or "无", question[:40],
    )

    async def event_stream():
        started = time.perf_counter()
        try:
            # 放在流内部：检索或模型任一步失败都能通过 SSE 回传，而不是直接 500
            prep = await prepare(question, history, req.category)
            async for event in stream_answer(prep):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            logger.info(
                "chat 完成 cost=%.1fms 命中片段=%d 有证据=%s",
                (time.perf_counter() - started) * 1000, len(prep.hits), prep.has_evidence,
            )
        except Exception as exc:
            logger.exception("RAG 链路失败: %s", exc)
            err = json.dumps(
                {"type": "error", "message": friendly_model_error(exc)},
                ensure_ascii=False,
            )
            yield f"data: {err}\n\n"
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 关掉反向代理缓冲，否则流式会变成一次性返回
        },
    )


@router.post("/agent-chat")
async def agent_chat(req: ChatRequest):
    """Agent 模式：模型自己选工具（语义检索/分类/热度/最新），控制流交给模型。"""
    payload = [m.model_dump() for m in req.messages]
    question, history = _split_messages(payload)
    logger.info("agent-chat 请求 question=%r 历史=%d", question[:40], len(history))

    async def event_stream():
        started = time.perf_counter()
        try:
            async for event in run_agent(question, history):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            logger.info("agent-chat 完成 cost=%.1fms", (time.perf_counter() - started) * 1000)
        except Exception as exc:
            logger.exception("Agent 链路失败: %s", exc)
            err = json.dumps(
                {"type": "error", "message": friendly_model_error(exc)},
                ensure_ascii=False,
            )
            yield f"data: {err}\n\n"
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
