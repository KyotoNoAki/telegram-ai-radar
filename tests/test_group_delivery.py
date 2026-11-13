"""抄送到团队群：只抄送配置了 share_to_group 的主题的正式汇报；不带按钮；群失败不影响私信；补发不重复。"""
import json
from unittest import mock

from intel import __main__ as M
from intel import store as S
from intel import tg

from .helpers import GROUP, TempRepo


class GroupDelivery(TempRepo):
    def setUp(self):
        super().setUp()
        self.sent = []          # (chat, text, buttons)

    def outbox(self, name, topic, n=2, **extra):
        S.OUTBOX.mkdir(parents=True, exist_ok=True)
        rep = {"topic": topic, "created_at": "2026-11-05T09:00:00+09:00", "header": f"header {topic}",
               "items": [{"id": f"id{i}", "text": f"{topic} item {i}"} for i in range(n)], **extra}
        (S.OUTBOX / name).write_text(json.dumps(rep, ensure_ascii=False), encoding="utf-8")

    def fake_send(self, fail=None):
        def send(text, buttons=None, chat=None):
            if fail and fail(text, chat):
                raise fail.error
            self.sent.append((chat or "owner", text, buttons))
            return {"message_id": len(self.sent)}
        return send

    def deliver(self, fail=None, group=GROUP):
        with mock.patch.dict("os.environ", {"TELEGRAM_TEAM_GROUP_ID": group}), \
                mock.patch.object(tg, "send", side_effect=self.fake_send(fail)):
            _, out = self.run_cmd(M.cmd_deliver)
        return out

    def to(self, chat):
        return [(t, b) for c, t, b in self.sent if c == chat]

    def test_config(self):
        shared = {k for k, v in S.load_topics().items() if v.get("share_to_group")}
        self.assertEqual(shared, {"tools-watch"})

    def test_shared_topic_goes_to_both(self):
        self.outbox("a-tools-watch.json", "tools-watch")
        out = self.deliver()
        self.assertEqual(len(self.to("owner")), 3)
        group = self.to(GROUP)
        self.assertEqual([t for t, _ in group], ["header tools-watch", "tools-watch item 0", "tools-watch item 1"])
        self.assertTrue(all(b is None for _, b in group))                       # 群里不带按钮
        self.assertTrue(any(b for _, b in self.to("owner")))                    # 私信照常带按钮
        self.assertIn("1 also to team group", out)

    def test_not_shared(self):
        self.outbox("a-daily-news.json", "daily-news")                          # 没开 share_to_group 的主题
        self.outbox("b-tools-watch-deep-x.json", "tools-watch", kind="deep")      # 深度内容
        self.deliver()
        self.assertEqual(self.to(GROUP), [])
        self.assertEqual(len(self.to("owner")), 6)

    def test_no_group_configured(self):
        self.outbox("a-tools-watch.json", "tools-watch")
        self.deliver(group="")
        self.assertEqual(self.to(GROUP), [])
        self.assertEqual(len(self.to("owner")), 3)

    def test_group_upgraded_does_not_block_owner(self):
        self.outbox("a-tools-watch.json", "tools-watch")
        fail = lambda text, chat: chat == GROUP
        fail.error = tg.TelegramError("sendMessage", {"description": "Bad Request: group chat was upgraded to a supergroup chat",
                                                      "parameters": {"migrate_to_chat_id": -1009876543210}})
        out = self.deliver(fail)
        self.assertEqual(len(self.to("owner")), 3)                               # 私信全部发出
        self.assertIn("::warning title=群推送失败::", out)
        self.assertIn("新的 chat_id 是 -1009876543210", out)
        self.assertEqual(list(S.OUTBOX.glob("*.json")), [])                      # 汇报照常归档，不卡住
        rep = json.loads(next(S.SENT.glob("*.json")).read_text(encoding="utf-8"))
        self.assertIn("-1009876543210", rep["group_error"])

    def test_resume_does_not_duplicate_group(self):
        # 群里已经发了标题和第 1 条（上次中断），这次只补第 2 条
        self.outbox("a-tools-watch.json", "tools-watch", header_sent=True, group_header_sent=True,
                    items=[{"id": "id0", "text": "item 0", "message_id": 1, "group_message_id": 2},
                           {"id": "id1", "text": "item 1", "message_id": 3}])
        self.deliver()
        self.assertEqual([t for t, _ in self.to(GROUP)], ["item 1"])
        self.assertEqual(self.to("owner"), [])


class ShareButton(GroupDelivery):
    """不进群的主题：卡片上有「📤 发到群」按钮，由 Worker 处理。"""

    def buttons_of(self, text):
        return next(b for c, t, b in self.sent if c == "owner" and t == text)

    def test_config_disjoint(self):
        topics = S.load_topics()
        share_btn = {k for k, v in topics.items() if v.get("share_button")}
        self.assertEqual(share_btn, {"daily-news", "events"})
        self.assertFalse(share_btn & {k for k, v in topics.items() if v.get("share_to_group")})

    def test_buttons(self):
        S.OUTBOX.mkdir(parents=True, exist_ok=True)
        (S.OUTBOX / "a-daily-news.json").write_text(json.dumps({     # 汇总版式：原来没有按钮
            "topic": "daily-news", "created_at": "2026-11-05T07:40:00+09:00", "header": "",
            "items": [{"id": "digest-0", "text": "digest", "buttons": False}]}), encoding="utf-8")
        self.outbox("b-events.json", "events", n=1)
        self.outbox("c-tools-watch.json", "tools-watch", n=1)
        self.outbox("d-events-deep-x.json", "events", n=1, kind="deep")
        self.deliver()
        share = [[{"text": "📤 发到群", "callback_data": "g"}]]
        self.assertEqual(self.buttons_of("digest"), share)                     # 只有发到群
        ev = [b for c, t, b in self.sent if c == "owner" and t == "events item 0"]
        self.assertEqual(ev[0][-1], share[0])                                   # 标记按钮 + 发到群
        self.assertEqual(len(ev[0][0]), 3)
        self.assertNotIn(share[0], self.buttons_of("tools-watch item 0"))       # 进群的主题不需要
        self.assertNotIn(share[0], ev[1] or [])                                 # 深度内容不加
        self.assertEqual({t for t, _ in self.to(GROUP)},                        # 只有 share_to_group 的自动进群
                         {"header tools-watch", "tools-watch item 0"})
