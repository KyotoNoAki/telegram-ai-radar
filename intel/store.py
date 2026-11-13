"""资料库：纯文本 JSONL，适合放在 git 里多方读写。

data/items/<topic>.jsonl  每条信息一行（由 ingest 写）
data/marks.jsonl          你按按钮的记录（只由 GitHub Action 写）
data/state.json           Telegram 拉取进度
data/runs.json            方案 B 每个主题上次运行的时间
data/outbox/*.json        待发送的汇报（生成推送时写，Action 发完后移到 sent/）
"""
import hashlib
import json
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, parse_qsl, urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError



def configure(root):
    """设定仓库根目录（topics.json、data/、exports/ 都在它下面）。测试用它指向临时目录。"""
    global ROOT, DATA, ITEMS, MARKS, STATE, OUTBOX, SENT, EXPORTS
    ROOT = Path(root)
    DATA = ROOT / "data"
    ITEMS = DATA / "items"
    MARKS = DATA / "marks.jsonl"
    STATE = DATA / "state.json"
    OUTBOX = DATA / "outbox"
    SENT = DATA / "sent"
    EXPORTS = ROOT / "exports"


DEFAULT_ROOT = Path(__file__).resolve().parent.parent
configure(DEFAULT_ROOT)

STATUS_LABEL = {
    "new": "新",
    "reported": "已报告",
    "interested": "感兴趣",
    "rejected": "不需要",
    "done": "已处理",
}
MARK_CODES = {"i": "interested", "n": "rejected", "d": "done"}


# 「今天」「现在」一律按用户所在的时区算：环境变量 INTEL_TZ（例如 Asia/Tokyo、Europe/Berlin），没设就用 UTC。
# GitHub Action 和云端 AI 环境都跑在 UTC，直接用机器本地日期的话，东八区早上 8 点前会算成前一天。
# 主题配置里的 "timezone" 只决定这个主题的 cron 按哪个时区理解（见 cron.py）。
TZ_NAME = os.environ.get("INTEL_TZ") or "UTC"


def tz():
    try:
        return ZoneInfo(TZ_NAME)
    except ZoneInfoNotFoundError:          # 环境里没装时区数据库或写错了：退回 UTC，不会因此报错
        return timezone.utc


def now():
    return datetime.now(timezone.utc).astimezone(tz())


def now_iso():
    return now().isoformat(timespec="seconds")


def today():
    return now().date()


# ---------- 读写 ----------
def read_jsonl(path):
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    path.write_text(text, encoding="utf-8")


def append_jsonl(path, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_topics():
    path = ROOT / "topics.json"
    if not path.exists():
        raise SystemExit("找不到 topics.json：先把 topics.example.json 复制成 topics.json，再按你的需要修改（见 docs/topics.md）")
    return {t["id"]: t for t in json.loads(path.read_text(encoding="utf-8"))}


def load_state():
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}


def save_state(state):
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---------- 去重用的键 ----------
_DROP_PARAMS = re.compile(r"^(utm_|fbclid|gclid|ref$|source$)")


def norm_url(url):
    p = urlsplit(url.strip())
    host = p.netloc.lower().removeprefix("www.")
    path = p.path.rstrip("/") or "/"
    q = [(k, v) for k, v in parse_qsl(p.query) if not _DROP_PARAMS.match(k)]
    return f"{host}{path}" + (f"?{urlencode(sorted(q))}" if q else "")


def norm_title(title):
    t = title.lower()
    t = re.sub(r"\b(19|20)\d{2}\b", "", t)          # 去掉年份，同一件事换一年也能认出来
    t = re.sub(r"[^\w぀-ヿ一-鿿]+", "", t)
    return t


def item_id(url):
    return hashlib.sha1(norm_url(url).encode()).hexdigest()[:10]


def title_key(topic, title):
    return hashlib.sha1(f"{topic}|{norm_title(title)}".encode()).hexdigest()[:10]


# ---------- 状态 ----------
def marks_by_item():
    latest = {}
    for m in read_jsonl(MARKS):
        latest[m["id"]] = m
    return latest


def items(topic):
    return read_jsonl(ITEMS / f"{topic}.jsonl")


def all_items():
    rows = []
    for p in sorted(ITEMS.glob("*.jsonl")):
        rows.extend(items(p.stem))
    return rows


def status_of(item, marks):
    m = marks.get(item["id"])
    if m:
        return m["status"]
    return "reported" if item.get("reported_at") else "new"


def parse_date(s):
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None
