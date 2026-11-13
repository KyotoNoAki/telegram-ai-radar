"""统一格式、主题模块查找、.env 读取、示例配置。"""
import json
import unittest

from intel import ai as A
from intel import schema
from intel import store as S
from intel.topics import default, module

from .helpers import REPO, TempRepo


class Normalize(unittest.TestCase):
    def test_core_fields(self):
        it = schema.normalize({"title": " X ", "url": "https://www.a.example.org/p?utm_source=x",
                               "fit": "maybe", "snippet": "raw", "points": {"k": "v"}}, "t")
        for k in schema.CORE:
            self.assertIn(k, it)
        self.assertEqual(it["title"], "X")
        self.assertEqual(it["source"], "a.example.org")     # 没给来源就用域名
        self.assertEqual(it["fit"], "low")                  # 不认识的匹配度当 low
        self.assertEqual(it["points"], {"k": "v"})          # 要点原样保留
        self.assertNotIn("snippet", it)
        self.assertEqual(it["id"], S.item_id("https://a.example.org/p"))   # 去掉 www 和追踪参数后同一个 ID

    def test_requires_title_and_url(self):
        with self.assertRaises(ValueError):
            schema.normalize({"title": "x"}, "t")


class ModuleLookup(unittest.TestCase):
    def test_default(self):
        self.assertIs(module("any-topic").select, default.select)
        self.assertIs(module("any-topic", {"module": "default"}).render, default.render)


class DotEnv(TempRepo):
    def test_load(self):
        import os
        from intel.env import load_dotenv
        p = self.tmp / ".env"
        p.write_text('# comment\nexport INTEL_TEST_A="1"\nINTEL_DRY_RUN=0\n', encoding="utf-8")
        load_dotenv(p)
        self.assertEqual(os.environ["INTEL_TEST_A"], "1")
        self.assertEqual(os.environ["INTEL_DRY_RUN"], "1")   # 已有的环境变量优先
        del os.environ["INTEL_TEST_A"]

    def test_missing_topics_json(self):
        (self.tmp / "topics.json").unlink()
        with self.assertRaisesRegex(SystemExit, "topics.example.json"):
            S.load_topics()


class Example(unittest.TestCase):
    """topics.example.json 是能直接复制使用的骨架：格式正确，默认不运行。"""

    def test_example_config(self):
        topics = json.loads((REPO / "topics.example.json").read_text(encoding="utf-8"))
        self.assertTrue(topics)
        for t in topics:
            self.assertRegex(t["id"], r"^[a-z0-9-]+$")
            self.assertFalse(t["enabled"])                       # 填好之前不会运行、不会花钱
            self.assertIn(t["ai"]["mode"], ("api", "agent"))
            self.assertIn(A.build_task(t).splitlines()[0], f"# 任务：{t['name']}")
            A.result_schema(t)


if __name__ == "__main__":
    unittest.main()
