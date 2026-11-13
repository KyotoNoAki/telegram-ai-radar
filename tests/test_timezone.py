"""「今天」「现在」按 INTEL_TZ 算（默认 UTC）：GitHub Action 跑在 UTC，东边时区的早上可能已经是第二天。"""
import unittest
from datetime import date, datetime
from unittest import mock

from intel import store as S


def at(utc_iso):
    """把「现在」固定在某个 UTC 时刻。"""
    fixed = datetime.fromisoformat(utc_iso)

    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)
    return mock.patch.object(S, "datetime", FakeDatetime)


def tz(name):
    return mock.patch.object(S, "TZ_NAME", name)


class Timezone(unittest.TestCase):
    def test_default_utc(self):
        with tz("UTC"), at("2026-11-04T22:43:19+00:00"):
            self.assertEqual(S.today(), date(2026, 11, 4))
            self.assertEqual(S.now_iso(), "2026-11-04T22:43:19+00:00")

    def test_user_timezone(self):
        with tz("Asia/Tokyo"), at("2026-11-04T22:43:19+00:00"):    # 东京已经是 11/5 早上 7:43
            self.assertEqual(S.today(), date(2026, 11, 5))
            self.assertEqual(S.now_iso(), "2026-11-05T07:43:19+09:00")
        with tz("America/New_York"), at("2026-11-05T03:00:00+00:00"):   # 纽约还是 11/4 晚上
            self.assertEqual(S.today(), date(2026, 11, 4))

    def test_bad_timezone_falls_back_to_utc(self):
        with tz("Not/AZone"), at("2026-11-04T22:43:19+00:00"):
            self.assertEqual(S.today(), date(2026, 11, 4))
            self.assertTrue(S.now_iso().endswith("+00:00"))


class ExportOrder(unittest.TestCase):
    def test_mixed_offsets(self):
        from intel.__main__ import fetched_key
        rows = [{"fetched_at": "2026-11-04T23:00:00+00:00"},    # 最新
                {"fetched_at": "2026-11-05T07:43:19+09:00"},    # = 11/4 22:43 UTC
                {"fetched_at": None}, {"fetched_at": "bad"}]
        order = sorted(rows, key=fetched_key, reverse=True)
        self.assertEqual(order[0]["fetched_at"], "2026-11-04T23:00:00+00:00")
        self.assertEqual(order[1]["fetched_at"], "2026-11-05T07:43:19+09:00")


if __name__ == "__main__":
    unittest.main()
