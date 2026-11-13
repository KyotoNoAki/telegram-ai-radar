# 和 AI 配合

两种方式共用同一份**工作说明**（由主题配置自动生成）和同一种**结果格式**。想看某个主题的工作说明长什么样：

```
python -m intel prompt <主题 id>
```

## 方案 A：AI 代理平台（`"mode": "agent"`）

适合已经在用 AI 代理平台（能上网、能运行命令、能推送 git）的人，例如 Claude Code 的定时任务，或其他同类工具。

1. 主题配置写 `"ai": {"mode": "agent"}`。
2. 在你的 AI 平台上建一个定时任务（时间表设在平台上），任务内容：

   ```
   克隆 <你的私有仓库>，按 prompts/run.md 运行主题 <主题 id>。
   ```

3. AI 会运行 `python -m intel prompt <主题>` 拿到工作说明，照着搜索、核实、判断，把结果存成 JSON，运行 `python -m intel ingest <主题> <结果文件>`，再推送到仓库。推送后 GitHub 的 `deliver` 工作流把消息发到 Telegram。

深度内容（`deep_dive`）也用这种方式：再建一个每天运行的任务，内容是"按 `prompts/deep-dive.md` 执行"。

有些平台的云端环境连不上外部网站：配置了 `feeds` 的主题由 GitHub 的 `fetch` 工作流每天先抓好 RSS，AI 直接读这份快照。

## 方案 B：GitHub 直接调用 AI 接口（`"mode": "api"`）

不需要 AI 代理平台。GitHub 的 `run` 工作流每小时检查一次，到了主题 `cron` 的时间就调用 AI 接口、入库、发送。GitHub 的定时常延迟几十分钟；晚了也会补上那一次，同一个时间点只运行一次（记录在 `data/runs.json`）。第一次启用主题时，只补最近 3 小时内错过的时间点。

```json
"ai": {
  "mode": "api",
  "provider": "anthropic",
  "model": "claude-opus-5-5",
  "web_search": true,
  "max_searches": 8
}
```

| 字段 | 说明 |
|---|---|
| `provider` | `anthropic` / `openai` / `gemini` / `openai_compatible` |
| `model` | 型号。各家的型号名会更新，请以各家文档为准。Anthropic 不写时默认 `claude-opus-5-5`；其他几家必须写 |
| `web_search` | 是否让 AI 自己联网搜索（默认 `true`）。关掉后只判断 RSS 候选 |
| `max_searches` | 每次最多搜索、打开网页各几次（Anthropic 有效，默认 8）。**搜索次数直接影响费用** |
| `effort` | （仅 Anthropic）思考深度：`low` / `medium` / `high`，默认 `medium` |
| `base_url` | （仅 `openai_compatible`）接口地址，例如 `https://api.example.com/v1` |
| `api_key_env` | （可选）API key 放在哪个环境变量里，默认见下表 |

API key 放在仓库的 **Settings → Secrets and variables → Actions → Secrets**，**不要写进配置文件**：

| provider | Secret 名称 | 能自己联网搜索 |
|---|---|---|
| `anthropic` | `ANTHROPIC_API_KEY` | ✅（接口自带网页搜索和打开网页） |
| `openai` | `OPENAI_API_KEY` | ✅（Responses 接口自带网页搜索） |
| `gemini` | `GEMINI_API_KEY` | ✅（自带 Google 搜索） |
| `openai_compatible` | `OPENAI_COMPATIBLE_API_KEY` | ❌ 只判断 RSS 候选，主题要配置 `feeds` |

调用分两步：先让 AI 按工作说明搜索、判断，交回带 JSON 的回答；如果回答里解析不出 JSON，再用各家的结构化输出把它整理成结果格式。

**费用**：方案 B 按用量付费，搜索和打开网页越多、型号越大越贵。建议先用一个主题、较小的 `max_searches` 跑一周，看各家后台的用量再调整。一个主题运行失败（例如 key 无效）只在运行页上显示警告，不会在下一个小时重复付费重试。

> 各家接口的适配用模拟回复做了测试，但各家接口会更新。第一次使用某一家时，请先手动运行一次 `run` 工作流（Actions → run → Run workflow），确认能正常收到推送。

## 结果格式

两种方式最后都要交回这样的 JSON（`points` 里是你在配置里列的要点；汇总版式另外要 `lead`）：

```json
{
  "items": [
    {
      "title": "标题",
      "url": "https://原始页面",
      "source": "来源名",
      "highlight": "一句话亮点",
      "fit": "high",
      "summary": "两三句概要",
      "points": {"point1": "…", "point2": ""}
    }
  ]
}
```
