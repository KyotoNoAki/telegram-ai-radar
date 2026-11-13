"""OpenAI：Responses API（/v1/responses），带网页搜索工具。型号必须在配置里写明。"""
import json

from . import AIError, post_json

URL = "https://api.openai.com/v1/responses"
SEARCH = True


def _model(ai):
    if not ai.get("model"):
        raise AIError("OpenAI 需要在配置的 ai.model 里写明型号")
    return ai["model"]


def _text(resp):
    out = []
    for item in resp.get("output") or []:
        if item.get("type") == "message":
            out += [c.get("text", "") for c in item.get("content") or [] if c.get("type") == "output_text"]
    return "".join(out)


def research(task, ai, key):
    body = {"model": _model(ai), "input": task}
    if ai.get("web_search", True):
        body["tools"] = [{"type": "web_search"}]
    return _text(post_json(URL, body, {"Authorization": f"Bearer {key}"}))


def structure(text, schema, ai, key):
    body = {"model": _model(ai),
            "input": "把下面这段调研结果整理成指定的 JSON 格式。只用文中已有的信息，没有的字段写空字符串。\n\n" + text,
            "text": {"format": {"type": "json_schema", "name": "result", "schema": schema, "strict": True}}}
    try:
        return json.loads(_text(post_json(URL, body, {"Authorization": f"Bearer {key}"})))
    except ValueError:
        raise AIError("OpenAI 的结构化输出无法解析") from None
