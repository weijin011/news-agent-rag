"""RAG 评测：把「感觉答得还行」变成能写进简历的数字。

运行（在项目根目录，用 venv 的 python）：
    python -m scripts.evaluate
    python -m scripts.evaluate --answers 3      # 额外对 3 条做端到端生成抽样
    python -m scripts.evaluate --limit 5        # 快速冒烟

默认只跑检索层（不调生成模型，省钱）。三个指标：
    Precision@1  相关问题的 top-1 命中正确新闻的比例
    Hit@5        正确答案出现在 top-5 里的比例
    拒答率        库外问题被阈值挡下、不进模型的比例
"""
import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import PROJECT_ROOT, settings
from services.llm import friendly_model_error
from services.rag_chain import NO_ANSWER, answer, prepare
from services.retriever import count_chunks, retrieve
from utils.logging_conf import setup_logging

RULE = "=" * 76


def faithfulness(text: str, context: str) -> tuple[int, int, list[str]]:
    """答案忠实度：检查答案里的数字是否都能在检索资料里找到。

    只查数字（含小数、百分号、千分位）——《xxx》书名号内容模型爱改写，
    而数字编造是最容易识别也最致命的幻觉。返回 (有依据数, 总数, 可疑数字)。
    """
    nums = re.findall(r"\d[\d,]*\.?\d*%?", text)
    if not nums:
        return 0, 0, []
    # 归一化：去掉逗号和百分号，避免 3,120 与 3120 判为不同
    norm = lambda s: s.replace(",", "").rstrip("%")
    ctx = {norm(n) for n in re.findall(r"\d[\d,]*\.?\d*%?", context)}
    missing = sorted({n for n in nums if norm(n) not in ctx})
    return len(nums) - len(missing), len(nums), missing


async def main(args: argparse.Namespace) -> None:
    data = json.loads(Path(args.eval_set).read_text(encoding="utf-8"))
    cases = data["cases"]
    if args.limit:
        cases = cases[:args.limit]

    total = count_chunks()
    if total == 0:
        print("索引为空，请先运行：python -m scripts.build_index --reset")
        raise SystemExit(1)

    print(f"索引片段：{total} | 阈值：{settings.rag_score_threshold} "
          f"| top_k：{settings.rag_top_k} | 用例：{len(cases)}")
    print(f"评测集：{args.eval_set}\n{RULE}")

    p1 = hit5 = rel_total = 0
    refused = oos_total = 0
    oos_raw_tops: list[float] = []

    for c in cases:
        q = c["question"]
        hits = await retrieve(q)
        expected = set(c["expected_news_ids"])
        top1_id = hits[0].news_id if hits else None
        top1_score = hits[0].score if hits else 0.0
        got5 = {h.news_id for h in hits}

        if c["type"] == "relevant":
            rel_total += 1
            ok1, ok5 = top1_id in expected, bool(got5 & expected)
            p1 += ok1
            hit5 += ok5
            print(f"{'OK  ' if ok1 else 'MISS'} #{c['id']:2d} P@1={int(ok1)} Hit@5={int(ok5)} "
                  f"top1={top1_id}({top1_score:.4f}) | {q}")
            if not ok1:
                print(f"       top5={[h.news_id for h in hits]} 期望={sorted(expected)}")
        else:
            oos_total += 1
            # 不过滤再查一次，拿到真实的原始最高分，用于评估阈值余量
            raw = await retrieve(q, threshold=0.0)
            raw_top = raw[0].score if raw else 0.0
            oos_raw_tops.append(raw_top)
            ok = len(hits) == 0
            refused += ok
            print(f"{'OK  ' if ok else 'LEAK'} #{c['id']:2d} 命中={len(hits)} "
                  f"原始最高分={raw_top:.4f} | {q}")
            if not ok:
                print(f"       泄漏片段 news_id={[h.news_id for h in hits]}")

    print(RULE)
    if rel_total:
        print(f"Precision@1 : {p1}/{rel_total} = {p1 / rel_total:.3f}")
        print(f"Hit@5       : {hit5}/{rel_total} = {hit5 / rel_total:.3f}")
    if oos_total:
        print(f"拒答率      : {refused}/{oos_total} = {refused / oos_total:.3f}")
        worst = max(oos_raw_tops) if oos_raw_tops else 0.0
        print(f"库外问题原始最高分：{worst:.4f} | 当前阈值 {settings.rag_score_threshold} "
              f"| 余量 {settings.rag_score_threshold - worst:+.4f}")
    print("指标随 embedding 模型、切块策略和阈值变化；调参前后各跑一次才有对照意义。")

    if args.answers:
        print(f"\n{RULE}\n端到端生成抽样（真实调用模型，最多 {args.answers} 条）")
        ok = failed = 0
        faithful_ok = faithful_total = 0
        for c in [x for x in cases if x["type"] == "relevant"][:args.answers]:
            try:
                text, sources = await answer(c["question"])
            except Exception as exc:
                # 一条失败不该让整个评测崩掉，尤其是额度/限流这类账户问题
                failed += 1
                print(f"#{c['id']} 生成失败：{friendly_model_error(exc)}")
                continue
            ok += 1
            nums = [int(n) for n in re.findall(r"\[(\d+)\]", text)]
            bad = sorted({n for n in nums if n < 1 or n > len(sources)})
            print(f"#{c['id']} 来源数={len(sources)} 答案引用={nums} 非法编号={bad or '无'}")
            print(f"   {text[:180].replace(chr(10), ' ')}")

            # 忠实度：拿同一问题的检索上下文对照答案里的数字
            prep = await prepare(c["question"])
            from services.rag_chain import build_context
            supported, total_num, missing = faithfulness(text, build_context(prep.hits))
            faithful_ok += supported
            faithful_total += total_num
            print(f"   忠实度：数字 {supported}/{total_num} 有依据"
                  + (f" | 可疑：{missing}" if missing else ""))

        if faithful_total:
            print(f"数字忠实度合计：{faithful_ok}/{faithful_total} "
                  f"= {faithful_ok / faithful_total:.3f}")
        print(f"生成抽样：成功 {ok} 条，失败 {failed} 条（失败通常只影响生成层，检索指标依然有效）")

        oos = next((x for x in cases if x["type"] == "out_of_scope"), None)
        if oos:
            # 这条不进模型：检索为空应直接返回固定拒答，可离线验证
            text, sources = await answer(oos["question"])
            print(f"拒答检查：来源数={len(sources)} 固定拒答={text == NO_ANSWER}")
            print(f"   {text}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RAG 检索质量评测")
    parser.add_argument(
        "--eval-set", default=str(PROJECT_ROOT / "data_files" / "rag_eval_set.json")
    )
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 条")
    parser.add_argument("--answers", type=int, default=0, help="额外做 N 条端到端生成抽样")
    args = parser.parse_args()

    setup_logging()
    try:
        asyncio.run(main(args))
    except Exception as exc:
        print(f"\n评测失败：{type(exc).__name__}: {exc}")
        raise SystemExit(1)
