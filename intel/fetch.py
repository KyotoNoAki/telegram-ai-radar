"""抓取：从主题配置里的 feeds（RSS / Atom）拉候选条目。

    "feeds": [{"name": "某网站", "url": "https://example.org/feed/", "match": ["关键词1", "关键词2"]}]

match 可选：标题或摘要里含任一关键词才保留（不分大小写）。
exclude 可选：含任一关键词就去掉；主题配置里的 "feed_exclude" 对这个主题的所有源都生效。
主题配置里的 "feed_limit"（或单个源的 "limit"）：每个源最多保留前几条，避免一个宽泛的搜索把候选淹没。
每个源单独处理：连不上、超时、格式坏了都只记一行日志然后继续下一个，绝不让整个任务卡住；
全部失败时输出空列表，AI 照常用网页搜索补上。
"""
import json
import logging
import time
import urllib.request
import xml.etree.ElementTree as ET
import re
from datetime import datetime, timezone
from html import unescape

from . import store as S

log = logging.getLogger("intel.fetch")
UA = "telegram-ai-radar/1.0 (+https://github.com/KyotoNoAki/telegram-ai-radar)"
ATOM = "{http://www.w3.org/2005/Atom}"


def fetch_url(url, timeout=20, retries=1):
    """取回网址内容；失败重试 retries 次，最后仍失败就抛出原异常。file:// 也可以（测试用）。"""
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception:
            if attempt >= retries:
                raise
            time.sleep(1 + attempt)


def _first(el, *tags):
    for t in tags:
        x = el.find(t)
        if x is not None:
            return x
    return None


def _text(el):
    if el is None:
        return ""
    s = "".join(el.itertext()) if len(el) else (el.text or "")
    s = re.sub(r"<[^>]+>", " ", unescape(s))
    return re.sub(r"\s+", " ", s).strip()


def parse_feed(data, source):
    """解析 RSS 2.0 或 Atom，返回 [{title, url, source, snippet, published}]。"""
    root = ET.fromstring(data)
    out = []
    for it in root.iter("item"):                                   # RSS
        out.append({"title": _text(it.find("title")), "url": _text(it.find("link")),
                    "snippet": _text(it.find("description"))[:300],
                    "published": _text(it.find("pubDate"))})
    for e in root.iter(f"{ATOM}entry"):                            # Atom
        link = e.find(f"{ATOM}link[@rel='alternate']")
        if link is None:
            link = e.find(f"{ATOM}link")
        out.append({"title": _text(e.find(f"{ATOM}title")),
                    "url": (link.get("href") if link is not None else "") or "",
                    "snippet": _text(_first(e, f"{ATOM}summary", f"{ATOM}content"))[:300],
                    "published": _text(_first(e, f"{ATOM}updated", f"{ATOM}published"))})
    if not out and root.tag not in ("rss", f"{ATOM}feed", "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}RDF"):
        raise ValueError(f"不是 RSS/Atom（根元素 {root.tag}）")
    return [dict(c, source=source) for c in out if c["title"] and c["url"]]


def _keyword_filter(items, match, exclude, name):
    """match：含任一词才保留；exclude：含任一词就去掉。都只看标题和摘要，不分大小写。"""
    text = lambda c: (c["title"] + " " + c["snippet"]).lower()
    match = [w.lower() for w in match or []]
    exclude = [w.lower() for w in exclude or []]
    if match:
        items = [c for c in items if any(w in text(c) for w in match)]
    if exclude:
        before = len(items)
        items = [c for c in items if not any(w in text(c) for w in exclude)]
        if before - len(items):
            log.info("%s：按排除词去掉 %d 条", name, before - len(items))
    return items


def fetch_topic(cfg, timeout=20, retries=1):
    """抓取一个主题的全部 feeds。返回 (候选列表, 每个源的结果)。"""
    candidates, results, seen = [], [], set()
    now = S.now_iso()
    for feed in cfg.get("feeds") or []:
        name, url = feed.get("name") or feed["url"], feed["url"]
        try:
            items = parse_feed(fetch_url(url, timeout=timeout, retries=retries), name)
        except Exception as e:
            reason = f"{type(e).__name__}: {e}"
            log.warning("抓取失败，已跳过：%s <%s> — %s", name, url, reason)
            results.append({"source": name, "url": url, "ok": False, "count": 0, "error": reason})
            continue
        items = _keyword_filter(items, feed.get("match"),
                                [*(cfg.get("feed_exclude") or []), *(feed.get("exclude") or [])], name)
        limit = feed.get("limit") or cfg.get("feed_limit")
        if limit:
            items = items[:int(limit)]
        kept = 0
        for c in items:
            iid = S.item_id(c["url"])
            if iid in seen:
                continue
            seen.add(iid)
            candidates.append(dict(c, fetched_at=now))
            kept += 1
        log.info("抓取成功：%s — %d 条", name, kept)
        results.append({"source": name, "url": url, "ok": True, "count": kept, "error": None})
    ok = sum(r["ok"] for r in results)
    if results:
        log.info("共 %d 个源，%d 个成功，%d 个失败，得到 %d 条候选", len(results), ok, len(results) - ok, len(candidates))
        if ok == 0:
            log.warning("所有源都抓取失败：本轮只靠网页搜索，流程继续")
    else:
        log.info("这个主题没有配置 feeds，跳过自动抓取")
    return candidates, results


# ---------- 快照：GitHub Action 抓好放进仓库，AI 直接读 ----------
# 有些 AI 代理平台的云端环境连不上外部网站，GitHub Action 可以。所以每天由 Action（.github/workflows/fetch.yml）
# 先抓一遍存成 data/fetched/<topic>.json，生成工作说明时读这份快照；快照缺失或过期时才现抓。
SNAPSHOT_MAX_AGE_HOURS = 36


def snapshot_path(topic):
    return S.DATA / "fetched" / f"{topic}.json"


def save_snapshot(topic, candidates, results):
    p = snapshot_path(topic)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"topic": topic, "fetched_at": S.now_iso(), "sources": results,
                             "candidates": candidates}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def load_snapshot(topic, max_age_hours=SNAPSHOT_MAX_AGE_HOURS):
    """读取 Action 抓好的快照；不存在、坏了或超过 max_age_hours 小时就返回 None。"""
    p = snapshot_path(topic)
    if not p.exists():
        log.info("没有 %s 的抓取快照", topic)
        return None
    try:
        snap = json.loads(p.read_text(encoding="utf-8"))
        age = datetime.now(timezone.utc) - datetime.fromisoformat(snap["fetched_at"])
    except (ValueError, KeyError, TypeError) as e:
        log.warning("抓取快照 %s 读不了（%s），改为现抓", p.name, e)
        return None
    hours = age.total_seconds() / 3600
    if hours > max_age_hours:
        log.warning("抓取快照 %s 已经 %.0f 小时没更新（Action 可能没跑），改为现抓", p.name, hours)
        return None
    failed = [s["source"] for s in snap.get("sources", []) if not s.get("ok")]
    log.info("使用 Action 在 %.0f 小时前抓好的快照：%d 条候选%s", hours, len(snap.get("candidates", [])),
             f"（当时失败的源：{'、'.join(failed)}）" if failed else "")
    return snap
