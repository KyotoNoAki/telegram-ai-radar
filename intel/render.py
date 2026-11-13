"""推送消息的通用排版。每条消息第一行永远是「💡 一句话亮点」，接着是标题、要点、概要，最后是链接。

主题模块（intel/topics/）可以只覆盖中间的正文部分，开头的亮点行和结尾的链接行由这里统一加。
"""
import re

from .schema import UNKNOWN
from .tg import h


def lead_line(i):
    """一句话亮点：优先用 highlight；没有就退回概要第一句 / 标题（只用已有文字，不另编）。"""
    text = i.get("highlight") or ""
    if not text and i.get("summary"):
        text = re.split(r"(?<=[。！？.!?])", i["summary"].strip(), maxsplit=1)[0]
    return f"💡 <b>{h(text or i['title'])}</b>"


def point_values(i, cfg):
    """按主题配置的要点清单（points）取出每个要点：[(显示名, 内容)]；AI 没填的写「待核实」。"""
    vals = i.get("points") or {}
    out = []
    for p in (cfg or {}).get("points") or []:
        key = p["key"] if isinstance(p, dict) else str(p)
        label = (p.get("label") if isinstance(p, dict) else None) or key
        v = vals.get(key)
        v = "、".join(map(str, v)) if isinstance(v, list) else ("" if v is None else str(v).strip())
        out.append((label, v or UNKNOWN))
    return out


def body_points(i, cfg):
    """一句话亮点之后逐条列出要点，再附一两句概要。"""
    lines = [f"<b>{h(label)}</b>：{h(v)}" for label, v in point_values(i, cfg)]
    if i.get("summary"):
        lines.append(h(i["summary"]))
    return lines


def wrap(i, body_lines):
    """亮点行 + 标题 + 正文 + 链接。"""
    lines = [lead_line(i), f"<b>{h(i['title'])}</b>", *body_lines]
    lines.append(f'<a href="{h(i["url"])}">→ 打开原文</a>')
    return "\n".join(lines)
