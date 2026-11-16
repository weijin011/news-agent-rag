"""检索验证脚本：故意不调用生成模型，只把 top-k 片段和分数打印出来。

为什么必须单独做这一步：
    LLM 的输出是不确定的。一旦把生成接进来，"答得不对"就有三种可能病因
    ——切块烂了 / 检索没命中 / 模型没读资料，症状却完全一样。
    先把 LLM 移出调试环路，才能单独确认检索这一层行不行。

运行（先建库，再跑这个）：
    python -m scripts.build_index --reset
    python -m scripts.probe_retrieval
    python -m scripts.probe_retrieval --query "AI 芯片有什么新进展"
    python -m scripts.probe_retrieval --category 科技 --query "芯片"

验收标准：
    相关问题的 top-1 必须命中正确新闻（Precision@1，人工看打印出的标题）；
    无关的"写代码"问题应命中 0 条，且其最高分低于相关问题的最低 top-1。
    脚本会自动算出两者间隔，并给出阈值建议区间。
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import settings
from services.retriever import count_chunks, retrieve
from utils.logging_conf import setup_logging

# 前 5 条都能在 news_seed.json 里找到对应新闻，最后一条用于测"库里没有"
DEFAULT_QUERIES = [
    "AI 设计的芯片有什么新进展？",
    "英伟达最近有什么投资动作？",
    "国产 AI 芯片公司盈利情况怎么样？",
    "赵心童的比赛结果如何？",
    "日本食品涨价的情况",
    "请写一段 Python 冒泡排序代码",
]


async def main(args: argparse.Namespace) -> None:
    total = count_chunks()
    print(f"索引片段总数：{total}（集合 {settings.chroma_collection}）")
    if total == 0:
        print("索引为空，请先运行：python -m scripts.build_index --reset")
        raise SystemExit(1)

    queries = [args.query] if args.query else DEFAULT_QUERIES
    print(f"阈值：{args.threshold} | top_k：{args.top_k} | 分类过滤：{args.category or '无'}")
    print("本脚本不调用生成模型，只验证检索质量\n")

    top1_scores: list[float] = []
    unrelated_hits = None
    for idx, q in enumerate(queries, start=1):
        hits = await retrieve(
            q, top_k=args.top_k, threshold=args.threshold, category=args.category
        )
        top1_scores.append(hits[0].score if hits else 0.0)
        print("=" * 68)
        print(f"[{idx}] {q}")
        print(f"    命中 {len(hits)}/{args.top_k} 条（低于阈值的已过滤）")
        for rank, h in enumerate(hits, start=1):
            snippet = h.text.replace("\n", " ")[:110]
            print(f"    #{rank} score={h.score:.4f} news_id={h.news_id} "
                  f"[{h.category}] {h.title[:32]}")
            print(f"       {snippet}...")
        if not hits:
            print("    （无命中 → 检索为空时，正式链路应回答「库中没有相关信息」）")
        if idx == len(queries) and not args.query:
            unrelated_hits = len(hits)

    print("=" * 68)
    if not args.query and len(top1_scores) >= 2:
        related = top1_scores[:-1]
        related_min = min(related)
        # 无关问题通常已被阈值挡光，top1 记成 0 会低估下界；
        # 单独用 threshold=0 查一次，拿到它真实的原始最高分
        raw = await retrieve(
            queries[-1], top_k=args.top_k, threshold=0.0, category=args.category
        )
        unrelated_top1 = raw[0].score if raw else 0.0
        print(f"相关问题 top-1 分数：{[round(s, 4) for s in related]}")
        print(f"相关最低 top-1：{related_min:.4f} | 无关最高分：{unrelated_top1:.4f} "
              f"| margin：{related_min - unrelated_top1:.4f}")
        print(f"无关问题命中数：{unrelated_hits}（期望 0；若 >0 说明阈值偏低）")
        if related_min > unrelated_top1:
            inside = unrelated_top1 < args.threshold < related_min
            print(f"阈值建议区间：({unrelated_top1:.4f}, {related_min:.4f})，"
                  f"当前 {args.threshold} " + ("落在区间内" if inside else "不在区间内，需调整"))
        else:
            print("警告：相关与无关的分数区间重叠，纯余弦阈值已无法分开，")
            print("      下一步应考虑 rerank / 混合检索，而不是继续硬调阈值。")
        print("验收：相关问题 top-1 正确 + 无关问题命中 0 条。")
        print("只调阈值不需要重建索引；改了切块参数才要 --reset。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="验证 RAG 检索质量（不调用 LLM）")
    parser.add_argument("--query", default="", help="只测一个问题")
    parser.add_argument("--category", default="", help="按分类过滤，如 科技")
    parser.add_argument("--top-k", type=int, default=settings.rag_top_k)
    parser.add_argument("--threshold", type=float, default=settings.rag_score_threshold)
    args = parser.parse_args()

    setup_logging()
    try:
        asyncio.run(main(args))
    except Exception as exc:
        print(f"\n检索失败：{type(exc).__name__}: {exc}")
        print("若提示未配置 AI_API_KEY，请把 .env 里的 AI_API_KEY 填上后重试。")
        raise SystemExit(1)
