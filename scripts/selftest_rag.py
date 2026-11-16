"""离线自测：不联网、不消耗 API 额度，验证 RAG 编排逻辑与 SSE 协议。

运行：
    python -m scripts.selftest_rag

覆盖：上下文编号 / 三条铁律 / 历史裁剪 / 引用反查 / 拒答分支 /
      引用编号校验 / 消息拆分 / 错误翻译 / HTTP 层 SSE 事件序列。
凡是会真实调用模型的地方都用 mock 替换，所以配额耗尽时也能跑。
"""
import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from main import app
from routers.ai import _split_messages
from services.llm import friendly_model_error
from services.rag_chain import (
    MAX_HISTORY_TURNS, NO_ANSWER, PreparedRag, build_context, build_messages,
    parse_citations, stream_answer,
)
from services.retriever import Hit


def make_hit(news_id: int, title: str = "标题A") -> Hit:
    return Hit(
        chunk_id=f"{news_id}_0", news_id=news_id, title=title,
        category="科技", text="正文片段内容", score=0.81,
    )


class RagChainTests(unittest.TestCase):
    def test_build_context_numbers_sources(self):
        ctx = build_context([make_hit(1), make_hit(2, "标题B")])
        self.assertIn("[1] 《标题A》", ctx)
        self.assertIn("[2] 《标题B》", ctx)
        self.assertIn("（分类：科技）", ctx)

    def test_system_prompt_has_three_rules(self):
        msgs = build_messages("问题", None, [make_hit(1)])
        system = msgs[0]["content"]
        for keyword in ("禁止", "资料不足以回答", "标注来源编号", "[1]"):
            self.assertIn(keyword, system, keyword)
        self.assertEqual(msgs[-1], {"role": "user", "content": "问题"})

    def test_history_is_trimmed_and_empty_removed(self):
        history = [
            {"role": role, "content": f"第{i}条"}
            for i, role in enumerate(["user", "assistant"] * 8)
        ]
        history.append({"role": "assistant", "content": ""})  # 前端空占位
        msgs = build_messages("当前问题", history, [make_hit(1)])
        # system + 最近 6 条历史 + 当前问题
        self.assertEqual(len(msgs), 1 + MAX_HISTORY_TURNS * 2 + 1)
        self.assertTrue(all(m["content"] for m in msgs))
        self.assertEqual(msgs[1]["content"], "第10条")

    def test_sources_come_from_hits_not_model(self):
        prep = PreparedRag("问题", [make_hit(7), make_hit(9)], [], True)
        src = prep.sources()
        self.assertEqual([s["index"] for s in src], [1, 2])
        self.assertEqual([s["news_id"] for s in src], [7, 9])
        self.assertTrue(all("chunk_id" not in s for s in src))

    def test_no_evidence_branch_never_calls_model(self):
        prep = PreparedRag("库外问题", [], [], False)

        async def collect():
            return [e async for e in stream_answer(prep)]

        events = asyncio.run(collect())
        self.assertEqual([e["type"] for e in events], ["sources", "delta", "done"])
        self.assertEqual(events[0]["sources"], [])
        self.assertEqual(events[1]["content"], NO_ANSWER)
        self.assertEqual(events[2]["finish_reason"], "no_evidence")

    def test_parse_citations_drops_invalid_numbers(self):
        self.assertEqual(parse_citations("见 [1] 与 [3]，还有假的 [9] 和 [0]", 5), [1, 3])
        self.assertEqual(parse_citations("没有引用", 5), [])

    def test_split_messages_takes_last_user_question(self):
        payload = [
            {"role": "assistant", "content": "你好"},
            {"role": "user", "content": "第一个问题"},
            {"role": "assistant", "content": "回答"},
            {"role": "user", "content": "第二个问题"},
        ]
        q, hist = _split_messages(payload)
        self.assertEqual(q, "第二个问题")
        self.assertEqual(len(hist), 3)

    def test_friendly_error_messages(self):
        self.assertIn("额度", friendly_model_error(Exception("Error code: 403 - Free quota exhausted")))
        self.assertIn("频繁", friendly_model_error(Exception("Error code: 429 - rate limit")))
        self.assertIn("密钥", friendly_model_error(Exception("401 invalid api key")))
        self.assertIn("模型名", friendly_model_error(Exception("404 Model not exist.")))


def _events_from_sse(body: str) -> list[dict]:
    events = []
    for line in body.splitlines():
        if not line.startswith("data: "):
            continue
        data = line[6:]
        if data == "[DONE]":
            continue
        events.append(json.loads(data))
    return events


class SseProtocolTests(unittest.TestCase):
    def test_sse_no_evidence(self):
        empty = PreparedRag("库外问题", [], [], False)
        with patch("routers.ai.prepare", new=AsyncMock(return_value=empty)):
            with TestClient(app) as client:
                with client.stream(
                    "POST", "/api/ai/chat",
                    json={"messages": [{"role": "user", "content": "库外问题"}]},
                ) as resp:
                    self.assertEqual(resp.status_code, 200)
                    self.assertIn("text/event-stream", resp.headers["content-type"])
                    body = "".join(resp.iter_text())
        events = _events_from_sse(body)
        self.assertEqual([e["type"] for e in events], ["sources", "delta", "done"])
        self.assertEqual(events[1]["content"], NO_ANSWER)

    def test_sse_carries_real_news_ids(self):
        prep = PreparedRag("问题", [make_hit(42, "某新闻")], [], True)

        async def fake_stream(p):
            yield {"type": "sources", "sources": p.sources()}
            yield {"type": "delta", "content": "答案 [1]"}
            yield {"type": "done", "finish_reason": "stop"}

        with patch("routers.ai.prepare", new=AsyncMock(return_value=prep)), \
             patch("routers.ai.stream_answer", new=fake_stream):
            with TestClient(app) as client:
                with client.stream(
                    "POST", "/api/ai/chat",
                    json={"messages": [{"role": "user", "content": "问题"}],
                          "category": "科技"},
                ) as resp:
                    body = "".join(resp.iter_text())
        events = _events_from_sse(body)
        self.assertEqual([e["type"] for e in events], ["sources", "delta", "done"])
        self.assertEqual(events[0]["sources"][0]["news_id"], 42)
        self.assertEqual(events[1]["content"], "答案 [1]")


if __name__ == "__main__":
    unittest.main(verbosity=2)
