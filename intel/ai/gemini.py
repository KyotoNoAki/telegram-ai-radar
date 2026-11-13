"""Google Gemini：generateContent，带 Google 搜索。型号必须在配置里写明。"""
import json
from urllib.parse import quote

from . import AIError, post_json

BASE = "https://generativelanguage.googleapis.com/v1beta/models"
SEARCH = True


def _url(ai):
    if not ai.get("model"):
        raise AIError("Gemini 需要在配置的 ai.model 里写明型号")
    return f"{BASE}/{quote(ai['model'])}:generateContent"


def _text(resp):
    cands = resp.get("candidates") or []
    if not cands:
        raise AIError(f"Gemini 没有返回内容：{(resp.get('promptFeedback') or {}).get('blockReason') or '未说明原因'}")
    return "".join(p.get("text", "") for p in (cands[0].get("content") or {}).get("parts") or [])


def research(task, ai, key):
    body = {"contents": [{"role": "user", "parts": [{"text": task}]}]}
    if ai.get("web_search", True):
        body["tools"] = [{"google_search": {}}]
    return _text(post_json(_url(ai), body, {"x-goog-api-key": key}))


def structure(text, schema, ai, key):
    # 只要求返回 JSON，不传 schema：各版本对 JSON Schema 关键字的支持不一样，格式由提示词和解析把关
    body = {"contents": [{"role": "user", "parts": [{"text":
            "把下面这段调研结果整理成 JSON，结构为 " + json.dumps(schema, ensure_ascii=False)
            + "。只用文中已有的信息，没有的字段写空字符串。\n\n" + text}]}],
            "generationConfig": {"responseMimeType": "application/json"}}
    try:
        return json.loads(_text(post_json(_url(ai), body, {"x-goog-api-key": key})))
    except ValueError:
        raise AIError("Gemini 的 JSON 输出无法解析") from None
