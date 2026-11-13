"""端到端（方案 A）：RSS 抓取 → 工作说明 → AI 交回结果（样例）→ 去重入库 → 推送 → 发送（dry-run）→ 按钮 → 深度内容 → 导出。
不联网、不调用 AI、不发 Telegram。"""
import csv
import json
from pathlib import Path
from unittest import mock

from intel import __main__ as M
from intel import fetch as F
from intel import schema
from intel import store as S
from intel import tg

from .helpers import TempRepo, fixture

UNREACHABLE = "http://127.0.0.1:9/feed.xml"     # 本机 9 号端口没有服务，立即连接失败


def result(*items, lead=None):
    out = {"items": list(items)}
    if lead is not None:
        out["lead"] = lead
    return out


NOTEBOOK = {"title": "Open Notebook 2.0 正式发布", "url": "https://notebook.example.org/blog/2-0",
            "source": "Open Notebook 官网", "highlight": "开源离线笔记，2.0 支持多设备同步", "fit": "high",
            "summary": "离线优先的笔记工具。", "points": {"what": "离线笔记工具", "price": "免费"}}
GRANT = {"title": "Sample Grant for Independent Tool Makers", "url": "https://grants.example.org/tool-makers",
         "source": "Sample Fund", "highlight": "面向独立开发者的小额资助", "fit": "medium",
         "summary": "", "points": {"what": "资助", "price": ""}}
COOKING = {"title": "Neighbourhood cooking contest", "url": "https://cooking.example.org/contest",
           "source": "cooking", "highlight": "", "fit": "low", "summary": "", "points": {}}


class Feeds(TempRepo):
    def setUp(self):
        super().setUp()
        self.set_topic("tools-watch", feeds=[
            {"name": "样例 RSS", "url": fixture("sample_feed.xml").as_uri()},
            {"name": "连不上的源", "url": UNREACHABLE},
            {"name": "坏掉的源", "url": fixture("broken_feed.html").as_uri()},
            {"name": "样例 Atom", "url": fixture("sample_feed2.xml").as_uri()},
        ])
        p = mock.patch("intel.fetch.time.sleep")      # 重试不真的等
        p.start()
        self.addCleanup(p.stop)

    def test_fetch_failures_do_not_stop(self):
        cfg = S.load_topics()["tools-watch"]
        with self.assertLogs("intel.fetch", level="INFO") as logs:
            cands, results = F.fetch_topic(cfg, timeout=2, retries=0)
        self.assertEqual({r["source"] for r in results if not r["ok"]}, {"连不上的源", "坏掉的源"})
        self.assertTrue(any("抓取失败" in l and "连不上的源" in l for l in logs.output))
        self.assertTrue(any("共 4 个源，2 个成功，2 个失败" in l for l in logs.output))
        titles = [c["title"] for c in cands]
        self.assertEqual(sum("Open Notebook" in t for t in titles), 1)    # 两个源里的同一页面只留一条
        self.assertIn("Sample Grant for Independent Tool Makers", titles)

    def test_snapshot_then_prompt(self):
        with mock.patch.dict("os.environ", {"GITHUB_ACTIONS": "true"}), self.assertLogs("intel.fetch"):
            _, out = self.run_cmd(M.cmd_fetch_all)
        self.assertIn("tools-watch: 2/4 个源成功", out)
        self.assertIn("::warning title=抓取失败 tools-watch::连不上的源", out)
        self.assertNotIn("daily-news", out)                                # 没有 feeds 的主题不抓

        # 生成工作说明时读快照，不联网；候选写进工作说明
        with mock.patch.object(F, "fetch_url", side_effect=AssertionError("不该联网")), \
                self.assertLogs("intel.fetch") as logs:
            _, task = self.run_cmd(M.cmd_prompt, "tools-watch")
        self.assertTrue(any("使用 Action" in l for l in logs.output))
        self.assertIn("City Library Workshop", task)

        # 快照过期 → 现抓
        snap_file = self.tmp / "data" / "fetched" / "tools-watch.json"
        snap = json.loads(snap_file.read_text(encoding="utf-8"))
        snap["fetched_at"] = "2020-01-01T00:00:00+00:00"
        snap_file.write_text(json.dumps(snap), encoding="utf-8")
        out_path = str(self.tmp / "fetched.json")
        with self.assertLogs("intel.fetch") as logs:
            self.run_cmd(M.cmd_fetch, "tools-watch", out_path)
        self.assertTrue(any("小时没更新" in l for l in logs.output))
        self.assertTrue(json.loads(Path(out_path).read_text(encoding="utf-8")))

    def test_exclude_match_limit(self):
        cfg = {"feed_exclude": ["sports"], "feeds": [{"name": "新闻", "url": fixture("news_feed.xml").as_uri()}]}
        with self.assertLogs("intel.fetch") as logs:
            cands, _ = F.fetch_topic(cfg, retries=0)
        self.assertEqual([c["title"] for c in cands],
                         ["Small museum opens a new photography wing", "Football club wins the regional cup"])
        self.assertTrue(any("按排除词去掉 1 条" in l for l in logs.output))

        cfg = {"feeds": [{"name": "x", "url": fixture("news_feed.xml").as_uri(), "match": ["cup", "museum"],
                          "exclude": ["museum"]}]}
        with self.assertLogs("intel.fetch"):
            cands, _ = F.fetch_topic(cfg, retries=0)
        self.assertEqual([c["title"] for c in cands], ["Football club wins the regional cup"])

        cfg = {"feed_limit": 2, "feeds": [{"name": "x", "url": fixture("sample_feed.xml").as_uri()},
                                          {"name": "y", "url": fixture("news_feed.xml").as_uri(), "limit": 1}]}
        with self.assertLogs("intel.fetch"):
            cands, _ = F.fetch_topic(cfg, retries=0)
        self.assertEqual([c["source"] for c in cands], ["x", "x", "y"])


class AgentFlow(TempRepo):
    def ingest(self, topic, obj):
        fname, out = self.run_cmd(M.cmd_ingest, topic, self.write_json("result.json", obj))
        return json.loads(fname.read_text(encoding="utf-8")), out

    def test_cards(self):
        rep, out = self.ingest("tools-watch", result(NOTEBOOK, GRANT, COOKING, dict(NOTEBOOK, url="")))
        self.assertIn("AI 交回 3 条，其中新的 3 条", out)                    # 没网址的丢掉
        self.assertIn("推送 2 条（另有 1 条不太相关，已入库）", rep["header"])
        self.assertIn("🏆 最值得看：Open Notebook 2.0 正式发布", rep["header"])
        first = rep["items"][0]["text"]
        self.assertTrue(first.startswith("💡 <b>开源离线笔记，2.0 支持多设备同步</b>\n<b>Open Notebook 2.0 正式发布</b>"))
        self.assertIn("<b>价格</b>：免费", first)
        self.assertIn('<a href="https://notebook.example.org/blog/2-0">→ 打开原文</a>', first)
        self.assertIn("<b>价格</b>：待核实", rep["items"][1]["text"])

        rows = S.items("tools-watch")
        for r in rows:
            for k in schema.CORE:                                          # 统一格式的核心字段都在
                self.assertIn(k, r)
            self.assertTrue(r["reported_at"])

        # 同一件事换了网址（带年份）再交回来：不重复推送
        again = dict(NOTEBOOK, title="Open Notebook 2.0 正式发布 2026", url="https://mirror.example.org/notebook")
        rep, out = self.ingest("tools-watch", result(again))
        self.assertIn("其中新的 0 条", out)
        self.assertEqual(rep["items"], [])

    def test_rejected_not_pushed_again(self):
        self.ingest("tools-watch", result(GRANT))
        S.append_jsonl(S.MARKS, {"id": S.item_id(GRANT["url"]), "status": "rejected", "at": S.now_iso()})
        rep, _ = self.ingest("tools-watch", result(dict(GRANT, url="https://other.example.org/grant")))
        self.assertEqual(rep["items"], [])                                 # 点过「不需要」的同名条目不再推

    def test_max_items(self):
        many = [dict(NOTEBOOK, title=f"工具 {n}", url=f"https://t{n}.example.org/") for n in range(5)]
        rep, _ = self.ingest("tools-watch", result(*many))
        self.assertEqual(len(rep["items"]), 3)
        left = [r for r in S.items("tools-watch") if not r.get("reported_at")]
        self.assertEqual(len(left), 2)                                     # 合格但这期没挑中的留到以后

    def test_digest(self):
        rep, _ = self.ingest("daily-news", result(
            {"title": "某项新规发布", "url": "https://news.example.org/a", "source": "通讯社",
             "highlight": "下月生效", "fit": "high", "summary": "", "points": {"impact": "需要更新流程"}},
            dict(COOKING), lead="今天一句话"))
        self.assertEqual(len(rep["items"]), 1)
        text = rep["items"][0]["text"]
        self.assertTrue(text.startswith("📰 <b>每日新闻</b> · 11/4\n\n💡 <b>今天一句话</b>"))
        self.assertIn("影响：需要更新流程", text)
        self.assertNotIn("cooking", text)                                  # 低匹配的不进汇总
        self.assertFalse(rep["items"][0]["buttons"])

    def test_digest_splits_long_messages(self):
        many = [{"title": f"新闻 {n} " + "很长的标题" * 30, "url": f"https://n{n}.example.org/", "fit": "high",
                 "highlight": "x" * 200, "points": {"impact": "y"}} for n in range(10)]
        rep, _ = self.ingest("daily-news", result(*many))
        self.assertGreater(len(rep["items"]), 1)
        self.assertTrue(all(len(i["text"]) <= 3600 for i in rep["items"]))

    def test_deliver_dry_run_and_resume_after_failure(self):
        self.ingest("tools-watch", result(NOTEBOOK, GRANT))
        real = tg.call
        calls = []

        def flaky(method, **p):
            calls.append(p)
            if len(calls) == 2:
                raise OSError("network down")
            return real(method, **p)
        with mock.patch.object(tg, "call", side_effect=flaky):
            _, out = self.run_cmd(M.cmd_deliver)
        self.assertIn("1 failed (kept in outbox for retry)", out)
        rep = self.outbox()[0]
        self.assertTrue(rep["header_sent"])

        with mock.patch.object(tg, "call", side_effect=lambda m, **p: calls.append(p) or real(m, **p)):
            _, out = self.run_cmd(M.cmd_deliver)
        self.assertIn("delivered 1 report(s)", out)
        texts = [c["text"] for c in calls]
        self.assertEqual(sum(t.startswith("📡") for t in texts), 1)        # 标题不重发
        self.assertEqual(len(calls), 4)                                    # 标题 + 失败的一次 + 两张卡片
        buttons = calls[-1]["reply_markup"]["inline_keyboard"]
        self.assertEqual([b["text"] for b in buttons[0]], ["⭐ 感兴趣", "✖ 不需要", "✔ 已处理"])
        self.assertEqual(self.outbox(), [])

    def test_custom_buttons(self):
        self.ingest("events", result({"title": "周末市集", "url": "https://market.example.org/", "fit": "high"}))
        sent = []
        with mock.patch.object(tg, "send", side_effect=lambda t, b=None, chat=None: sent.append(b) or {"message_id": 1}):
            self.run_cmd(M.cmd_deliver)
        card = [b for b in sent if b][0]
        self.assertEqual([b["text"] for b in card[0]], ["⭐ 想去", "✖ 没兴趣", "✔ 去过了"])
        self.assertEqual(card[1], [{"text": "📤 发到群", "callback_data": "g"}])

    def test_buttons_sync_deep_and_export(self):
        self.ingest("tools-watch", result(NOTEBOOK))
        iid = S.item_id(NOTEBOOK["url"])
        path = self.write_json("updates.json", [{"update_id": 5, "callback_query": {
            "id": "q", "data": f"m:{iid}:i", "from": {"id": 42},
            "message": {"message_id": 9, "chat": {"id": 42}}}}])
        with mock.patch.dict("os.environ", {"INTEL_FAKE_UPDATES": path}):
            _, out = self.run_cmd(M.cmd_sync)
        self.assertIn("synced 1 mark(s)", out)
        self.assertEqual(S.marks_by_item()[iid]["status"], "interested")

        _, out = self.run_cmd(M.cmd_pending_deep)
        pending = json.loads(out.split("\n#")[0])
        self.assertEqual([p["id"] for p in pending], [iid])

        md = self.tmp / "deep.md"
        md.write_text("## 它是什么\n\n**离线**笔记工具。\n\n- 要点一", encoding="utf-8")
        self.run_cmd(M.cmd_deep, "tools-watch", iid, str(md))
        deep = [r for r in self.outbox() if r.get("kind") == "deep"][0]
        self.assertIn("<b>它是什么</b>", deep["items"][0]["text"])
        self.assertIn("• 要点一", deep["items"][0]["text"])
        _, out = self.run_cmd(M.cmd_pending_deep)
        self.assertEqual(json.loads(out.split("\n#")[0]), [])              # 写过的不再列出

        self.run_cmd(M.cmd_export)
        with (self.tmp / "exports" / "all_items.csv").open(encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(rows[0]["status"], "感兴趣")
        self.assertEqual(rows[0]["points"], "是什么：离线笔记工具；价格：免费")


if __name__ == "__main__":
    import unittest
    unittest.main()
