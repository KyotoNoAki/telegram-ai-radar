"""测试共用：在临时目录里搭一个干净的仓库（测试用的 topics.json + 空的 data/），跑完自动删除。"""
import contextlib
import io
import json
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from intel import store as S

REPO = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
TODAY = date(2026, 11, 4)
GROUP = "-1001234567890"            # 虚构的群 chat_id

# 测试用的三个主题：一个卡片版式、自动进群；一个汇总版式、卡片上有「发到群」；一个没有要点的
TOOLS = {
    "id": "tools-watch", "name": "新工具观察", "enabled": True,
    "purpose": "关注能提高写作效率的新工具", "queries": ["new writing tool release"],
    "judge": "开源、能离线用的为 high", "max_items": 3,
    "points": [{"key": "what", "label": "是什么"}, {"key": "price", "label": "价格", "hint": "写原文金额"}],
    "cron": "40 8 * * 1", "timezone": "Asia/Tokyo",
    "ai": {"mode": "api", "provider": "anthropic", "model": "claude-opus-5-5"},
    "share_to_group": True, "deep_dive": True,
}
NEWS = {
    "id": "daily-news", "name": "每日新闻", "enabled": True, "purpose": "行业新闻",
    "layout": "digest", "max_items": 10, "points": [{"key": "impact", "label": "影响"}],
    "ai": {"mode": "agent"}, "share_button": True,
}
EVENTS = {
    "id": "events", "name": "周边活动", "enabled": True, "purpose": "周末活动",
    "ai": {"mode": "agent"}, "share_button": True,
    "buttons": ["⭐ 想去", "✖ 没兴趣", "✔ 去过了"],
}
TEST_TOPICS = [TOOLS, NEWS, EVENTS]


def fixture(name):
    return FIXTURES / name


class TempRepo(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="radar-test-"))
        (self.tmp / "topics.json").write_text(json.dumps(TEST_TOPICS, ensure_ascii=False), encoding="utf-8")
        (self.tmp / "data" / "items").mkdir(parents=True)
        S.configure(self.tmp)
        p = mock.patch.object(S, "today", return_value=TODAY)
        p.start()
        self.addCleanup(p.stop)
        env = mock.patch.dict("os.environ", {"INTEL_DRY_RUN": "1", "TELEGRAM_CHAT_ID": "42"})
        env.start()
        self.addCleanup(env.stop)

    def tearDown(self):
        S.configure(S.DEFAULT_ROOT)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def set_topic(self, topic, **changes):
        path = self.tmp / "topics.json"
        topics = json.loads(path.read_text(encoding="utf-8"))
        for t in topics:
            if t["id"] == topic:
                t.update(changes)
        path.write_text(json.dumps(topics, ensure_ascii=False), encoding="utf-8")

    def write_json(self, name, data):
        p = self.tmp / name
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return str(p)

    def run_cmd(self, fn, *args):
        """运行一个命令函数，返回 (返回值, 标准输出)。"""
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            ret = fn(*args)
        return ret, out.getvalue()

    def outbox(self):
        files = sorted((self.tmp / "data" / "outbox").glob("*.json"))
        return [json.loads(f.read_text(encoding="utf-8")) for f in files]
