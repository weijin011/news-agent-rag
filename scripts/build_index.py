"""离线建库：把新闻切块 → 向量化 → 写入 Chroma。

运行（在项目根目录）：
    python -m scripts.build_index                       # 用 data_files/news_seed.json
    python -m scripts.build_index --source mysql        # 用 MySQL 里的真实新闻
    python -m scripts.build_index --reset --limit 20    # 重建索引，只跑 20 篇（省钱调试）

幂等：每篇新闻写入前先删它的旧片段，重复执行不会产生重复数据。
注意：--source file 时 seed 文件里没有主键，news_id 用文件内序号，
      仅用于本地验证检索质量；接 MySQL 后会换成真实主键，引用才能跳对详情页。
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

# 兼容 `python scripts/build_index.py` 直接运行的场景
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import PROJECT_ROOT, settings
from services.chunking import build_chunks
from services.embedding import embed_texts
from services.retriever import count_chunks, delete_news, reset_collection, upsert_chunks
from utils.logging_conf import (
    get_logger, new_request_id, request_id_var, setup_logging,
)

logger = get_logger("build_index")


def load_from_file(path: str) -> List[Dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = data["rows"] if isinstance(data, dict) else data
    out = []
    for i, row in enumerate(rows, start=1):
        content = (row.get("content") or row.get("description") or "").strip()
        if not content:
            continue
        out.append({
            "news_id": int(row.get("id") or i),
            "title": row.get("title") or "",
            "content": content,
            "category": row.get("category_name") or row.get("category") or "",
        })
    return out


async def load_from_mysql() -> List[Dict[str, Any]]:
    from sqlalchemy import select

    from config.database_conf import AsyncSessionLocal
    from models.news import Category, News

    async with AsyncSessionLocal() as db:
        stmt = (
            select(News.id, News.title, News.content, Category.name)
            .join(Category, News.category_id == Category.id)
        )
        rows = (await db.execute(stmt)).all()
    return [
        {"news_id": r[0], "title": r[1] or "", "content": r[2] or "", "category": r[3] or ""}
        for r in rows if (r[2] or "").strip()
    ]


async def build(rows: List[Dict[str, Any]], reset: bool = False) -> None:
    if reset:
        logger.info("--reset：删除旧集合并按 cosine 距离重建")
        reset_collection()

    ids: List[str] = []
    texts: List[str] = []
    metas: List[Dict[str, Any]] = []
    skipped = 0

    for row in rows:
        chunks = build_chunks(
            row["title"], row["content"],
            size=settings.rag_chunk_size,
            overlap=settings.rag_chunk_overlap,
        )
        if not chunks:
            skipped += 1
            continue
        delete_news(int(row["news_id"]))
        for idx, chunk in enumerate(chunks):
            cid = f'{int(row["news_id"])}_{idx}'
            ids.append(cid)
            texts.append(chunk)
            metas.append({
                "chunk_id": cid,
                "news_id": int(row["news_id"]),
                "title": str(row["title"])[:200],
                "category": str(row["category"])[:50],
                "chunk_index": idx,
            })

    if not ids:
        raise RuntimeError("没有任何可写入的片段，请检查数据源")

    logger.info(
        "准备写入：新闻 %d 篇 / 片段 %d 条 / 空内容跳过 %d 篇",
        len(rows), len(ids), skipped,
    )
    vectors = await embed_texts(texts)

    # Chroma 单次 upsert 不宜太大，分批写
    for start in range(0, len(ids), 100):
        end = start + 100
        upsert_chunks(ids[start:end], texts[start:end], metas[start:end], vectors[start:end])

    avg_len = sum(len(t) for t in texts) // len(texts)
    logger.info(
        "建库完成：集合=%s 总片段=%d 平均长度=%d字 嵌入维度=%d",
        settings.chroma_collection, count_chunks(), avg_len, len(vectors[0]),
    )


async def async_main(args: argparse.Namespace) -> None:
    if args.source == "mysql":
        rows = await load_from_mysql()
        logger.info("数据源=MySQL，读到 %d 篇", len(rows))
    else:
        rows = load_from_file(args.file_path)
        logger.info("数据源=%s，读到 %d 篇", args.file_path, len(rows))

    if args.limit:
        rows = rows[:args.limit]
        logger.info("--limit=%d，本次只处理 %d 篇", args.limit, len(rows))
    if not rows:
        raise RuntimeError("数据源为空，建库终止")

    await build(rows, reset=args.reset)


def main() -> None:
    parser = argparse.ArgumentParser(description="构建新闻 RAG 向量索引")
    parser.add_argument("--source", choices=["file", "mysql"], default="file")
    parser.add_argument(
        "--file-path", default=str(PROJECT_ROOT / "data_files" / "news_seed.json"),
        help="JSON 种子文件路径",
    )
    parser.add_argument("--reset", action="store_true", help="删除旧集合后重建")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 篇，调试用")
    args = parser.parse_args()

    setup_logging()
    request_id_var.set(new_request_id())
    try:
        asyncio.run(async_main(args))
    except Exception:
        logger.exception("建库失败")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
