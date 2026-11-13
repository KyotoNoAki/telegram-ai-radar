"""群升级为超级群组（chat_id 变化）时的提示：发到你看得到的地方，同一次迁移只提示一次。"""
import json
from unittest import mock

from intel import __main__ as M
from intel import store as S
from intel import tg

from .helpers import TempRepo

OLD, NEW = -123456789, -1001234567890


def updates(*msgs, start=1):
    return [{"update_id": start + i, "message": m} for i, m in enumerate(msgs)]


class PollingPath(TempRepo):
    """没有 Worker 时，sync 直接从 Telegram 拉更新：由 Action 发私信。"""

    def run_sync(self, ups, **env):
        path = self.write_json("updates.json", ups)
        sent = []
        with mock.patch.dict("os.environ", {"INTEL_FAKE_UPDATES": path, **env}), \
                mock.patch.object(tg, "send", side_effect=lambda t, b=None: sent.append(t) or {"message_id": 1}):
            _, out = self.run_cmd(M.sync_polling)
        return out, sent

    def test_both_service_messages_one_notice(self):
        ups = updates({"chat": {"id": OLD}, "migrate_to_chat_id": NEW},
                      {"chat": {"id": NEW}, "migrate_from_chat_id": OLD})
        out, sent = self.run_sync(ups, GITHUB_ACTIONS="true", TELEGRAM_TEAM_GROUP_ID=str(OLD))
        self.assertEqual(len(sent), 1)                                # 私信只发一次
        self.assertIn(f"{OLD} → {NEW}", sent[0])
        self.assertIn("就是 TELEGRAM_TEAM_GROUP_ID", sent[0])
        self.assertIn("::warning title=群 chat_id 变了::", out)       # Action 运行页上的黄色警告
        self.assertEqual(out.count("::warning"), 1)
        rows = S.read_jsonl(S.DATA / "migrations.jsonl")
        self.assertEqual([(r["from"], r["to"]) for r in rows], [(OLD, NEW)])

    def test_ordinary_message_ignored(self):
        out, sent = self.run_sync(updates({"chat": {"id": 1}, "from": {"id": 1}, "text": "/start"}))
        self.assertEqual(sent, [])
        self.assertFalse((S.DATA / "migrations.jsonl").exists())

    def test_notice_failure_does_not_break_sync(self):
        path = self.write_json("u.json", updates({"chat": {"id": OLD}, "migrate_to_chat_id": NEW}))
        with mock.patch.dict("os.environ", {"INTEL_FAKE_UPDATES": path}), \
                mock.patch.object(tg, "send", side_effect=OSError("network down")):
            _, out = self.run_cmd(M.sync_polling)
        self.assertIn("migration notice not sent", out)
        self.assertEqual(len(S.read_jsonl(S.DATA / "migrations.jsonl")), 1)
        self.assertEqual(S.load_state()["tg_offset"], 2)              # 更新照常确认，不会卡住


class WorkerPath(TempRepo):
    """有 Worker 时：Worker 已当场发过私信，sync 只在运行页上提示、留底，并让 Worker 删掉记录。"""

    def test_worker_record(self):
        rows = [{"key": f"migrate:{OLD}:{NEW}", "from": OLD, "to": NEW, "at": "2026-11-05T00:00:00Z"},
                {"key": "mark:1:2", "id": "abc1234567", "code": "i", "at": "2026-11-05T00:00:00Z"}]
        calls = []

        def fake_http(method, url, token, body=None):
            calls.append((method, url, body))
            return rows if method == "GET" else {"deleted": len(body["keys"])}

        with mock.patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "x", "GITHUB_ACTIONS": "true"}), \
                mock.patch.object(M, "http_json", side_effect=fake_http), \
                mock.patch.object(tg, "send") as send:
            _, out = self.run_cmd(M.sync_worker, "https://w.example.org")
            _, out2 = self.run_cmd(M.sync_worker, "https://w.example.org")   # 记录还没删掉又取到一次
        send.assert_not_called()                                       # Worker 已经发过私信
        self.assertIn("::warning title=群 chat_id 变了::", out)
        self.assertNotIn("::warning", out2)                            # 同一次迁移不重复警告
        self.assertIn("synced 1 mark(s)", out)                         # 按钮记录照常同步
        ack = [c for c in calls if c[0] == "POST"][0][2]["keys"]
        self.assertIn(f"migrate:{OLD}:{NEW}", ack)                     # 让 Worker 删掉迁移记录
        self.assertEqual(len(S.read_jsonl(S.DATA / "migrations.jsonl")), 1)
