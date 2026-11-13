"""主题的定时（方案 B）：配置里写标准 cron 表达式 "分 时 日 月 周"，按主题的 timezone（没写就用 INTEL_TZ，再没有就是 UTC）理解。

GitHub Actions 每小时检查一次（.github/workflows/run.yml）。GitHub 的定时常会延迟几十分钟，
所以不是"现在正好是那个时间才跑"，而是"上次运行之后，出现过一个该运行的时间点"就跑，延迟了也不会漏掉，
同一个时间点只跑一次。运行记录在 data/runs.json。
"""
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import store as S

RANGES = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]
LOOKBACK_DAYS = 8                 # 最多往回找 8 天（每周一次的主题也够）
FIRST_RUN_WINDOW = timedelta(hours=3)   # 第一次运行：只补最近 3 小时内错过的时间点，免得一配好就把一周前的补跑


def _field(spec, lo, hi):
    out = set()
    for part in spec.split(","):
        step = 1
        if "/" in part:
            part, step = part.split("/")
            step = int(step)
        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            a, b = map(int, part.split("-"))
        else:
            a = b = int(part)
        if not (lo <= a <= hi and lo <= b <= hi and step > 0):
            raise ValueError(f"cron 字段超出范围：{spec}")
        out.update(range(a, b + 1, step))
    return out


def parse(expr):
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError(f"cron 需要 5 个字段（分 时 日 月 周）：{expr!r}")
    sets = [_field(p, lo, hi) for p, (lo, hi) in zip(parts, RANGES)]
    if 7 in sets[4]:
        sets[4] = (sets[4] - {7}) | {0}           # 周日写 0 或 7 都可以
    return sets, parts[2] != "*", parts[4] != "*"


def matches(expr, t, parsed=None):
    (mins, hours, doms, months, dows), dom_set, dow_set = parsed or parse(expr)
    if t.minute not in mins or t.hour not in hours or t.month not in months:
        return False
    dow = (t.weekday() + 1) % 7                   # cron：0 = 周日
    day_ok = [t.day in doms, dow in dows]
    if dom_set and dow_set:                       # 标准 cron：日和周都写了，满足其一即可
        return any(day_ok)
    return all(day_ok)


def tz_of(cfg):
    try:
        return ZoneInfo(cfg.get("timezone") or S.TZ_NAME)
    except ZoneInfoNotFoundError:
        return S.tz()


def latest_slot(expr, now):
    """now 之前（含）最近一个该运行的时间点；往回 LOOKBACK_DAYS 天都没有就返回 None。"""
    parsed = parse(expr)                          # 只解析一次
    t = now.replace(second=0, microsecond=0)
    for _ in range(LOOKBACK_DAYS * 24 * 60):
        if matches(expr, t, parsed):
            return t
        t -= timedelta(minutes=1)
    return None


def runs_path():
    return S.DATA / "runs.json"


def load_runs():
    p = runs_path()
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def record_run(topic, when=None):
    runs = load_runs()
    runs[topic] = (when or S.now()).isoformat(timespec="seconds")
    runs_path().parent.mkdir(parents=True, exist_ok=True)
    runs_path().write_text(json.dumps(runs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def due(topics, now=None):
    """到点该运行的主题（只看 "ai": {"mode": "api"} 且写了 cron 的）。返回 [(主题id, 该运行的时间点)]。"""
    now = now or datetime.now(timezone.utc)
    runs = load_runs()
    out = []
    for tid, cfg in topics.items():
        ai = cfg.get("ai") or {}
        if not cfg.get("enabled", True) or ai.get("mode") != "api" or not cfg.get("cron"):
            continue
        slot = latest_slot(cfg["cron"], now.astimezone(tz_of(cfg)))
        if slot is None:
            continue
        last = runs.get(tid)
        if last:
            if datetime.fromisoformat(last) >= slot:
                continue                          # 这个时间点已经跑过
        elif now - slot > FIRST_RUN_WINDOW:
            continue
        out.append((tid, slot))
    return out
