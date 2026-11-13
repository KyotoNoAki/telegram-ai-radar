# telegram-ai-radar

> 在配置里写好"我关心什么"，它定时抓取、去重、筛选，把值得看的推到你的 Telegram；你点按钮标记，它记住你的偏好。
> 不需要服务器，也不需要数据库，跑在 GitHub 的免费额度里。

*A personal intelligence radar: describe what you care about in a config file; an AI searches, verifies and filters on a schedule, and the worthwhile items arrive in your Telegram as cards with buttons. Runs on GitHub Actions — no server, no database. Docs are in Chinese.*

**这是一个模式，不是模板。** 仓库里没有任何预先写好的主题：抓什么、什么时候推、每条讲哪些要点、用哪个 AI，全部由你在 `topics.json` 里决定。

## 它做什么

```
你的主题配置 ──▶ AI 搜索、打开原页面核实、判断 ──▶ 统一格式的结果 ──▶ 去重、入库
                     ▲                                                 │
              （可选）RSS 候选                                          ▼
                                     你点按钮标记 ◀── Telegram 卡片 ◀── 生成推送
```

- **你定义要点**：每条推送第一行是一句话亮点，接着是你在配置里列出的要点（例如"价格""地点""对我的影响"），最后是原文链接。AI 查不到的要点写「待核实」，不猜。
- **两种版式**：`cards` 一条一张卡片；`digest` 一天汇总成一条（太长自动分条）。
- **去重**：同一网址只入库一次；同一件事换了网址或换了年份也认得出来；点过「不需要」的同类不再推。
- **按钮**：每张卡片下面的按钮文字由你定（默认：感兴趣 / 不需要 / 已处理），点了记进资料库。部署可选的 Cloudflare Worker 后，按钮一秒内有反应。
- **发送不重复**：发送中断时，下次只补发没发出去的。
- **团队群（可选）**：指定的主题自动抄送到群；其他主题的卡片上可以加「📤 发到群」按钮。
- **深度内容（可选）**：对某条点「感兴趣」后，让 AI 为它写一篇长文。
- **RSS（可选）**：GitHub 每天抓好 RSS 候选交给 AI 判断；单个源失败只记警告，不中断。
- **导出**：全部条目导出成 CSV。

## 和 AI 配合的两种方式

两种方式让 AI 交回**同一种格式**的结果，后面的去重、入库、推送完全相同。每个主题可以单独选。

| | 方案 A：AI 代理平台 | 方案 B：GitHub 直接调用 AI 接口 |
|---|---|---|
| 配置 | `"ai": {"mode": "agent"}` | `"ai": {"mode": "api", "provider": "…"}` |
| 谁来跑 | 你自己的 AI 代理平台（能上网、能运行命令、能推送 git 的，例如 Claude Code 的定时任务） | GitHub Actions 每小时检查一次，到点的主题自动运行 |
| 时间表 | 设在 AI 平台上 | 写在主题配置的 `cron` 里 |
| 费用 | 按你的 AI 平台订阅 | 按 AI 接口用量付费（搜索越多越贵） |
| 支持 | 任何能照着说明做事的 AI 代理 | Anthropic（Claude）、OpenAI、Google Gemini、OpenAI 兼容接口 |

详见 [docs/ai.md](docs/ai.md)。

## 快速开始

> ⚠️ **请在你自己的私有仓库里运行。** 这个系统会把资料库（你的标记、推送记录）自动提交回仓库。
> 工作流在公开仓库里默认不运行（会显示"已跳过"）。

1. 点本页右上角 **Use this template → Create a new repository**，选 **Private**（或者把文件复制到你自己的私有仓库）。
2. 把 `topics.example.json` 复制成 `topics.json`，按 [docs/topics.md](docs/topics.md) 填写你的主题，填好后把 `enabled` 改成 `true`。
3. 按 [docs/setup.md](docs/setup.md)：建 Telegram bot、查 chat ID、在仓库的 Secrets 里填 token；用方案 B 的再填 AI 的 API key。
4. （可选）部署 Cloudflare Worker，让按钮即时响应。
5. 方案 A：在你的 AI 代理平台上建定时任务，任务内容是"按 `prompts/run.md` 运行主题 `<主题 id>`"。方案 B：什么都不用做，到点自动运行。

本地试运行：复制 `.env.example` 为 `.env`，保持 `INTEL_DRY_RUN=1`（只打印、不发送）。

## 结构

| 部分 | 作用 |
|---|---|
| `intel/` | Python 程序，只用标准库，没有第三方依赖 |
| `intel/ai/` | 生成工作说明、解析结果；方案 B 的各家接口适配 |
| `topics.example.json` | 空白配置骨架，复制成 `topics.json` 后填写 |
| `prompts/` | 方案 A 给 AI 代理的通用说明（`run.md`、`deep-dive.md`） |
| `worker/worker.js` | （可选）Cloudflare Worker：按钮即时响应、发到群、群升级提醒 |
| `.github/workflows/` | `run`（方案 B）、`deliver`（发送和同步按钮）、`fetch`（RSS）、`test`、`forward-test` |
| `tests/` | 用虚构样例跑通整条流程的测试，不联网、不调用 AI、不发消息 |

## 测试

```
python -m unittest discover -s tests -t .
node tests/worker_migration.test.mjs
node tests/worker_share.test.mjs
```

## 安全

- 密钥只放在 GitHub Secrets、Cloudflare 和本地 `.env` 里；`.env` 在 `.gitignore` 里，不会被提交。
- 采集到的网页、RSS 内容只当数据，不当指令：工作说明里明确要求 AI 这样做。
- Worker 的接口都有密钥保护（由 bot token 推算），不需要另外设置。

## 许可证

- 代码和说明文档：[MIT 许可证](LICENSE)。
- 本仓库不包含、也不授权作者的任何文学或影像作品，这些作品保留所有权利（见 [LICENSE](LICENSE) 末尾）。
