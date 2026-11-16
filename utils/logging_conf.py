"""结构化日志（零第三方依赖）。

为什么用 contextvars 而不是全局变量：
    FastAPI 是单进程并发处理请求的，用普通全局变量存 request_id，
    A 请求的日志会被 B 请求覆盖。ContextVar 为每个 asyncio Task 维护
    独立副本，这是 async 编程里传请求上下文的正确方式（面试常问）。

输出格式：
    2026-11-11 19:50:00 | INFO    | a1b2c3d4e5f6 | http:28 | GET /api/news/list -> 200 12.3ms
"""
import logging
import sys
from contextvars import ContextVar

# 每次请求唯一；默认值 "-" 表示日志来自后台脚本而非 HTTP 请求
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    """把当前协程的 request_id 注入每条 LogRecord。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


_configured = False


def setup_logging(level: int = logging.INFO) -> None:
    """应用启动时调用一次。重复调用不会重复添加 handler。"""
    global _configured
    if _configured:
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s | %(levelname)-7s | %(request_id)s | %(name)s:%(lineno)d | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    handler.addFilter(RequestIdFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # 让 uvicorn 的日志走同一套格式，不重复输出
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv = logging.getLogger(name)
        uv.handlers.clear()
        uv.propagate = True

    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def new_request_id() -> str:
    """给离线脚本/批处理生成 trace id，便于把一次建库的所有日志串起来。"""
    import uuid
    return uuid.uuid4().hex[:12]
