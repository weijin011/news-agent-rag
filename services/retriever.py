"""Chroma 向量检索。

一个容易踩的坑：Chroma 集合默认距离是 l2（欧氏），不是余弦。
创建集合时必须显式写 metadata={"hnsw:space": "cosine"}，
否则下面的 score = 1 - distance 就没有意义，阈值也全废了。
"""
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import chromadb
from chromadb.config import Settings as ChromaSettings

from config.settings import settings
from services.embedding import embed_query
from utils.logging_conf import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class Hit:
    """一条命中片段。news_id 用于前端跳新闻详情，不依赖模型生成的内容。"""
    chunk_id: str
    news_id: int
    title: str
    category: str
    text: str
    score: float


_collection = None


def get_collection():
    global _collection
    if _collection is None:
        client = chromadb.PersistentClient(
            path=settings.chroma_persist_dir,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        _collection = client.get_or_create_collection(
            name=settings.chroma_collection,
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


def reset_collection():
    """--reset 时调用：删掉旧集合，确保用正确的距离度量重建。"""
    global _collection
    client = chromadb.PersistentClient(
        path=settings.chroma_persist_dir,
        settings=ChromaSettings(anonymized_telemetry=False),
    )
    # list_collections() 在不同版本返回 str 或 Collection 对象，做兼容；
    # 集合本来不存在时直接跳过，否则第一次 --reset 会抛 NotFoundError
    existing = {
        c if isinstance(c, str) else getattr(c, "name", "")
        for c in client.list_collections()
    }
    if settings.chroma_collection in existing:
        client.delete_collection(name=settings.chroma_collection)
    _collection = None
    return get_collection()


def upsert_chunks(
    ids: List[str],
    texts: List[str],
    metadatas: List[Dict[str, Any]],
    embeddings: List[List[float]],
) -> None:
    get_collection().upsert(
        ids=ids, documents=texts, metadatas=metadatas, embeddings=embeddings
    )


def delete_news(news_id: int) -> None:
    """幂等建库：先删该新闻的旧片段，避免重复插入。"""
    get_collection().delete(where={"news_id": news_id})


def count_chunks() -> int:
    return get_collection().count()


async def retrieve(
    query: str,
    top_k: Optional[int] = None,
    threshold: Optional[float] = None,
    category: Optional[str] = None,
) -> List[Hit]:
    """语义检索 + 相似度阈值过滤 + 可选分类过滤。"""
    k = top_k or settings.rag_top_k
    th = settings.rag_score_threshold if threshold is None else threshold

    vector = await embed_query(query)
    kwargs: Dict[str, Any] = {}
    if category:
        kwargs["where"] = {"category": category}

    raw = get_collection().query(
        query_embeddings=[vector],
        n_results=k,
        include=["documents", "metadatas", "distances"],
        **kwargs,
    )

    docs = raw["documents"][0] if raw.get("documents") else []
    metas = raw["metadatas"][0] if raw.get("metadatas") else []
    dists = raw["distances"][0] if raw.get("distances") else []

    hits: List[Hit] = []
    for doc, meta, dist in zip(docs, metas, dists):
        score = 1.0 - float(dist)  # cosine distance = 1 - cosine similarity
        if score < th:
            continue
        hits.append(Hit(
            chunk_id=str(meta.get("chunk_id", "")),
            news_id=int(meta.get("news_id", 0)),
            title=str(meta.get("title", "")),
            category=str(meta.get("category", "")),
            text=str(doc),
            score=round(score, 4),
        ))
    return hits
