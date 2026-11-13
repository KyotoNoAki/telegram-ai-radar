"""OpenAI 兼容接口（DeepSeek、本地模型等）：/chat/completions。

这类接口一般不能自己联网搜索，所以只判断 RSS 抓来的候选（主题要配置 feeds）。
配置里要写 base_url（例如 https://api.deepseek.com）和 model；API key 默认读 OPENAI_COMPATIBLE_API_KEY，
也可以用 api_key_env 指定别的环境变量名。
"""
import json

from . import AIError, post_json

SEARCH = False


def _call(ai, key, content, **extra):
    if not ai.get("base_url") or not ai.get("model"):
        raise AIError("OpenAI 兼容接口需要在配置的 ai 里写明 base_url 和 model")
    url = ai["base_url"].rstrip("/") + "/chat/completions"
    resp = post_json(url, {"model": ai["model"], "messages": [{"role": "user", "content": content}], **extra},
                     {"Authorization": f"Bearer {key}"})
    try:
        return resp["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise AIError(f"接口返回格式不认识：{str(resp)[:200]}") from None


def research(task, ai, key):
    return _call(ai, key, task)


def structure(text, schema, ai, key):
    out = _call(ai, key, "把下面这段内容整理成 JSON，结构为 " + json.dumps(schema, ensure_ascii=False)
                + "。只用文中已有的信息，没有的字段写空字符串。\n\n" + text,
                response_format={"type": "json_object"})
    try:
        return json.loads(out)
    except ValueError:
        raise AIError("接口的 JSON 输出无法解析") from None
