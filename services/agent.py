"""Agent：把控制流交给模型，由它决定调哪个工具、调几次。

与 RAG 管道（rag_chain.py）的本质区别：
    RAG：检索 → 拼装 → 生成，流程写死。
    Agent：模型看问题 → 自己选工具 → 看结果 → 再决定下一步，直到它认为够了。

事件流（供 routers/ai.py 转 SSE）：
    thought     模型正在调用工具的意图
    tool_call   工具名 + 参数
    tool_result 工具返回的摘要与条目
    sources     最终引用（从工具结果反查，不从模型输出解析）
    delta       正文分片
    done        结束原因
"""
import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional

from config.settings import settings
from services.llm import get_client
from services.news_tools import TOOL_DISPATCH, TOOL_SCHEMAS, ToolResult
from utils.logging_conf import get_logger

logger = get_logger(__name__)

MAX_TOOL_ROUNDS = 4  # 防止模型死循环调工具，硬上限

SYSTEM_PROMPT = """你是新闻资讯系统的智能助手。你手里有 4 个工具可以查新闻库。

工作方式：
① 遇到任何关于新闻内容、事件、分类、热度、时间的问题，必须先调用工具拿真实数据，不许凭记忆回答。
② 一次可以调多个工具；拿到结果后如果信息够了就直接回答，不够可以继续调（最多 4 轮）。
③ 回答时每个事实性结论后标来源编号 [1]、[2]，编号对应工具结果条目里的顺序。
④ 工具没查到相关内容时，直接说「新闻库中没有相关信息」，不要编造。
⑤ 用简洁中文回答，不复述问题。
"""

# 全局并发闸门：限制同时打向模型服务的请求数，防止 429（大小可配置）
_model_semaphore = asyncio.Semaphore(settings.ai_max_concurrency)


@dataclass
class AgentTrace:
    """一次 Agent 运行的完整轨迹，用于日志与前端展示。"""
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    results: List[ToolResult] = field(default_factory=list)
    rounds: int = 0

    def collect_sources(self) -> List[Dict[str, Any]]:
        """把所有工具返回的新闻条目汇总成前端引用列表，按 news_id 去重。"""
        seen: Dict[int, Dict[str, Any]] = {}
        idx = 1
        for r in self.results:
            for item in r.items:
                nid = item.get("news_id")
                if not nid or nid in seen:
                    continue
                seen[nid] = {
                    "index": idx,
                    "news_id": nid,
                    "title": item.get("title", ""),
                    "category": item.get("category", ""),
                    "tool": r.tool,
                }
                idx += 1
        return list(seen.values())


async def _execute_tool(name: str, args: Dict[str, Any]) -> ToolResult:
    fn = TOOL_DISPATCH.get(name)
    if fn is None:
        return ToolResult(tool=name, summary=f"未知工具：{name}", items=[])
    try:
        result = fn(**args)
        if asyncio.iscoroutine(result):
            result = await result
        return result
    except Exception as e:
        logger.exception("工具 %s 执行失败: %s", name, e)
        return ToolResult(tool=name, summary=f"工具执行失败：{type(e).__name__}", items=[])


async def _call_model(messages: List[Dict[str, Any]], stream: bool):
    """统一走并发闸门调用模型。"""
    client = get_client()
    async with _model_semaphore:
        return await client.chat.completions.create(
            model=settings.ai_chat_model,
            messages=messages,
            tools=TOOL_SCHEMAS,
            tool_choice="auto",
            temperature=0.2,
            stream=stream,
        )


async def run_agent(
    question: str,
    history: Optional[List[Dict[str, str]]] = None,
) -> AsyncIterator[Dict[str, Any]]:
    """Agent 主循环：产出事件流。

    注意：工具结果消息必须带 tool_call_id 才能喂回模型（OpenAI function calling 协议）。
    """
    trace = AgentTrace()
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT}
    ]
    for m in (history or [])[-6:]:
        if m.get("role") in ("user", "assistant") and (m.get("content") or "").strip():
            messages.append({"role": m["role"], "content": m["content"][:4000]})
    messages.append({"role": "user", "content": question})

    # ---------- 阶段 1：工具决策循环 ----------
    for _ in range(MAX_TOOL_ROUNDS):
        resp = await _call_model(messages, stream=False)
        msg = resp.choices[0].message
        tool_calls = msg.tool_calls or []
        trace.rounds += 1

        if not tool_calls:
            # 模型认为信息够了，准备直接回答；把它的话放进上下文供流式阶段续写
            if msg.content:
                messages.append({"role": "assistant", "content": msg.content})
            break

        # 把 assistant 的 tool_calls 消息原样加入上下文（协议要求）
        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [tc.model_dump() for tc in tool_calls],
        })

        for tc in tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            yield {"type": "tool_call", "name": name, "arguments": args}

            result = await _execute_tool(name, args)
            trace.tool_calls.append({"name": name, "arguments": args})
            trace.results.append(result)
            yield {
                "type": "tool_result",
                "name": name,
                "summary": result.summary,
                "count": len(result.items),
            }
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result.summary,
            })

        logger.info("Agent 第 %d 轮调用工具 %s", trace.rounds, [tc.function.name for tc in tool_calls])

    # ---------- 阶段 2：把最终引用先送出去 ----------
    sources = trace.collect_sources()
    yield {"type": "sources", "sources": sources}
    if not sources:
        # 工具一条新闻都没拿到 → 直接拒答，不调生成模型
        yield {"type": "delta", "content": "抱歉，新闻库中没有相关信息，无法回答这个问题。"}
        yield {"type": "done", "finish_reason": "no_evidence", "trace": {
            "rounds": trace.rounds, "tool_calls": trace.tool_calls
        }}
        return

    # ---------- 阶段 3：流式生成最终答案 ----------
    # 明确告诉模型基于已有工具结果回答，避免它再去调工具
    followup = {
        "role": "user",
        "content": "请基于上面工具返回的新闻数据回答用户的问题，按来源编号标注出处，不要调用工具。",
    }
    messages.append(followup)

    stream = await _call_model(messages, stream=True)
    async for chunk in stream:
        if not chunk.choices:
            continue
        content = getattr(chunk.choices[0].delta, "content", None)
        if content:
            yield {"type": "delta", "content": content}

    yield {
        "type": "done",
        "finish_reason": "stop",
        "trace": {"rounds": trace.rounds, "tool_calls": trace.tool_calls},
    }
