"""Anthropic（Claude）：Messages API，直接用 HTTP（本项目只用标准库）。

- 第一步 research：带服务器端网页搜索 / 打开网页工具（web_search_20260209 / web_fetch_20260209），
  服务器端的工具循环到上限时会以 stop_reason == "pause_turn" 暂停，原样把回答接回去继续。
- 第二步 structure：不带工具，用 output_config.format（JSON Schema）把第一步的文字整理成结果格式
  （结构化输出不能和引用一起用，所以和搜索分成两步）。
- 默认型号 claude-opus-5-5；开启服务器端兜底（安全分类器拒答时自动换模型重试）。
"""
import json

from . import AIError, post_json

URL = "https://api.anthropic.com/v1/messages"
SEARCH = True
VERSION = "2026-10-21"
DEFAULT_MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_PAUSES = 5


def _headers(key):
    return {"x-api-key": key, "anthropic-version": VERSION, "anthropic-beta": FALLBACK_BETA}


def _body(ai, messages, **extra):
    return {"model": ai.get("model") or DEFAULT_MODEL, "max_tokens": int(ai.get("max_tokens") or 16000),
            "fallbacks": "default", "output_config": {"effort": ai.get("effort") or "medium"},
            "messages": messages, **extra}


def _text(resp):
    return "".join(b.get("text", "") for b in resp.get("content") or [] if b.get("type") == "text")


def _check(resp):
    if resp.get("stop_reason") == "refusal":
        raise AIError(f"Claude 拒绝了这个请求：{(resp.get('stop_details') or {}).get('category') or '未说明原因'}")


def research(task, ai, key):
    tools = []
    if ai.get("web_search", True):
        n = int(ai.get("max_searches") or 8)
        tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": n},
                 {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": n}]
    messages = [{"role": "user", "content": task}]
    for _ in range(MAX_PAUSES + 1):
        resp = post_json(URL, _body(ai, messages, **({"tools": tools} if tools else {})), _headers(key))
        _check(resp)
        if resp.get("stop_reason") != "pause_turn":
            return _text(resp)
        messages = [*messages, {"role": "assistant", "content": resp["content"]}]   # 原样接回，服务器会继续
    raise AIError(f"Claude 的搜索暂停了 {MAX_PAUSES} 次仍未完成")


def structure(text, schema, ai, key):
    prompt = "把下面这段调研结果整理成指定的 JSON 格式。只用文中已有的信息，没有的字段写空字符串。\n\n" + text
    resp = post_json(URL, _body(ai, [{"role": "user", "content": prompt}],
                                output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}}),
                     _headers(key))
    _check(resp)
    try:
        return json.loads(_text(resp))
    except ValueError:
        raise AIError("Claude 的结构化输出无法解析") from None
