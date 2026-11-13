# 一步步配置

## 1. 建私有仓库

在本仓库页面点 **Use this template → Create a new repository**，选 **Private**。
（工作流会把资料库提交回仓库；在公开仓库里它们默认不运行。）

## 2. 建 Telegram bot

1. 在 Telegram 里找 **@BotFather**，发 `/newbot`，按提示起名字，拿到一串 token（形如 `123456:ABC-…`）。
2. 找到你的 bot，给它发一条任意消息。
3. 在浏览器打开 `https://api.telegram.org/bot<你的token>/getUpdates`，在返回内容里找 `"chat":{"id":…}`，这个数字就是你的 **chat ID**。

> token 相当于 bot 的密码：不要发给别人、不要写进仓库里的任何文件。

## 3. 填 GitHub Secrets

私有仓库 → **Settings → Secrets and variables → Actions**：

**Secrets**（加密保存）：

| 名称 | 必填 | 内容 |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ | 第 2 步的 token |
| `TELEGRAM_CHAT_ID` | ✅ | 你的 chat ID。只接受这个 chat 的按钮点击 |
| `TELEGRAM_TEAM_GROUP_ID` | | 团队群的 chat ID（用 `share_to_group` 时） |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY` / `OPENAI_COMPATIBLE_API_KEY` | | 方案 B 用到哪家填哪家 |

**Variables**（普通变量）：

| 名称 | 内容 |
|---|---|
| `INTEL_TZ` | 你的时区，例如 `Asia/Shanghai`、`Europe/London`。推送标题上的日期按它算；不填就是 UTC |

### 团队群的 chat ID

把 bot 拉进群，在群里发一条消息，再打开上面的 `getUpdates` 网址，找群的 `"chat":{"id":-100…}`（群的 ID 是负数）。
普通群升级为超级群组时 ID 会变；这时系统会私信提醒你新的 ID。

## 4. 写主题配置

把 `topics.example.json` 复制成 `topics.json`，按 [topics.md](topics.md) 填写，填好后把 `enabled` 改成 `true`，提交。

## 5. 试一次

- 方案 B：仓库 → **Actions → run → Run workflow**。如果还没到主题的 `cron` 时间，它会说"到点的主题：无"。想马上试，可以临时把 `cron` 改成几分钟前的时间（例如现在是 10:20，就写 `15 10 * * *`）。
- 方案 A：让你的 AI 代理按 `prompts/run.md` 运行一次。

收到推送后，点卡片下的按钮。没部署 Worker 时，`deliver` 工作流每 2 小时同步一次点击，按钮在那时变成「已标记」。

## 6. （可选）部署 Cloudflare Worker：按钮即时响应

1. 登录 Cloudflare → **Workers & Pages → Create → Worker**，把 `worker/worker.js` 的内容粘贴进去，部署。
2. **Storage → KV** 新建一个命名空间（名字随意），回到 Worker → **Settings → Bindings** 加一个 KV 绑定，变量名填 `MARKS`。
3. Worker → **Settings → Variables and Secrets**：
   - Secret `TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID`（和 GitHub 里的一样）；
   - （用「📤 发到群」时）变量 `TELEGRAM_TEAM_GROUP_ID`。
4. 在仓库根目录新建 `worker.json`，写上 Worker 的网址：

   ```json
   {"url": "https://你的-worker.你的子域.workers.dev"}
   ```

5. 提交后，下一次 `deliver` 运行会自动把 Telegram 的按钮回调指到 Worker（不用手动设置 webhook）。

检查：浏览器打开 Worker 网址应显示 `telegram-ai-radar worker ok`。

## 7. （可选）本地运行

```
cp .env.example .env      # 填上 token；保持 INTEL_DRY_RUN=1 时只打印、不发送
python -m intel prompt <主题 id>
```

只需要 Python 3.10 以上，没有第三方依赖。
