"""把最近的汇报转发到群（测试用）：不带按钮；群已升级时给出新的 chat_id。"""
import contextlib
import io
import json
from unittest import mock

from intel import __main__ as M
from intel import store as S
from intel import tg

from .helpers import GROUP, TempRepo


class Forward(TempRepo):
    def setUp(self):
        super().setUp()
        S.SENT.mkdir(parents=True)
        for name, created, title in [("2026-11-02T180000+0900-tools-watch.json", "2026-11-02T18:00:00+09:00", "旧的"),
                                     ("2026-11-04T090000+0000-tools-watch.json", "2026-11-04T09:00:00+00:00", "新的"),
                                     ("2026-11-04T100000+0000-tools-watch-deep-abc.json", "2026-11-04T10:00:00+00:00", "深度")]:
            (S.SENT / name).write_text(json.dumps({
                "topic": "tools-watch", "created_at": created,
                "header": f"header {title}", "items": [{"id": "a", "text": f"item {title}", "message_id": 1}]},
                ensure_ascii=False), encoding="utf-8")

    def test_sends_latest_without_buttons(self):
        calls = []
        with mock.patch.object(tg, "send", side_effect=lambda t, b=None, chat=None: calls.append((t, b, chat))):
            _, out = self.run_cmd(M.cmd_forward, GROUP, "tools-watch,events")
        texts = [c[0] for c in calls]
        self.assertEqual(texts[1:], ["header 新的", "item 新的"])     # 最新的一份，不是深度阅读
        self.assertTrue(all(c[1] is None for c in calls))            # 不带按钮
        self.assertTrue(all(c[2] == GROUP for c in calls))           # 发到群，不是私信
        self.assertIn("events", out)                                  # 没发过的主题说明跳过
        self.assertIn("forwarded 2 message(s)", out)

    def test_upgraded_group_reports_new_id(self):
        err = tg.TelegramError("sendMessage", {"ok": False, "description": "Bad Request: group chat was upgraded to a supergroup chat",
                                               "parameters": {"migrate_to_chat_id": -1009876543210}})
        buf = io.StringIO()
        with mock.patch.object(tg, "send", side_effect=err), contextlib.redirect_stdout(buf), self.assertRaises(SystemExit):
            M.cmd_forward(GROUP)
        self.assertIn("已经变成 -1009876543210", buf.getvalue())

    def test_default_topics_are_shared_ones(self):
        calls = []
        with mock.patch.object(tg, "send", side_effect=lambda t, b=None, chat=None: calls.append(t)):
            _, out = self.run_cmd(M.cmd_forward, GROUP)
        self.assertIn("header 新的", calls)                          # 默认转发开启 share_to_group 的主题
        self.assertIn("tools-watch", out)

    def test_no_chat(self):
        with self.assertRaises(SystemExit):
            M.cmd_forward("")
