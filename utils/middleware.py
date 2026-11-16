"""HTTP 中间件：注入 request_id、记录耗时与异常。

注意（SSE 场景）：BaseHTTPMiddleware 的 call_next 在响应头返回时就结束，
流式响应的正文是在之后异步发送的，所以下面统计的 cost_ms 对 /api/ai/chat
这类接口只代表"首字节时间"，不是完整生成耗时。完整耗时应在业务层
（services/rag_chain.py）单独记录。
"""
import time
import uuid

from fastapi import Request

from utils.logging_conf import get_logger, request_id_var

logger = get_logger("http")


async def request_context_middleware(request: Request, call_next):
    rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    token = request_id_var.set(rid)
    start = time.perf_counter()

    try:
        response = await call_next(request)
    except Exception:
        cost_ms = (time.perf_counter() - start) * 1000
        logger.exception(
            "%s %s 异常 cost=%.1fms", request.method, request.url.path, cost_ms
        )
        raise
    else:
        cost_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "%s %s -> %s cost=%.1fms",
            request.method, request.url.path, response.status_code, cost_ms,
        )
        response.headers["X-Request-ID"] = rid
        return response
    finally:
        # 必须 reset，否则连接池里的 Task 复用时会残留上一个请求的 id
        request_id_var.reset(token)
