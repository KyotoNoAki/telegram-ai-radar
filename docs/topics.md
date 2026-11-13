# 主题配置（topics.json）

`topics.json` 是一个列表，每个主题一段。从 `topics.example.json` 复制一份开始改；想加主题就复制一段、换个 `id`。

## 抓什么

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | ✅ | 主题编号，只用小写英文、数字和连字符，例如 `tool-watch` |
| `name` | ✅ | 显示在推送标题上的名字 |
| `enabled` | | `false` 时这个主题不运行。示例里默认是 `false`，填好后改成 `true` |
| `purpose` | ✅ | 你为什么关注这类信息、你是谁、要拿它做什么。**这是 AI 判断的主要依据，越具体越好** |
| `queries` | | 建议的搜索词。能联网的 AI 每一条都会搜 |
| `sources_hint` | | 建议去看的网站或网站类型 |
| `watchlist` | | 每次都要打开看的网站：`[{"name": "…", "url": "https://…"}]` |
| `judge` | | 判断标准：什么样的值得推、什么样的不要；high / medium / low 各是什么 |
| `feeds` | | RSS / Atom 源，GitHub 每天抓好交给 AI（见下面） |

**AI 只会推 high 和 medium**；low 的入库但不推送。

### RSS 源（可选）

```json
"feeds": [
  {"name": "某网站", "url": "https://example.org/feed/", "match": ["关键词"], "exclude": ["不要的词"], "limit": 20}
],
"feed_exclude": ["对这个主题所有源都生效的排除词"],
"feed_limit": 30
```

- `match`：标题或摘要里含任一关键词才保留；`exclude`：含任一关键词就去掉（都不分大小写）。
- `limit` / `feed_limit`：每个源最多保留前几条，避免一个宽泛的源把候选淹没。
- 排除词要尽量具体：太宽的词会误伤有用的条目。改完后手动跑一次 fetch 工作流，看 `data/fetched/<主题>.json` 里的结果核对。
- RSS 只是线索。AI 会被要求找到原始页面核实，不会直接照搬。

## 怎么推送

| 字段 | 默认 | 说明 |
|---|---|---|
| `cron` | | 几点运行（**只对方案 B 有效**；方案 A 的时间设在你的 AI 平台上）。标准 5 段式："分 时 日 月 周"。例：`40 8 * * 1` 每周一 8:40；`0 7 * * *` 每天 7:00；`30 19 1,15 * *` 每月 1 日和 15 日 19:30 |
| `timezone` | 环境变量 `INTEL_TZ`，再没有就是 UTC | `cron` 按哪个时区理解，例如 `Asia/Shanghai`、`Europe/Berlin` |
| `layout` | `cards` | `cards`：一条一张卡片，带按钮；`digest`：一天汇总成一条，没有标记按钮 |
| `max_items` | 不限 | 每次最多推几条 |
| `points` | | 每条要讲的要点，见下面 |
| `buttons` | `["⭐ 感兴趣", "✖ 不需要", "✔ 已处理"]` | 卡片下三个按钮的文字。含义固定：第一个＝感兴趣，第二个＝不需要（同类以后不再推），第三个＝已处理 |
| `top_label` | `最值得看` | 推送开头"🏆 最值得看：…"这一行的说法 |

### 要点（points）

```json
"points": [
  {"key": "price", "label": "价格", "hint": "写原文金额和币种"},
  {"key": "where", "label": "地点"},
  {"key": "why_me", "label": "对我有什么用"}
]
```

- `key`：AI 交回结果时用的字段名，只用英文；`label`：推送里显示的名字；`hint`（可选）：告诉 AI 这一项怎么写。
- 推送的样子：

  ```
  💡 一句话亮点
  标题
  价格：免费
  地点：待核实          ← AI 查不到的写「待核实」，不猜
  对我有什么用：……
  两三句概要
  → 打开原文
  ```

## 用哪个 AI

```json
"ai": {"mode": "api", "provider": "anthropic", "model": "claude-opus-5-5", "web_search": true, "max_searches": 8}
```

或 `"ai": {"mode": "agent"}`。各项说明见 [ai.md](ai.md)。

## 可选功能

| 字段 | 说明 |
|---|---|
| `share_to_group` | `true`：这个主题的推送自动抄送一份到团队群（需要 Secret `TELEGRAM_TEAM_GROUP_ID`）。群里不带按钮 |
| `share_button` | `true`：卡片上加「📤 发到群」按钮，点一下把这张卡片发到群（需要部署 Worker，并在 Worker 里设 `TELEGRAM_TEAM_GROUP_ID`） |
| `deep_dive` | `true`：点「感兴趣」的条目，由 AI 写一篇深度内容（目前只支持方案 A，见 `prompts/deep-dive.md`） |
| `deep_prompt` | 深度内容怎么写：结构、长度、要查几个来源。例如"写 1500 字的上手指南，包括价格、限制、三步试用方法" |
| `deep_label` | 深度内容消息的标题前缀，默认「📖 深度阅读」 |

## 进阶：自定义打分或排版

只需要"搜索 → 判断 → 推送"的，配置就够了。需要自己的打分、挑选或排版时，在 `intel/topics/` 里新建 `<主题id，连字符换成下划线>.py`，实现 `prepare` / `select` / `render` 中需要的几个，接口说明在 `intel/topics/__init__.py`。
