"""向量化：入库与查询必须走同一个模型，否则向量空间不一致，检索必然失效。"""
from typing import List

from config.settings import settings
from services.llm import get_embedding_client
from utils.logging_conf import get_logger

logger = get_logger(__name__)


async def embed_texts(texts: List[str]) -> List[List[float]]:
    """批量向量化，按输入顺序返回。分批是为了绕开服务端的单次条数上限。"""
    if not texts:
        return []

    client = get_embedding_client()
    batch_size = max(1, settings.embedding_batch_size)
    result: List[List[float]] = []

    for start in range(0, len(texts), batch_size):
        part = texts[start:start + batch_size]
        resp = await client.embeddings.create(
            model=settings.ai_embedding_model,
            input=part,
        )
        # 显式按 index 排序：兼容实现不保证返回顺序与输入一致
        items = sorted(resp.data, key=lambda d: getattr(d, "index", 0))
        result.extend(item.embedding for item in items)
        logger.info("向量化 %d/%d", min(start + batch_size, len(texts)), len(texts))

    if len(result) != len(texts):
        raise RuntimeError(
            f"向量化返回条数不符：期望 {len(texts)}，实际 {len(result)}"
        )
    return result


async def embed_query(text: str) -> List[float]:
    return (await embed_texts([text]))[0]
