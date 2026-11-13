"""telegram-ai-radar 命令行。

整条流程：生成工作说明 → AI 搜索、核实、判断 → 交回结果（统一格式）→ 去重入库 → 生成推送 → 发送 → 按钮标记写回资料库

和 AI 配合：
  python -m intel prompt <主题>                     方案 A：打印给 AI 代理的工作说明（按主题配置生成）
  python -m intel ingest <主题> <result.json>       方案 A：AI 交回的结果入库、去重、生成推送
  python -m intel run    <主题>                     方案 B：直接调用配置里的 AI 接口，跑完整一轮
  python -m intel run-due                           方案 B：运行所有到点的主题（GitHub Actions 每小时调用）

其他：
  python -m intel fetch  <主题> [out.json] [--live] 查看 RSS 候选：优先读 Action 抓好的快照，过期才现抓
  python -m intel report <主题>                     把还没推送的条目重新生成一份推送
  python -m intel pending-deep [主题]               列出点了「感兴趣」、还没写深度内容的条目
  python -m intel deep <主题> <id> <文章.md>        把写好的深度内容入库并放进待发送
  python -m intel export                            导出全部条目为 CSV

GitHub Action 用：
  python -m intel fetch-all  抓取所有配置了 feeds 的主题，存成 data/fetched/<topic>.json
  python -m intel deliver    发送 outbox 里的汇报
  python -m intel sync       拉取按钮点击，写回资料库
  python -m intel forward <chat_id> [主题,主题]   把最近的汇报转发到群（测试用，不带按钮）
"""
import csv
import json
import logging
import os
import sys

from . import fetch as F
from . import render as R
from . import schema
from . import store as S
from . import tg
from . import topics as T
from .env import load_dotenv
from .tg import h


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------- 候选、去重、入库 ----------
def cmd_fetch(topic, out=None, *flags):
    """输出一个主题的 RSS 候选：优先用 GitHub Action 抓好的快照，没有或过期才现抓（加 --live 强制现抓）。"""
    if out and out.startswith("--"):
        flags, out = (out, *flags), None
    snap = None if "--live" in flags else F.load_snapshot(topic)
    if snap is not None:
        cands = snap.get("candidates", [])
    else:
        cands, _ = F.fetch_topic(S.load_topics()[topic])
    text = json.dumps(cands, ensure_ascii=False, indent=2)
    if out:
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        print(text)


def cmd_fetch_all():
    """GitHub Action 用：抓取所有配置了 feeds 的主题，存成 data/fetched/<topic>.json。任何源失败都不让任务失败。"""
    gha = os.environ.get("GITHUB_ACTIONS") == "true"
    for topic, cfg in S.load_topics().items():
        if not cfg.get("enabled", True) or not cfg.get("feeds"):
            continue
        cands, results = F.fetch_topic(cfg)
        p = F.save_snapshot(topic, cands, results)
        ok = sum(r["ok"] for r in results)
        print(f"{topic}: {ok}/{len(results)} 个源成功，{len(cands)} 条候选 -> {p.relative_to(S.ROOT)}")
        for r in results:
            if not r["ok"] and gha:            # 在 Action 的运行摘要里显示成黄色警告
                print(f"::warning title=抓取失败 {topic}::{r['source']} <{r['url']}> — {r['error']}")


def filter_new(topic, candidates):
    """去掉资料库里已有的（同网址，或同名且被你点过「不需要」的），以及本批内部的重复。"""
    existing = S.items(topic)
    marks = S.marks_by_item()
    ids = {i["id"] for i in existing}
    tkeys = {S.title_key(topic, i["title"]): i for i in existing}
    out, seen_ids, seen_tk = [], set(), set()
    for c in candidates:
        if not c.get("url") or not c.get("title"):
            continue
        iid = S.item_id(c["url"])
        tk = S.title_key(topic, c["title"])
        if iid in ids or iid in seen_ids or tk in seen_tk:
            continue
        old = tkeys.get(tk)
        if old:
            if S.status_of(old, marks) == "rejected":
                continue                      # 同一件事换了网址，你点过「不需要」→ 跳过
            c["previously_seen"] = old["id"]
        seen_ids.add(iid)
        seen_tk.add(tk)
        out.append(c)
    return out


def add_rows(topic, judged):
    """AI 判断后的条目整理成统一格式入库。返回 (新增, 重复, 无效) 条数。"""
    cfg = S.load_topics()[topic]
    mod = T.module(topic, cfg)
    rows = S.items(topic)
    ids = {r["id"] for r in rows}
    seen_tk = set()
    added = dup = bad = 0
    for j in judged:
        try:
            it = schema.normalize(j, topic, cfg)
        except ValueError as e:
            print(f"  skip: {e}", file=sys.stderr)
            bad += 1
            continue
        tk = S.title_key(topic, it["title"])
        if it["id"] in ids or tk in seen_tk:  # 同一网址已入库，或本批里同一件事出现两次
            dup += 1
            continue
        it = mod.prepare(it, cfg)
        it["reported_at"] = None
        rows.append(it)
        ids.add(it["id"])
        seen_tk.add(tk)
        added += 1
    S.write_jsonl(S.ITEMS / f"{topic}.jsonl", rows)
    print(f"added {added}, duplicates {dup}, invalid {bad}, total {len(rows)}")
    return added, dup, bad


# ---------- 生成推送 ----------
def is_digest(t):
    return t.get("layout") == "digest"


def save_outbox(topic, rep, suffix=""):
    S.OUTBOX.mkdir(parents=True, exist_ok=True)
    fname = S.OUTBOX / f"{rep['created_at'].replace(':', '')}-{topic}{suffix}.json"
    fname.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    return fname


def chunk(blocks, limit=3500):
    """把若干段文字拼成几条消息，每条不超过 limit 字（Telegram 单条上限 4096）。"""
    chunks, cur = [], ""
    for b in blocks:
        if cur and len(cur) + len(b) > limit:
            chunks.append(cur)
            cur = ""
        cur += ("\n\n" if cur else "") + b
    if cur:
        chunks.append(cur)
    return chunks


def digest_report(topic, t, lead=""):
    """汇总版式：一天一条（太长时自动分几条），每条新闻一段。"""
    rows = S.items(topic)
    marks = S.marks_by_item()
    fresh = [r for r in rows if not r.get("reported_at") and S.status_of(r, marks) != "rejected"]
    shown = T.module(topic, t).select(fresh, t)
    d = S.today()
    header = f"📰 <b>{h(t['name'])}</b> · {d.month}/{d.day}"
    if lead:
        header += f"\n\n💡 <b>{h(lead)}</b>"
    if not shown:
        header += "\n\n今天没有值得看的新内容。"
    blocks = []
    for n, r in enumerate(shown, 1):
        b = f"<b>{n}. {h(r['title'])}</b>"
        if r.get("highlight"):
            b += f"\n{h(r['highlight'])}"
        if t.get("points"):                   # 用户自定义的要点，汇总版式里排成一行
            b += "\n" + " · ".join(f"{h(label)}：{h(v)}" for label, v in R.point_values(r, t))
        b += f'\n<a href="{h(r["url"])}">{h(r.get("source") or "原文")}</a>'
        blocks.append(b)
    chunks = chunk(blocks) or [""]
    chunks[0] = header + ("\n\n" + chunks[0] if chunks[0] else "")   # 标题和第一批合成一条
    stamp = S.now_iso()
    fname = save_outbox(topic, {"topic": topic, "created_at": stamp, "header": "",
                                "items": [{"id": f"digest-{i}", "text": c, "buttons": False}
                                          for i, c in enumerate(chunks)]})
    for r in fresh:                           # 没挑中的（低匹配）也记为已处理，不留到明天
        r["reported_at"] = stamp
    S.write_jsonl(S.ITEMS / f"{topic}.jsonl", rows)
    print(f"digest -> {fname.name} ({len(shown)} items, {len(chunks)} message(s))")
    return fname


def cmd_report(topic, lead=""):
    """把还没推送的条目生成一份推送放进 outbox：cards 版式一条一张卡片，digest 版式汇总成一条。"""
    t = S.load_topics()[topic]
    if is_digest(t):
        return digest_report(topic, t, lead)
    mod = T.module(topic, t)
    rows = S.items(topic)
    marks = S.marks_by_item()
    fresh = [r for r in rows if not r.get("reported_at") and S.status_of(r, marks) == "new"]
    # 同一件事去重：同名（去掉年份后）的条目推送过就不再推
    fresh_ids = {r["id"] for r in fresh}
    pushed = {S.title_key(topic, r["title"]) for r in rows
              if r.get("reported_at") and r["id"] not in fresh_ids}
    cands, dups, seen = [], [], set()
    for r in fresh:
        k = S.title_key(topic, r["title"])
        if k in seen or k in pushed:
            dups.append(r)
            continue
        seen.add(k)
        cands.append(r)
    shown = mod.select(cands, t)
    shown_ids = {r["id"] for r in shown}
    rejected = [r for r in cands if r.get("rejected")]
    low = [r for r in cands if r["id"] not in shown_ids and not r.get("rejected") and r.get("fit") == "low"]
    d = S.today()
    header = [f"📡 <b>{h(t['name'])}</b> · {d.month}/{d.day}",
              f"推送 {len(shown)} 条" + (f"（另有 {len(low)} 条不太相关，已入库）" if low else "")]
    if rejected:
        header.append(f"🚩 剔除 {len(rejected)} 条：" + "、".join(h(r["title"]) for r in rejected))
    if shown:
        top = shown[0]
        header.append(f"\n🏆 {h(t.get('top_label') or '最值得看')}：{h(top['title'])}")
        if top.get("highlight"):
            header.append(f"　　{h(top['highlight'])}")
    else:
        header.append("本轮没有值得看的新条目。")
    if shown and mod.header_note:
        header.append("\n" + h(mod.header_note))
    stamp = S.now_iso()
    fname = save_outbox(topic, {"topic": topic, "created_at": stamp, "header": "\n".join(header),
                                "items": [{"id": r["id"], "text": mod.render(r, t)} for r in shown]})
    # 推送的、不相关的、剔除的、重复的都记为已处理；合格但这期没挑中的（超过 max_items）留到以后
    done = shown_ids | {r["id"] for r in low + rejected + dups}
    for r in rows:
        if r["id"] in done:
            r["reported_at"] = r.get("reported_at") or stamp
    S.write_jsonl(S.ITEMS / f"{topic}.jsonl", rows)
    print(f"report -> {fname.name} ({len(shown)} items, {len(rejected)} rejected, {len(dups)} duplicate)")
    return fname


# ---------- 深度内容（可选） ----------
def cmd_pending_deep(topic=None):
    """列出点了「感兴趣」但还没写深度内容的条目；不给主题就列出所有开启 deep_dive 的主题。"""
    marks = S.marks_by_item()
    topics = S.load_topics()
    names = [topic] if topic else [k for k, v in topics.items() if v.get("deep_dive")]
    out = [{"topic": t, "deep_prompt": topics[t].get("deep_prompt") or "", "id": r["id"],
            "title": r["title"], "url": r["url"], "highlight": r.get("highlight"),
            "summary": r.get("summary"), "points": r.get("points")}
           for t in names for r in S.items(t)
           if S.status_of(r, marks) == "interested" and not r.get("deep_at")]
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
    print(f"\n# {len(out)} pending deep dive(s)", file=sys.stderr)


def md_to_tg(text):
    """把简单 Markdown（## 标题、**粗体**、- 列表）转成 Telegram HTML。"""
    import re
    out = []
    for line in text.splitlines():
        l = h(line.rstrip())
        if l.startswith("#"):
            l = "<b>" + l.lstrip("#").strip() + "</b>"
        l = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", l)
        if l.startswith("- "):
            l = "• " + l[2:]
        out.append(l)
    return "\n".join(out).strip()


def cmd_deep(topic, iid, path):
    """把写好的深度内容入库（data/deep/<id>.md）并放进待发送，按段落分成几条消息。"""
    rows = S.items(topic)
    r = next((x for x in rows if x["id"] == iid), None)
    if not r:
        sys.exit(f"no item {iid}")
    with open(path, encoding="utf-8") as f:
        text = f.read().strip()
    deep_dir = S.DATA / "deep"
    deep_dir.mkdir(parents=True, exist_ok=True)
    (deep_dir / f"{iid}.md").write_text(f"# {r['title']}\n\n{text}\n", encoding="utf-8")
    chunks = chunk(md_to_tg(text).split("\n\n"))
    n = len(chunks)
    label = S.load_topics().get(topic, {}).get("deep_label") or "📖 深度阅读"
    items = [{"id": f"deep-{iid}-{k}", "buttons": False,
              "text": f"<b>{h(label)}｜{h(r['title'])}</b>" + (f"（{k}/{n}）" if n > 1 else "") + "\n\n" + c}
             for k, c in enumerate(chunks, 1)]
    stamp = S.now_iso()
    fname = save_outbox(topic, {"topic": topic, "kind": "deep", "created_at": stamp, "header": "", "items": items},
                        suffix=f"-deep-{iid}")
    r["deep_at"] = stamp
    S.write_jsonl(S.ITEMS / f"{topic}.jsonl", rows)
    print(f"deep -> {fname.name} ({n} message(s), {len(text)} chars)")


# ---------- 和 AI 配合 ----------
def known_items(topic, limit=80):
    """已经推送过的条目（标题和网址），写进工作说明里让 AI 别再报。"""
    rows = [r for r in S.items(topic) if r.get("reported_at")]
    return [{"title": r["title"], "url": r["url"]} for r in rows[-limit:]]


def feed_candidates(topic, cfg):
    if not cfg.get("feeds"):
        return []
    snap = F.load_snapshot(topic)
    return snap.get("candidates", []) if snap is not None else F.fetch_topic(cfg)[0]


def task_for(topic, web=True):
    from . import ai as A
    cfg = S.load_topics()[topic]
    return A.build_task(cfg, known_items(topic), feed_candidates(topic, cfg), web=web)


def cmd_prompt(topic):
    """方案 A：打印给 AI 代理的工作说明（按主题配置生成）。"""
    print(task_for(topic))


def ingest(topic, obj):
    """把 AI 交回的结果去重、入库、生成推送。"""
    from . import ai as A
    rows = A.clean_items(obj, S.load_topics()[topic])
    new = filter_new(topic, [dict(r) for r in rows])
    new = [r for r in new if not r.get("previously_seen")]      # 同名的旧条目：当作同一件事，不再推
    print(f"AI 交回 {len(rows)} 条，其中新的 {len(new)} 条")
    add_rows(topic, new)
    return cmd_report(topic, lead=str(obj.get("lead") or ""))


def cmd_ingest(topic, path):
    """方案 A：AI 代理把结果存成 JSON 文件后，用这条命令入库并生成推送。"""
    from . import ai as A
    with open(path, encoding="utf-8") as f:
        obj = A.parse_result(f.read())
    if obj is None:
        sys.exit("结果文件里找不到 {\"items\": [...]} 格式的 JSON")
    return ingest(topic, obj)


def cmd_run(topic):
    """方案 B：按主题配置调用 AI 接口，拿到结果后入库并生成推送。"""
    from . import ai as A
    from . import cron
    cfg = S.load_topics()[topic]
    ai = cfg.get("ai") or {}
    web = bool(ai.get("web_search", True)) and getattr(A.provider(ai.get("provider", "")), "SEARCH", False)
    obj = A.ask(cfg, task_for(topic, web=web))
    cron.record_run(topic)
    return ingest(topic, obj)


def cmd_run_due():
    """GitHub Actions 每小时调用：运行所有到点的主题。一个主题失败只警告，不影响其他主题。"""
    from . import cron
    due = cron.due(S.load_topics())
    print(f"到点的主题：{', '.join(t for t, _ in due) or '无'}")
    failed = 0
    for tid, slot in due:
        try:
            cmd_run(tid)
        except Exception as e:
            failed += 1
            cron.record_run(tid)                   # 失败也记一笔：不在下一个小时重复付费重试，等下一个时间点
            print(f"::warning title=主题运行失败 {tid}::{e}")
    print(f"ran {len(due) - failed}/{len(due)} topic(s)")


# ---------- 导出 ----------
def fetched_key(r):
    """按真实时间排序：时间戳可能带不同的时区，按字符串排会乱。"""
    from datetime import datetime, timezone
    try:
        t = datetime.fromisoformat(r.get("fetched_at") or "")
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def cmd_export():
    marks = S.marks_by_item()
    topics = S.load_topics()
    S.EXPORTS.mkdir(exist_ok=True)
    cols = ["topic", "status", "fit", "title", "highlight", "points", "summary", "source", "url", "fetched_at", "id"]
    path = S.EXPORTS / "all_items.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for i in sorted(S.all_items(), key=fetched_key, reverse=True):
            row = dict(i, status=S.STATUS_LABEL[S.status_of(i, marks)])
            row["points"] = "；".join(f"{label}：{v}" for label, v in R.point_values(i, topics.get(i["topic"], {})))
            w.writerow(["" if row.get(c) is None else row.get(c) for c in cols])
    print(f"exported -> {path.relative_to(S.ROOT)}")


# ---------- 发送 ----------
def team_group():
    return os.environ.get("TELEGRAM_TEAM_GROUP_ID", "").strip()


def shares_to_group(rep, tcfg, name):
    """只有配置了 share_to_group 的主题的正式汇报才发到群里；深度内容是你点出来的，只发给你。"""
    return bool(team_group() and tcfg.get("share_to_group")
                and rep.get("kind") != "deep" and "-deep-" not in name)


def send_to_group(rep, save):
    """把汇报抄送到群里（不带按钮：群里点按钮不会被记录）。每发一条记一笔，补发时不重复。
    群那边失败只警告，不影响你的私信；群升级为超级群组时写明新的 chat_id。"""
    group = team_group()
    try:
        if rep.get("header") and not rep.get("group_header_sent"):
            tg.send(rep["header"], chat=group)
            rep["group_header_sent"] = True
            save()
        for it in rep["items"]:
            if it.get("group_message_id"):
                continue
            it["group_message_id"] = tg.send(it["text"], chat=group)["message_id"]
            save()
    except tg.TelegramError as e:
        new = e.parameters.get("migrate_to_chat_id")
        hint = (f"群已升级为超级群组，新的 chat_id 是 {new}，请把 Secret TELEGRAM_TEAM_GROUP_ID 改成它"
                if new else e.description)
        print(f"::warning title=群推送失败::{rep.get('topic')}：{hint}（你的私信不受影响）")
        rep["group_error"] = hint
        return False
    except Exception as e:
        print(f"::warning title=群推送失败::{rep.get('topic')}：{e}（你的私信不受影响）")
        rep["group_error"] = str(e)
        return False
    return True


def cmd_deliver():
    """发送 outbox 里的汇报。每发出一条就记下 message_id；中途失败时下次只补发没发出的，不会重复推送。
    配置了 share_to_group 的主题，私信发完后再抄送一份到群（TELEGRAM_TEAM_GROUP_ID）。"""
    S.SENT.mkdir(parents=True, exist_ok=True)
    files = sorted(S.OUTBOX.glob("*.json"))
    done = failed = shared = 0
    for p in files:
        rep = load_json(p)
        tcfg = S.load_topics().get(rep.get("topic"), {})
        labels, codes = tcfg.get("buttons"), tcfg.get("button_codes")

        def save():
            p.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            if rep.get("header") and not rep.get("header_sent"):
                tg.send(rep["header"])
                rep["header_sent"] = True
                save()
            for it in rep["items"]:
                if it.get("message_id"):
                    continue                  # 上次已经发出
                buttons = tg.mark_buttons(it["id"], labels, codes) if it.get("buttons", True) else None
                if tcfg.get("share_button") and rep.get("kind") != "deep" and "-deep-" not in p.name:
                    buttons = tg.with_share(buttons)   # 不进群的主题：卡片上可以一键发到群
                msg = tg.send(it["text"], buttons)
                it["message_id"] = msg["message_id"]
                save()
        except Exception as e:
            failed += 1
            sent = sum(1 for it in rep["items"] if it.get("message_id"))
            print(f"::warning::{p.name} 发送中断（已发 {sent}/{len(rep['items'])} 条），下次运行补发剩下的：{e}")
            continue
        if shares_to_group(rep, tcfg, p.name) and send_to_group(rep, save):
            shared += 1
        rep["sent_at"] = S.now_iso()
        (S.SENT / p.name).write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
        p.unlink()
        done += 1
    print(f"delivered {done} report(s)" + (f", {shared} also to team group" if shared else "")
          + (f", {failed} failed (kept in outbox for retry)" if failed else ""))


def latest_sent(topic):
    """某个主题最近发出的一份汇报（不含深度内容）。"""
    from datetime import datetime
    reps = []
    for p in S.SENT.glob(f"*-{topic}.json"):
        rep = load_json(p)
        if rep.get("topic") == topic:
            reps.append((datetime.fromisoformat(rep.get("sent_at") or rep["created_at"]), rep))
    return max(reps, key=lambda x: x[0])[1] if reps else None


def cmd_forward(chat, topics=""):
    """把各主题最近发出的汇报转发到指定的群（测试用）：不带按钮，群里点按钮不会被记录。
    不指定主题时，转发所有开启了 share_to_group 的主题。"""
    chat = str(chat).strip()
    if not chat:
        sys.exit("没有给群的 chat_id（工作流的 chat_id 输入，或 Secret TELEGRAM_TEAM_GROUP_ID）")
    names = [x.strip() for x in topics.split(",") if x.strip()]
    if not names:
        names = [k for k, v in S.load_topics().items() if v.get("share_to_group")]
    reps = [(t, latest_sent(t)) for t in names]
    missing = [t for t, r in reps if not r]
    reps = [(t, r) for t, r in reps if r]
    if missing:
        print(f"这些主题还没有发出过汇报，跳过：{'、'.join(missing)}")
    if not reps:
        sys.exit("没有可以转发的汇报")
    n = 0
    try:
        tg.send("🧪 <b>测试转发</b>：最近搜集的内容（按钮已去掉）", chat=chat)
        for t, rep in reps:
            if rep.get("header"):
                tg.send(rep["header"], chat=chat)
                n += 1
            for it in rep["items"]:
                tg.send(it["text"], chat=chat)
                n += 1
    except tg.TelegramError as e:
        new = e.parameters.get("migrate_to_chat_id")
        if new:
            print(f"::error title=群已升级为超级群组::{chat} 已经变成 {new}，请用新的 chat_id 重新运行，"
                  f"并把 TELEGRAM_TEAM_GROUP_ID 改成 {new}")
        elif "not found" in e.description or "kicked" in e.description or "not a member" in e.description:
            print(f"::error title=发不到群里::{e.description}。请确认 bot 已经加入这个群、chat_id 正确")
        else:
            print(f"::error title=发送失败::{e.description}")
        sys.exit(1)
    print(f"forwarded {n} message(s) from {len(reps)} report(s) to chat …{chat[-4:]}: "
          + "、".join(t for t, _ in reps))


# ---------- 按钮同步 ----------
def worker_url():
    f = S.ROOT / "worker.json"
    if not f.exists():
        return None
    return (json.loads(f.read_text(encoding="utf-8")).get("url") or "").rstrip("/") or None


def derive(purpose):
    import hashlib, hmac
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    return hmac.new(token.encode(), purpose.encode(), hashlib.sha256).hexdigest()[:32]


def http_json(method, url, token, body=None):
    import urllib.request
    req = urllib.request.Request(url, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token}",
                                          "Content-Type": "application/json",
                                          "User-Agent": "telegram-ai-radar"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def cmd_sync():
    w = worker_url()
    if not w or tg.dry():
        return sync_polling()
    hook = f"{w}/tg"
    info = tg.call("getWebhookInfo")
    if info.get("url") != hook:
        print(f"webhook not set (current: {info.get('url') or 'none'}) -> drain polling first, then set")
        sync_polling()
        tg.call("setWebhook", url=hook, secret_token=derive("intel-webhook"),
                allowed_updates=["callback_query", "message"])
        print(f"webhook set -> {hook}")
    elif info.get("last_error_message"):
        print(f"webhook last error: {info.get('last_error_message')}")
    sync_worker(w)


def migration_text(old, new):
    team = os.environ.get("TELEGRAM_TEAM_GROUP_ID", "").strip()
    hit = "（就是 TELEGRAM_TEAM_GROUP_ID）" if team and team == str(old) else ""
    return (f"群已升级为超级群组，chat_id 变了：{old} → {new}{hit}。"
            f"请把 TELEGRAM_TEAM_GROUP_ID 改成 {new}（本地 .env、GitHub Secrets、Cloudflare 都要改）")


def report_migration(msg, notify=False):
    """普通群升级为超级群组时，Telegram 会发一条带 migrate_to_chat_id 的服务消息，群的 chat_id 随之改变。

    提示要发到你会看到的地方：
    - Action 运行页上的黄色警告（::warning::），并留底到 data/migrations.jsonl；
    - notify=True 时直接给你发 Telegram 私信。走 Worker 的路径由 Worker 当场发私信，这里不再重复发。
    旧群和新群各会收到一条服务消息，同一次迁移只提示一次。返回是否是新的迁移。"""
    chat = (msg.get("chat") or {}).get("id")
    if "migrate_to_chat_id" in msg:            # 旧群收到：我搬到了新 ID
        old, new = chat, msg["migrate_to_chat_id"]
    elif "migrate_from_chat_id" in msg:        # 新超级群组收到：我是从旧 ID 搬来的
        old, new = msg["migrate_from_chat_id"], chat
    elif "from" in msg and "to" in msg:        # Worker 存进 KV 的记录
        old, new = msg["from"], msg["to"]
    else:
        return False
    log_path = S.DATA / "migrations.jsonl"
    if any(str(m.get("from")) == str(old) and str(m.get("to")) == str(new) for m in S.read_jsonl(log_path)):
        print(f"  migration {old} -> {new} already reported")
        return False
    text = migration_text(old, new)
    print(f"::warning title=群 chat_id 变了::{text}" if os.environ.get("GITHUB_ACTIONS") == "true" else f"!!! {text}")
    logging.warning(text)
    S.append_jsonl(log_path, {"from": old, "to": new, "at": S.now_iso()})
    if notify:
        try:
            tg.send("⚠️ " + h(text))
        except Exception as e:                 # 私信发不出去不影响同步，Action 页上还有警告
            print(f"  (migration notice not sent: {e})")
    return True


def sync_worker(w):
    token = derive("intel-pull")
    rows = http_json("GET", f"{w}/marks", token)
    print(f"got {len(rows)} stored click(s) from worker")
    ignored = S.DATA / "ignored.jsonl"
    n = 0
    for r in rows:
        if r["key"].startswith("migrate:"):
            report_migration(r)
            continue
        if r["key"].startswith("bad:") or r.get("code") not in S.MARK_CODES:
            S.append_jsonl(ignored, {"reason": "bad data", "data": r.get("data"), "at": S.now_iso()})
            continue
        S.append_jsonl(S.MARKS, {"id": r["id"], "status": S.MARK_CODES[r["code"]],
                                 "at": r.get("at") or S.now_iso()})
        n += 1
        print(f"  mark {r['id']} -> {S.MARK_CODES[r['code']]}")
    if rows:
        http_json("POST", f"{w}/marks/ack", token, {"keys": [r["key"] for r in rows]})
    print(f"synced {n} mark(s)")


def sync_polling():
    state = S.load_state()
    offset = state.get("tg_offset", 0)
    updates = tg.call("getUpdates", offset=offset, timeout=0,
                      allowed_updates=["callback_query", "message"])
    owner = str(tg.chat_id()).strip()
    n = 0
    print(f"got {len(updates)} update(s) from offset {offset}")
    ignored = S.DATA / "ignored.jsonl"

    def skip(u, reason, **extra):            # 不认识的更新也留底，绝不悄悄丢掉
        S.append_jsonl(ignored, {"update_id": u.get("update_id"), "reason": reason,
                                 "kinds": [k for k in u if k != "update_id"], **extra,
                                 "at": S.now_iso()})
        print(f"  skip {u.get('update_id')}: {reason} {extra}")

    for u in updates:
        offset = max(offset, u["update_id"] + 1)
        if "message" in u:
            chat = u["message"]["chat"]
            print(f"  message from chat_id=…{str(chat['id'])[-3:]}")
            report_migration(u["message"], notify=True)
            continue
        q = u.get("callback_query")
        if not q:
            skip(u, "not a button click")
            continue
        msg = q.get("message") or {}
        from_chat = str(msg.get("chat", {}).get("id", "")) or str(q.get("from", {}).get("id", ""))
        if from_chat != owner:              # 不是本人 → 忽略
            skip(u, "not owner", chat_tail=from_chat[-3:])
            continue
        if q.get("data") == "g":
            skip(u, "share button needs the Worker")   # 「发到群」由 Worker 即时处理，轮询路径不处理
            continue
        try:
            _, iid, code = q["data"].split(":")
            status = S.MARK_CODES[code]
        except (ValueError, KeyError):
            skip(u, "bad data", data=q.get("data"))
            continue
        S.append_jsonl(S.MARKS, {"id": iid, "status": status, "at": S.now_iso()})
        n += 1
        label = S.STATUS_LABEL[status]
        print(f"  mark {iid} -> {status}")
        try:
            tg.call("answerCallbackQuery", callback_query_id=q["id"], text=f"已标记：{label}")
        except Exception:
            pass                             # 超过时限的回调无法应答，不影响记录
        try:
            nxt = "n" if status != "rejected" else "i"
            hint = "点此改为不需要" if status != "rejected" else "点此改为感兴趣"
            tg.call("editMessageReplyMarkup", chat_id=owner, message_id=msg["message_id"],
                    reply_markup={"inline_keyboard": [[
                        {"text": f"已标记：{label}（{hint}）", "callback_data": f"m:{iid}:{nxt}"}]]})
        except Exception as e:
            print(f"  (button not updated: {e})")   # 改按钮失败不影响记录
    state["tg_offset"] = offset
    S.save_state(state)
    print(f"synced {n} mark(s)")


def main(argv):
    cmds = {
        "prompt": cmd_prompt, "ingest": cmd_ingest, "run": cmd_run, "run-due": cmd_run_due,
        "fetch": cmd_fetch, "report": cmd_report, "pending-deep": cmd_pending_deep, "deep": cmd_deep,
        "export": cmd_export, "fetch-all": cmd_fetch_all, "deliver": cmd_deliver, "sync": cmd_sync,
        "forward": cmd_forward,
    }
    load_dotenv(S.ROOT / ".env")
    logging.basicConfig(level=os.environ.get("INTEL_LOG_LEVEL", "INFO"), stream=sys.stderr,
                        format="%(levelname)s %(name)s: %(message)s")
    if not argv or argv[0] not in cmds:
        print(__doc__)
        return 1
    cmds[argv[0]](*argv[1:])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
