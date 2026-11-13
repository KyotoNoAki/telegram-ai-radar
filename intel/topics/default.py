"""默认主题行为：信任 AI 给出的匹配度（fit），高/中匹配的按匹配度排序推送，最多 max_items 条。"""
from .. import render as R
from ..schema import FIT_ORDER

header_note = None


def prepare(item, cfg):
    return item


def sort_key(r):
    return FIT_ORDER.get(r.get("fit"), 2)


def select(items, cfg):
    picked = sorted([r for r in items if r.get("fit") in ("high", "medium") and not r.get("rejected")],
                    key=sort_key)
    n = (cfg or {}).get("max_items")
    return picked[:int(n)] if n else picked


def render(item, cfg):
    return R.wrap(item, R.body_points(item, cfg))
