"""OpenAI 兼容客户端单例。

生成（DeepSeek）与向量化（DashScope）各自独立客户端：
DeepSeek 不提供 embeddings 接口，且已建好的索引必须用原 embedding 模型，
所以两套服务分开配置，换生成模型不会影响向量空间。
"""
from functools import lru_cache

from openai import AsyncOpenAI

from config.settings import settings
from utils.logging_conf import get_logger

logger = get_logger(__name__)


@lru_cache(maxsize=1)
def get_client() -> AsyncOpenAI:
    """生成模型客户端（DeepSeek），懒加载单例。"""
    logger.info("初始化生成客户端 base_url=%s", settings.ai_base_url)
    return AsyncOpenAI(
        api_key=settings.require_ai_key(),
        base_url=settings.ai_base_url,
        timeout=settings.ai_timeout,
        max_retries=settings.ai_max_retries,
    )


@lru_cache(maxsize=1)
def get_embedding_client() -> AsyncOpenAI:
    """向量化客户端（DashScope），与生成服务分开，互不干扰。

    换生成模型绝不换 embedding 模型，否则 242 条已建好的向量索引
    全部对不上，检索直接失效。
    """
    headers = {}
    if "dashscope" in settings.embedding_base_url:
        headers["X-DashScope-SSE"] = "enable"
    logger.info("初始化向量客户端 base_url=%s", settings.embedding_base_url)
    return AsyncOpenAI(
        api_key=settings.require_embedding_key(),
        base_url=settings.embedding_base_url,
        timeout=settings.ai_timeout,
        max_retries=settings.ai_max_retries,
        default_headers=headers or None,
    )


def friendly_model_error(exc: Exception) -> str:
    """把模型服务的原始异常翻译成用户能照着做的提示。

    前端和评测脚本共用这一处映射，避免出现 PermissionDeniedError 这种
    用户看不懂、也不知道该怎么办的英文类名。
    """
    msg = str(exc)
    low = msg.lower()
    if "403" in msg or "free quota" in low or "quota exhausted" in low:
        return "模型服务额度已用尽或无权限，请到对应服务商控制台检查余额与付费设置"
    if "429" in msg or "rate limit" in low:
        return "模型服务请求过于频繁，请稍后再试"
    if "401" in msg or "invalid api key" in low or "authentication" in low:
        return "模型服务密钥无效或已过期，请检查 .env 里的 AI_API_KEY"
    if "404" in msg or "model not exist" in low:
        return "配置的模型名不存在，请检查 .env 里的 AI_CHAT_MODEL"
    return f"服务暂时不可用：{type(exc).__name__}"
