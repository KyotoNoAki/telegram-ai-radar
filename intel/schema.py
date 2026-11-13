"""统一数据格式。

每条信息入库前都经过 normalize()，保证下面几个核心字段一定存在；
主题配置里的要点（points）原样保存在 item["points"] 里，其余字段也原样保留。
查不到的信息留空，推送时显示为「待核实」，绝不填猜测值。
"""
from urllib.parse import urlsplit

from . import store as S

CORE = {
    "title": "标题",
    "url": "链接",
    "source": "来源",
    "highlight": "一句话亮点",
    "fetched_at": "抓取时间",
    "fit": "匹配度",
}
UNKNOWN = "待核实"
FIT_ORDER = {"high": 0, "medium": 1, "low": 2}


def host_of(url):
    return urlsplit(url).netloc.lower().removeprefix("www.")


def normalize(raw, topic, cfg=None):
    """把 AI 交回的一条原始记录整理成统一格式。缺标题或链接时抛 ValueError。"""
    title = str(raw.get("title") or "").strip()
    url = str(raw.get("url") or "").strip()
    if not title or not url:
        raise ValueError(f"缺少标题或链接：{raw!r:.120}")
    item = {k: v for k, v in raw.items() if k not in ("snippet", "found_at")}
    fit = raw.get("fit")
    item.update(
        id=S.item_id(url),
        topic=topic,
        title=title,
        url=url,
        source=str(raw.get("source") or host_of(url)).strip(),
        highlight=str(raw.get("highlight") or "").strip(),
        fetched_at=raw.get("fetched_at") or raw.get("found_at") or S.now_iso(),
        fit=fit if fit in FIT_ORDER else "low",
    )
    return item
