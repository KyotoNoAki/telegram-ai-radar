"""和 AI 配合：工作说明、结果解析、四家接口适配（假回复，不真的调用、不花钱）、定时、方案 B 完整一轮。"""
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from intel import __main__ as M
from intel import ai as A
from intel import cron
from intel import store as S
from intel.ai import anthropic, gemini, openai, openai_compatible

from .helpers import TOOLS, TempRepo

RESULT = {"items": [
    {"title": "Open Notebook 2.0", "url": "https://notebook.example.org/2-0", "source": "官网",
     "highlight": "开源、可离线，2.0 支持多设备同步", "fit": "high", "summary": "一款离线笔记工具。",
     "points": {"what": "离线笔记工具", "price": ""}},
    {"title": "无网址的条目", "url": "", "fit": "high"},
    {"title": "Low One", "url": "https://low.example.org/", "fit": "maybe"},
]}


def reply_text(text):
    return {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}


class Task(unittest.TestCase):
    def test_build_task(self):
        t = A.build_task(TOOLS, known=[{"title": "旧条目", "url": "https://old.example.org/"}],
                         candidates=[{"title": "RSS 候选", "url": "https://rss.example.org/x"}])
        for s in ("关注能提高写作效率的新工具", "new writing tool release", "开源、能离线用的为 high", "最多交回 3 条",
                  "旧条目", "RSS 候选", "what＝是什么", "price＝价格（写原文金额）", "```json"):
            self.assertIn(s, t)
        self.assertIn("搜索并打开原始页面核实", t)
        self.assertIn("不要编造网址", A.build_task(TOOLS, web=False))

    def test_schema_strict(self):
        sch = A.result_schema(TOOLS)
        item = sch["properties"]["items"]["items"]
        self.assertFalse(item["additionalProperties"])
        self.assertEqual(item["properties"]["points"]["required"], ["what", "price"])
        self.assertIn("lead", A.result_schema(dict(TOOLS, layout="digest"))["required"])

    def test_parse_and_clean(self):
        text = "调研过程……\n```json\n" + json.dumps(RESULT, ensure_ascii=False) + "\n```\n结束"
        obj = A.parse_result(text)
        rows = A.clean_items(obj, TOOLS)
        self.assertEqual([r["title"] for r in rows], ["Open Notebook 2.0", "Low One"])   # 没网址的丢掉
        self.assertEqual(rows[1]["fit"], "low")                                         # 不认识的匹配度当 low
        self.assertEqual(rows[1]["points"], {"what": "", "price": ""})
        self.assertIsNone(A.parse_result("没有 JSON"))

    def test_missing_key(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(A.AIError, "ANTHROPIC_API_KEY"):
                A.api_key(TOOLS["ai"])


class Providers(unittest.TestCase):
    def test_all_can_say_whether_they_search(self):
        self.assertTrue(anthropic.SEARCH and openai.SEARCH and gemini.SEARCH)
        self.assertFalse(openai_compatible.SEARCH)

    def test_anthropic_request_and_pause(self):
        calls = []
        replies = [{"content": [{"type": "server_tool_use", "id": "s1", "name": "web_search", "input": {}}],
                    "stop_reason": "pause_turn"}, reply_text("```json\n{\"items\": []}\n```")]
        with mock.patch.object(anthropic, "post_json", side_effect=lambda u, b, h: calls.append((u, b, h)) or replies.pop(0)):
            text = anthropic.research("任务", {"model": "claude-opus-5-5", "max_searches": 4}, "k")
        self.assertIn('"items"', text)
        url, body, headers = calls[0]
        self.assertEqual(url, "https://api.anthropic.com/v1/messages")
        self.assertEqual(headers["x-api-key"], "k")
        self.assertEqual(headers["anthropic-version"], "2026-10-21")
        self.assertEqual(body["tools"][0], {"type": "web_search_20260209", "name": "web_search", "max_uses": 4})
        self.assertEqual(body["fallbacks"], "default")
        self.assertNotIn("thinking", body)
        self.assertEqual(calls[1][1]["messages"][-1]["role"], "assistant")   # 暂停后把回答原样接回去

    def test_anthropic_structure_and_refusal(self):
        with mock.patch.object(anthropic, "post_json", return_value=reply_text('{"items": []}')) as p:
            self.assertEqual(anthropic.structure("文字", {"type": "object"}, {}, "k"), {"items": []})
        body = p.call_args[0][1]
        self.assertEqual(body["output_config"]["format"]["type"], "json_schema")
        self.assertNotIn("tools", body)
        self.assertEqual(body["model"], "claude-opus-5-5")
        with mock.patch.object(anthropic, "post_json",
                               return_value={"content": [], "stop_reason": "refusal", "stop_details": {"category": "cyber"}}):
            with self.assertRaisesRegex(A.AIError, "拒绝"):
                anthropic.research("任务", {}, "k")

    def test_openai(self):
        resp = {"output": [{"type": "web_search_call"},
                           {"type": "message", "content": [{"type": "output_text", "text": '{"items": []}'}]}]}
        with mock.patch.object(openai, "post_json", return_value=resp) as p:
            self.assertEqual(openai.research("任务", {"model": "m"}, "k"), '{"items": []}')
            self.assertEqual(p.call_args[0][1]["tools"], [{"type": "web_search"}])
            self.assertEqual(openai.structure("t", {"type": "object"}, {"model": "m"}, "k"), {"items": []})
            self.assertEqual(p.call_args[0][1]["text"]["format"]["type"], "json_schema")
        with self.assertRaisesRegex(A.AIError, "型号"):
            openai.research("任务", {}, "k")

    def test_gemini(self):
        resp = {"candidates": [{"content": {"parts": [{"text": '{"items": '}, {"text": "[]}"}]}}]}
        with mock.patch.object(gemini, "post_json", return_value=resp) as p:
            self.assertEqual(gemini.research("任务", {"model": "g"}, "k"), '{"items": []}')
            url, body, headers = p.call_args[0]
            self.assertTrue(url.endswith("/models/g:generateContent"))
            self.assertEqual(headers, {"x-goog-api-key": "k"})
            self.assertEqual(body["tools"], [{"google_search": {}}])
        with mock.patch.object(gemini, "post_json", return_value={"promptFeedback": {"blockReason": "SAFETY"}}):
            with self.assertRaisesRegex(A.AIError, "SAFETY"):
                gemini.research("任务", {"model": "g"}, "k")

    def test_openai_compatible(self):
        resp = {"choices": [{"message": {"content": '{"items": []}'}}]}
        ai = {"base_url": "https://api.example.org/", "model": "some-model"}
        with mock.patch.object(openai_compatible, "post_json", return_value=resp) as p:
            self.assertEqual(openai_compatible.structure("t", {}, ai, "k"), {"items": []})
            self.assertEqual(p.call_args[0][0], "https://api.example.org/chat/completions")
            self.assertEqual(p.call_args[0][1]["response_format"], {"type": "json_object"})

    def test_ask_falls_back_to_structure(self):
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "k"}), \
                mock.patch.object(anthropic, "research", return_value="只有文字，没有 JSON"), \
                mock.patch.object(anthropic, "structure", return_value=RESULT) as st:
            self.assertEqual(A.ask(TOOLS, "任务"), RESULT)
        self.assertEqual(st.call_args[0][1], A.result_schema(TOOLS))


JST = timezone(timedelta(hours=9))


class Cron(TempRepo):
    def test_matches(self):
        mon = datetime(2026, 11, 9, 8, 40, tzinfo=JST)           # 周一
        self.assertTrue(cron.matches("40 8 * * 1", mon))
        self.assertFalse(cron.matches("40 8 * * 2", mon))
        self.assertTrue(cron.matches("*/20 8-9 * * 1-5", mon))
        self.assertTrue(cron.matches("40 8 1,15 * 7", datetime(2026, 11, 15, 8, 40, tzinfo=JST)))  # 日或周满足其一
        with self.assertRaises(ValueError):
            cron.parse("61 * * * *")

    def test_due_with_github_delay(self):
        topics = {"tools-watch": TOOLS, "agent-topic": dict(TOOLS, ai={"mode": "agent"})}
        slot = datetime(2026, 11, 9, 8, 40, tzinfo=JST)                  # 按主题的 timezone 理解
        late = (slot + timedelta(minutes=55)).astimezone(timezone.utc)   # GitHub 晚了 55 分钟
        self.assertEqual([t for t, _ in cron.due(topics, late)], ["tools-watch"])   # 方案 A 的主题不由这里跑
        cron.record_run("tools-watch", late)
        self.assertEqual(cron.due(topics, late + timedelta(hours=1)), [])         # 同一个时间点不重复
        self.assertEqual([t for t, _ in cron.due(topics, late + timedelta(days=7))], ["tools-watch"])

    def test_first_run_does_not_backfill_old_slots(self):
        self.assertEqual(cron.due({"tools-watch": TOOLS}, datetime(2026, 11, 12, 12, 0, tzinfo=JST)), [])

    def test_disabled_topic_not_run(self):
        slot = datetime(2026, 11, 9, 8, 41, tzinfo=JST)
        self.assertEqual(cron.due({"tools-watch": dict(TOOLS, enabled=False)}, slot), [])


class RunEndToEnd(TempRepo):
    def test_run(self):
        """方案 B 完整一轮：调用 AI（假回复）→ 去重入库 → 按要点排版 → 再跑一次不重复推送。"""
        reply = reply_text("```json\n" + json.dumps(RESULT, ensure_ascii=False) + "\n```")
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "k"}), \
                mock.patch.object(anthropic, "post_json", return_value=reply) as p:
            fname, _ = self.run_cmd(M.cmd_run, "tools-watch")
        task = p.call_args[0][1]["messages"][0]["content"]
        self.assertIn("关注能提高写作效率的新工具", task)
        self.assertIn("搜索并打开原始页面核实", task)                          # Claude 能联网搜索
        rep = json.loads(fname.read_text(encoding="utf-8"))
        self.assertEqual(len(rep["items"]), 1)                                  # low 的不推
        card = rep["items"][0]["text"]
        self.assertTrue(card.startswith("💡 <b>开源、可离线，2.0 支持多设备同步</b>"))
        self.assertIn("<b>是什么</b>：离线笔记工具", card)
        self.assertIn("<b>价格</b>：待核实", card)                              # 没填的要点写待核实
        self.assertIn("tools-watch", cron.load_runs())

        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "k"}), \
                mock.patch.object(anthropic, "post_json", return_value=reply) as p:
            fname, _ = self.run_cmd(M.cmd_run, "tools-watch")
        self.assertIn("Open Notebook 2.0", p.call_args[0][1]["messages"][0]["content"])  # 推过的写进"不要再报"
        self.assertEqual(json.loads(fname.read_text(encoding="utf-8"))["items"], [])

    def test_run_due_failure_is_warning(self):
        with mock.patch.object(cron, "due", return_value=[("tools-watch", None)]), \
                mock.patch.object(M, "cmd_run", side_effect=A.AIError("接口返回 401")):
            _, out = self.run_cmd(M.cmd_run_due)
        self.assertIn("::warning title=主题运行失败 tools-watch::接口返回 401", out)
        self.assertIn("tools-watch", cron.load_runs())                          # 失败也记录，不在下个小时重复付费


if __name__ == "__main__":
    unittest.main()
