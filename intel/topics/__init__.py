"""主题模块。

新增主题时：
- 只需要「搜索 → 判断 → 按 fit 排序推送」的，在 topics.json 加一条配置即可，自动使用 default 模块；
- 需要自己的打分、挑选或排版的，在这里新建 `<主题id，连字符换成下划线>.py`，实现下面三个函数中需要的那几个，
  没实现的自动沿用 default；也可以在配置里写 "module": "<模块名>" 让几个主题共用一个模块。

    prepare(item, cfg) -> item     入库前调用：打分、补字段、判断是否剔除（设 item["rejected"]=True）
    select(items, cfg) -> list     从本轮新条目里挑出要推送的，并排好顺序
    render(item, cfg) -> str       一条推送消息的 HTML（用 render.wrap 保证亮点在第一行）
    header_note: str               （可选）汇报开头的说明，例如评分规则
"""
import importlib

from . import default

HOOKS = ("prepare", "select", "render", "header_note")


class Topic:
    def __init__(self, mod):
        for name in HOOKS:
            setattr(self, name, getattr(mod, name, getattr(default, name, None)))


def module(topic_id, cfg=None):
    name = (cfg or {}).get("module") or topic_id.replace("-", "_")
    try:
        mod = importlib.import_module(f"{__name__}.{name}")
    except ModuleNotFoundError as e:
        if e.name != f"{__name__}.{name}":
            raise                          # 模块存在但内部 import 出错，不要吞掉
        mod = default
    return Topic(mod)
