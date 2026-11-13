"""Telegram 收发。INTEL_DRY_RUN=1 时只打印、不联网（本地测试用）。"""
import json
import os
import urllib.error
import urllib.request
from html import escape as h

_fake_id = [1000]


def dry():
    return os.environ.get("INTEL_DRY_RUN") == "1"


def chat_id():
    return os.environ.get("TELEGRAM_CHAT_ID", "")


def call(method, **params):
    if dry():
        print(f"[dry-run] {method}: {json.dumps(params, ensure_ascii=False)[:400]}")
        _fake_id[0] += 1
        if method == "getUpdates":
            path = os.environ.get("INTEL_FAKE_UPDATES")
            return json.load(open(path, encoding="utf-8")) if path else []
        return {"message_id": _fake_id[0]}
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(params).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = json.load(r)
    except urllib.error.HTTPError as e:      # Telegram 的错误（如群已升级）带说明，读出来再报
        try:
            body = json.load(e)
        except ValueError:
            raise e from None
    if not body.get("ok"):
        raise TelegramError(method, body)
    return body["result"]


class TelegramError(RuntimeError):
    """Telegram 返回的错误。群升级为超级群组后再往旧 ID 发，parameters 里会带 migrate_to_chat_id。"""

    def __init__(self, method, body):
        self.description = body.get("description", "")
        self.parameters = body.get("parameters") or {}
        super().__init__(f"Telegram {method} failed: {self.description or body}")


def send(text, buttons=None, chat=None):
    params = {
        "chat_id": chat or chat_id(),
        "text": text[:4000],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if buttons:
        params["reply_markup"] = {"inline_keyboard": buttons}
    return call("sendMessage", **params)


DEFAULT_LABELS = ["⭐ 感兴趣", "✖ 不需要", "✔ 已处理"]


def mark_buttons(item_id, labels=None, codes=None):
    labels = labels or DEFAULT_LABELS
    codes = codes or ["i", "n", "d"]
    return [[{"text": t, "callback_data": f"m:{item_id}:{c}"} for t, c in zip(labels, codes)]]


SHARE_BUTTON = {"text": "📤 发到群", "callback_data": "g"}   # Worker 收到后把这张卡片发到团队群


def with_share(buttons):
    """在按钮下面加一行「📤 发到群」；原来没有按钮的消息（如汇总版式）就只有这一个。"""
    return (buttons or []) + [[dict(SHARE_BUTTON)]]


__all__ = ["call", "send", "mark_buttons", "h", "chat_id", "dry", "TelegramError", "with_share"]
