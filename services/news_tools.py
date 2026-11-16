"""Agent 可调用的新闻工具集：4 个工具 + 统一结果结构。

数据源说明：
    结构化工具（分类/热度/最新）读 data_files/news_seed.json，不依赖 MySQL——
    当前 MySQL 未启动，用它保证 Agent 离线也能演示。
    语义检索工具仍走 Chroma 向量库。
    每个工具返回 news_id，供前端跳详情页 & 引用溯源。
"""
import json
from dataclasses import dataclass, asdict
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.settings import PROJECT_ROOT, settings
from services.embedding import embed_query
from services.retriever import get_collection, retrieve
from utils.logging_conf import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class ToolResult:
    """工具执行结果：summary 给模型看，items 给前端展示/引用溯源。"""
    tool: str
    summary: str
    items: List[Dict[str, Any]]

    def to_payload(self) -> Dict[str, Any]:
        return asdict(self)


@lru_cache(maxsize=1)
def _load_news() -> List[Dict[str, Any]]:
    """读种子文件，给每条补上/news_id（文件序号，与 file 源建的向量索引一致）。"""
    path = PROJECT_ROOT / "data_files" / "news_seed.json"
    rows = json.loads(path.read_text(encoding="utf-8"))["rows"]
    out = []
    for i, r in enumerate(rows, start=1):
        out.append({
            "news_id": i,
            "title": r.get("title") or "",
            "category": r.get("category_name") or "",
            "views": int(r.get("views") or 0),
            "publish_time": r.get("publish_time") or "",
            "description": r.get("description") or "",
            "content": r.get("content") or "",
        })
    return out


def _brief(item: Dict[str, Any]) -> Dict[str, Any]:
    """给前端/引用用的精简字段，不把整篇正文塞进去。"""
    return {
        "news_id": item["news_id"],
        "title": item["title"],
        "category": item["category"],
        "views": item["views"],
        "publish_time": item["publish_time"],
    }


def _fmt(items: List[Dict[str, Any]]) -> str:
    """给模型看的紧凑摘要：编号 + 标题 + 关键字段。"""
    lines = []
    for i, it in enumerate(items, start=1):
        line = f"[{i}] news_id={it['news_id']}《{it['title']}」({it['category']}"
        if "views" in it:
            line += f"，浏览{it['views']}"
        if it.get("publish_time"):
            line += f"，{it['publish_time']}"
        line += ")"
        lines.append(line)
    return "\n".join(lines) if lines else "（无匹配新闻）"


# ---------- 工具 1：语义检索（已有 RAG 链路的核心） ----------
async def search_news(query: str, top_k: Optional[int] = None) -> ToolResult:
    k = top_k or settings.rag_top_k
    hits = await retrieve(query, top_k=k)
    items = [{
        "news_id": h.news_id,
        "title": h.title,
        "category": h.category,
        "score": h.score,
        "snippet": h.text[:300],
    } for h in hits]
    return ToolResult(
        tool="search_news",
        summary=f"语义检索「{query}」，命中 {len(items)} 条：\n" + _fmt(items),
        items=items,
    )


# ---------- 工具 2：按分类筛选 ----------
def filter_by_category(category: str, limit: int = 5) -> ToolResult:
    news = _load_news()
    matched = [r for r in news if category in r["category"]]
    # 同分类内按浏览量排，保证返回的是该分类的代表性新闻
    matched.sort(key=lambda r: r["views"], reverse=True)
    picked = matched[:max(1, min(limit, 20))]
    return ToolResult(
        tool="filter_by_category",
        summary=f"分类「{category}」共 {len(matched)} 篇，按热度取前 {len(picked)}：\n" + _fmt(picked),
        items=[_brief(x) for x in picked],
    )


# ---------- 工具 3：热度排行 ----------
def get_hot_news(category: Optional[str] = None, limit: int = 5) -> ToolResult:
    news = _load_news()
    pool = [r for r in news if not category or category in r["category"]]
    pool.sort(key=lambda r: r["views"], reverse=True)
    picked = pool[:max(1, min(limit, 20))]
    scope = f"分类「{category}」" if category else "全站"
    return ToolResult(
        tool="get_hot_news",
        summary=f"{scope}浏览量最高 {len(picked)} 篇：\n" + _fmt(picked),
        items=[_brief(x) for x in picked],
    )


# ---------- 工具 4：最新资讯 ----------
def get_latest_news(category: Optional[str] = None, limit: int = 5) -> ToolResult:
    news = _load_news()
    pool = [r for r in news if not category or category in r["category"]]
    pool.sort(key=lambda r: r["publish_time"], reverse=True)
    picked = pool[:max(1, min(limit, 20))]
    scope = f"分类「{category}」" if category else "全站"
    return ToolResult(
        tool="get_latest_news",
        summary=f"{scope}最新发布 {len(picked)} 篇：\n" + _fmt(picked),
        items=[_brief(x) for x in picked],
    )


# OpenAI function calling 的 tools schema
TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_news",
            "description": "按语义在新闻库中检索相关内容。用于回答具体事件、事实、进展类问题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "要检索的问题或关键词"},
                    "top_k": {"type": "integer", "description": "返回条数，默认5，最多10", "minimum": 1, "maximum": 10},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "filter_by_category",
            "description": "按分类浏览新闻。用于「体育类有哪些新闻」「教育分类」这类问题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "分类名，如 科技/体育/财经/国际/社会/娱乐/健康/教育"},
                    "limit": {"type": "integer", "description": "返回条数，默认5，最多20", "minimum": 1, "maximum": 20},
                },
                "required": ["category"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_hot_news",
            "description": "按浏览量排序取热门新闻。用于「最火的新闻」「热度排行」类问题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "可选分类过滤，不传则全站"},
                    "limit": {"type": "integer", "description": "返回条数，默认5，最多20", "minimum": 1, "maximum": 20},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_latest_news",
            "description": "按发布时间排序取最新新闻。用于「最近有什么新闻」「最新发布」类问题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "可选分类过滤，不传则全站"},
                    "limit": {"type": "integer", "description": "返回条数，默认5，最多20", "minimum": 1, "maximum": 20},
                },
            },
        },
    },
]

TOOL_DISPATCH = {
    "search_news": lambda **kw: search_news(**kw),       # async
    "filter_by_category": filter_by_category,
    "get_hot_news": get_hot_news,
    "get_latest_news": get_latest_news,
}
