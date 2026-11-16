"""编排层：RAG 五步的本体（检索 → 拼装 → 生成 → 引用溯源）。

分层约定：
    routers/ai.py 只负责参数校验和 SSE 编码，不碰检索和 prompt；
    本模块不碰 HTTP，方便被评测脚本、离线任务直接调用。

两个刻意的设计：
1. 引用列表从「检索结果」反查 news_id，绝不让模型自己报编号 —— 模型会编造引用。
2. 检索为空时直接返回固定拒答，根本不调用生成模型 —— 省 token，也是唯一
   能 100% 保证不幻觉的做法。
"""
import asyncio
import re
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, List, Optional

from config.settings import settings
from services.llm import get_client
from services.retriever import Hit, retrieve
from utils.logging_conf import get_logger

logger = get_logger(__name__)

# 与 Agent 共用同一份并发上限思路：限制同时打向模型的请求数
_model_semaphore = asyncio.Semaphore(settings.ai_max_concurrency)

NO_ANSWER = "抱歉，新闻库中没有找到相关信息，无法回答这个问题。"
MAX_HISTORY_TURNS = 3  # 只带最近 3 轮，防止多轮对话把上下文窗口撑爆

SYSTEM_TEMPLATE = """你是「新闻资讯系统」的智能问答助手，必须严格遵守以下规则：

① 只能依据下面【检索资料】里的内容回答，禁止使用资料之外的知识，禁止编造数字、人名、时间。
② 资料不足以回答时，直接输出：「{no_answer}」，不要猜测，不要说「可能」。
③ 每个事实性结论后必须标注来源编号，格式为 [1]；同一结论可引用多个，如 [1][3]。
④ 用简洁的中文回答，不要复述问题，不要写「根据资料显示」这类套话。
⑤ 只能引用下面实际出现的编号，禁止输出不存在的编号。

【检索资料】
{context}
"""


@dataclass(frozen=True)
class PreparedRag:
    """prepare() 的产物：与模型调用解耦，可直接单测和做检索评测。"""
    question: str
    hits: List[Hit]
    messages: List[Dict[str, Any]]
    has_evidence: bool

    def sources(self) -> List[Dict[str, Any]]:
        """引用来自检索结果，不解析模型输出。news_id 供前端跳新闻详情。"""
        return [
            {
                "index": i,
                "news_id": h.news_id,
                "title": h.title,
                "category": h.category,
                "score": h.score,
            }
            for i, h in enumerate(self.hits, start=1)
        ]


def build_context(hits: List[Hit]) -> str:
    """把命中片段编号拼成上下文；编号后面会变成答案里的 [n]。"""
    blocks = []
    for i, h in enumerate(hits, start=1):
        head = f"[{i}] 《{h.title}》"
        if h.category:
            head += f"（分类：{h.category}）"
        blocks.append(head + "\n" + h.text)
    return "\n\n".join(blocks)


def build_messages(
    question: str,
    history: Optional[List[Dict[str, str]]],
    hits: List[Hit],
) -> List[Dict[str, str]]:
    """system 放检索资料，中间放最近几轮历史，最后放当前问题。"""
    system = SYSTEM_TEMPLATE.format(
        no_answer=NO_ANSWER, context=build_context(hits)
    )
    messages: List[Dict[str, str]] = [{"role": "system", "content": system}]

    # 先清洗再裁剪：否则末尾一个空的 assistant 占位会白白占掉一个历史名额
    cleaned = [
        {"role": m["role"], "content": m["content"].strip()[:4000]}
        for m in (history or [])
        if m.get("role") in ("user", "assistant") and (m.get("content") or "").strip()
    ]
    messages.extend(cleaned[-(MAX_HISTORY_TURNS * 2):])

    messages.append({"role": "user", "content": question})
    return messages


async def prepare(
    question: str,
    history: Optional[List[Dict[str, str]]] = None,
    category: Optional[str] = None,
) -> PreparedRag:
    """第 ②③ 步：检索 + 上下文拼装。不调用生成模型。"""
    hits = await retrieve(question, category=category)
    has_evidence = bool(hits)
    logger.info(
        "RAG 检索 question=%r 命中=%d top1=%.4f", question[:40],
        len(hits), hits[0].score if hits else 0.0,
    )
    messages = build_messages(question, history, hits) if has_evidence else []
    return PreparedRag(question, hits, messages, has_evidence)


async def stream_answer(prep: PreparedRag) -> AsyncIterator[Dict[str, Any]]:
    """第 ④⑤ 步：流式生成，产出统一事件（sources / delta / done）。"""
    yield {"type": "sources", "sources": prep.sources()}

    if not prep.has_evidence:
        # 没有证据就不进模型，这是最可靠的防幻觉手段
        yield {"type": "delta", "content": NO_ANSWER}
        yield {"type": "done", "finish_reason": "no_evidence"}
        return

    client = get_client()
    async with _model_semaphore:
        stream = await client.chat.completions.create(
            model=settings.ai_chat_model,
            messages=prep.messages,
            stream=True,
            temperature=0.2,  # 问答场景要稳，不要发散
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            content = getattr(chunk.choices[0].delta, "content", None)
            if content:
                yield {"type": "delta", "content": content}
    yield {"type": "done", "finish_reason": "stop"}


async def answer(
    question: str,
    history: Optional[List[Dict[str, str]]] = None,
    category: Optional[str] = None,
) -> tuple[str, List[Dict[str, Any]]]:
    """非流式版本，供评测脚本 / 离线批处理使用。返回 (答案, 引用列表)。"""
    prep = await prepare(question, history, category)
    if not prep.has_evidence:
        return NO_ANSWER, []
    client = get_client()
    async with _model_semaphore:
        resp = await client.chat.completions.create(
            model=settings.ai_chat_model,
            messages=prep.messages,
            stream=False,
            temperature=0.2,
        )
    return resp.choices[0].message.content or "", prep.sources()


def parse_citations(text: str, max_index: int) -> List[int]:
    """抽出答案里的合法引用编号，评测时用来查模型有没有编造 [9] 这类编号。"""
    nums = {int(n) for n in re.findall(r"\[(\d+)\]", text)}
    return sorted(n for n in nums if 1 <= n <= max_index)
