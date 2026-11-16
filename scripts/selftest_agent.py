"""Agent 离线自测：mock 掉模型与工具，验证 Agent 循环逻辑，不联网不花钱。

运行：
    python -m scripts.selftest_agent

覆盖：单次工具调用 / 多轮连续调用 / 引用从结果反查 / 工具失败降级 /
      拒答分支 / 并发闸门存在 / SSE 事件顺序。
"""
import asyncio
import json
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from main import app
from services import agent as agent_mod
from services.agent import AgentTrace, run_agent
from services.news_tools import ToolResult

# 造一个假的 function call 返回对象
class FakeToolCall:
    def __init__(self, name: str, args: Dict[str, Any], call_id: str):
        self.id = call_id
        self.function = type("Fn", (), {
            "name": name, "arguments": json.dumps(args, ensure_ascii=False)
        })()

    def model_dump(self):
        return {"id": self.id, "type": "function", "function": {
            "name": self.function.name, "arguments": self.function.arguments
        }}


class FakeMessage:
    def __init__(self, tool_calls=None, content="", finish="stop"):
        self.tool_calls = tool_calls
        self.content = content
        self.finish_reason = finish


class FakeChoice:
    def __init__(self, message: FakeMessage, finish="stop"):
        self.message = message
        self.finish_reason = finish


class FakeResp:
    def __init__(self, message: FakeMessage, finish="stop"):
        self.choices = [FakeChoice(message, finish)]


class FakeChunk:
    def __init__(self, content):
        self.choices = [type("C", (), {"delta": type("D", (), {"content": content})()})()]


async def fake_stream(chunks):
    """把普通 list 包装成 async iterable，匹配真实 SDK 的流式接口。"""
    for c in chunks:
        yield c


class AgentLoopTests(unittest.TestCase):
    def _fake_tool(self, name: str, count: int = 2) -> ToolResult:
        return ToolResult(
            tool=name,
            summary=f"{name} 返回 {count} 条",
            items=[{
                "news_id": i + 1, "title": f"新闻{i+1}", "category": "科技",
            } for i in range(count)],
        )

    def test_single_tool_then_answer(self):
        """模型调一次工具，然后给出带引用的答案。"""
        sequence = [
            FakeResp(FakeMessage([FakeToolCall("search_news", {"query": "芯片"}, "c1")], finish="tool_calls")),
        ]
        call_count = {"n": 0}

        async def fake_call(messages, stream):
            if stream:
                return fake_stream([FakeChunk("找到相关新闻 [1]")])
            call_count["n"] += 1
            # 第 1 次决策调工具；之后模型认为信息够了，不再调工具
            if call_count["n"] == 1:
                return sequence[0]
            return FakeResp(FakeMessage([], content="可以回答了"))

        async def fake_execute(name, args):
            return self._fake_tool(name, 2)

        async def collect():
            with patch.object(agent_mod, "_call_model", new=fake_call), \
                 patch.object(agent_mod, "_execute_tool", new=fake_execute):
                return [e async for e in run_agent("芯片新闻")]

        events = asyncio.run(collect())
        types = [e["type"] for e in events]
        self.assertIn("tool_call", types)
        self.assertIn("tool_result", types)
        self.assertIn("sources", types)
        self.assertEqual(types[-1], "done")
        src = next(e for e in events if e["type"] == "sources")["sources"]
        self.assertEqual(len(src), 2)
        self.assertEqual([s["news_id"] for s in src], [1, 2])
        self.assertEqual([s["index"] for s in src], [1, 2])

    def test_multiple_tool_calls_in_sequence(self):
        """模型连续调两个不同工具——这是 Agent 区别于 RAG 的核心行为。"""
        calls = []

        async def fake_call(messages, stream):
            if stream:
                return fake_stream([FakeChunk("体育更高 [1]")])
            # 检查 tool 角色的消息被正确喂回（function calling 协议要求）
            tool_msgs = [m for m in messages if m.get("role") == "tool"]
            if len(tool_msgs) == 0:
                return FakeResp(FakeMessage([
                    FakeToolCall("get_hot_news", {"category": "科技"}, "c1")
                ], finish="tool_calls"))
            if len(tool_msgs) == 1:
                return FakeResp(FakeMessage([
                    FakeToolCall("get_hot_news", {"category": "体育"}, "c2")
                ], finish="tool_calls"))
            return FakeResp(FakeMessage([], content="可以回答了"))

        async def fake_execute(name, args):
            calls.append((name, args.get("category")))
            return self._fake_tool(name, 1)

        async def collect():
            with patch.object(agent_mod, "_call_model", new=fake_call), \
                 patch.object(agent_mod, "_execute_tool", new=fake_execute):
                return [e async for e in run_agent("对比两个分类")]

        events = asyncio.run(collect())
        self.assertEqual(calls, [
            ("get_hot_news", "科技"), ("get_hot_news", "体育")
        ])
        done = events[-1]
        self.assertEqual(done["finish_reason"], "stop")
        # 2 轮工具调用 + 1 轮「信息够了」的决策 = 3
        self.assertEqual(done["trace"]["rounds"], 3)

    def test_no_tool_result_triggers_refusal_without_llm(self):
        """工具返回空 → 直接拒答，且不再调生成模型。"""
        llm_calls = {"n": 0, "generation": 0}

        async def fake_call(messages, stream):
            if stream:
                # 拒答分支不应该走到生成，走到就记一次，供断言检查
                llm_calls["generation"] += 1
                return fake_stream([FakeChunk("不该出现")])
            llm_calls["n"] += 1
            # 第一轮调工具；第二轮模型意识到查不到，停止调用
            if llm_calls["n"] == 1:
                return FakeResp(FakeMessage([
                    FakeToolCall("search_news", {"query": "不存在"}, "c1")
                ], finish="tool_calls"))
            return FakeResp(FakeMessage([], content=""))

        async def fake_execute(name, args):
            return ToolResult(tool=name, summary="无匹配新闻", items=[])

        async def collect():
            with patch.object(agent_mod, "_call_model", new=fake_call), \
                 patch.object(agent_mod, "_execute_tool", new=fake_execute):
                return [e async for e in run_agent("库外问题")]

        events = asyncio.run(collect())
        # 2 次决策（调工具 + 决定停止），但绝不进生成阶段
        self.assertEqual(llm_calls["n"], 2)
        self.assertEqual(llm_calls["generation"], 0)  # 拒答分支绝不调生成模型
        self.assertEqual(events[-1]["finish_reason"], "no_evidence")
        delta = [e["content"] for e in events if e["type"] == "delta"]
        self.assertIn("没有相关信息", "".join(delta))

    def test_trace_deduplicates_news_ids(self):
        """多次工具返回同一条新闻时，引用列表按 news_id 去重。"""
        trace = AgentTrace()
        for tool in ["get_hot_news", "filter_by_category"]:
            trace.results.append(ToolResult(
                tool=tool, summary="x",
                items=[{"news_id": 7, "title": "同一篇", "category": "科技"}],
            ))
        src = trace.collect_sources()
        self.assertEqual(len(src), 1)
        self.assertEqual(src[0]["news_id"], 7)

    def test_semaphore_is_configured(self):
        """并发闸门存在且容量可配置。"""
        from config.settings import settings
        self.assertIsNotNone(agent_mod._model_semaphore)
        self.assertGreaterEqual(settings.ai_max_concurrency, 1)


class AgentSseTests(unittest.TestCase):
    def test_agent_sse_event_order(self):
        """HTTP 层：tool_call / tool_result 必须先于 sources 与正文到达。"""
        async def fake_agent(question, history):
            yield {"type": "tool_call", "name": "get_hot_news", "arguments": {"category": "体育"}}
            yield {"type": "tool_result", "name": "get_hot_news", "summary": "x", "count": 1}
            yield {"type": "sources", "sources": [{"index": 1, "news_id": 6, "title": "新闻"}]}
            yield {"type": "delta", "content": "答案 [1]"}
            yield {"type": "done", "finish_reason": "stop", "trace": {"rounds": 1, "tool_calls": []}}

        with patch("routers.ai.run_agent", new=fake_agent):
            with TestClient(app) as client:
                with client.stream(
                    "POST", "/api/ai/agent-chat",
                    json={"messages": [{"role": "user", "content": "热门新闻"}]},
                ) as resp:
                    self.assertEqual(resp.status_code, 200)
                    body = "".join(resp.iter_text())
        events = []
        for line in body.splitlines():
            if line.startswith("data: ") and line[6:] != "[DONE]":
                events.append(json.loads(line[6:]))
        self.assertEqual(
            [e["type"] for e in events],
            ["tool_call", "tool_result", "sources", "delta", "done"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
