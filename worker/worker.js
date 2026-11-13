// telegram-ai-radar · Cloudflare Worker（可选）
//
// 按钮即时响应：Telegram 把按钮点击直接推到 /tg，这里一秒内回复「已记录」、改按钮，
// 并把点击暂存进 KV；GitHub Action 定时到 /marks 取走写进资料库，取完再删。
// 不部署 Worker 也能用：GitHub Action 每 2 小时拉一次点击，只是按钮要等到那时才有反应。
//
// 需要的设置（都在 Cloudflare 后台配）：
//   Secret  TELEGRAM_BOT_TOKEN   bot 的 token
//   Secret  TELEGRAM_CHAT_ID     你的 chat ID
//   变量    TELEGRAM_TEAM_GROUP_ID  团队群的 chat ID（「📤 发到群」按钮用；不设则按钮会提示未设置）
//   KV 绑定  MARKS               任意一个 KV 命名空间
//
// webhook 密钥和取数密钥都由 bot token 推算出来，GitHub Action 用同一个 token 算出同样的值，
// 所以不需要额外设置任何密钥。

const LABEL = { i: "感兴趣", n: "不需要", d: "已处理" };
const enc = new TextEncoder();

async function derive(token, purpose) {
  const key = await crypto.subtle.importKey(
    "raw", enc.encode(token), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", key, enc.encode(purpose));
  return [...new Uint8Array(sig)].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 32);
}

async function tg(env, method, params) {
  const r = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(params),
  });
  return r.json();
}

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json" } });

// ---------- 按钮点击 ----------
// 普通群升级为超级群组时 chat_id 会变（旧群收到 migrate_to_chat_id，新群收到 migrate_from_chat_id）。
// 当场给你（TELEGRAM_CHAT_ID）发一条私信，并存进 KV（migrate: 前缀），GitHub Action 的 sync 取走后在运行页上显示警告。
// 旧群、新群各会收到一条服务消息：用 migrate-notified: 标记（保留 30 天）保证同一次迁移只发一次私信。
async function noteMigration(env, u) {
  const m = u.message;
  if (!m) return;
  let from, to;
  if (m.migrate_to_chat_id) [from, to] = [m.chat?.id, m.migrate_to_chat_id];
  else if (m.migrate_from_chat_id) [from, to] = [m.migrate_from_chat_id, m.chat?.id];
  else return;
  const text = `群已升级为超级群组，chat_id 变了：${from} → ${to}。` +
    `请把 TELEGRAM_TEAM_GROUP_ID 改成 ${to}（本地 .env、GitHub Secrets、Cloudflare 都要改）`;
  console.warn("!!! " + text);
  await env.MARKS.put(`migrate:${from}:${to}`,
    JSON.stringify({ from, to, at: new Date().toISOString(), update_id: u.update_id }));
  const flag = `migrate-notified:${from}:${to}`;
  if (await env.MARKS.get(flag)) return;
  await env.MARKS.put(flag, "1", { expirationTtl: 30 * 24 * 3600 });
  try {
    await tg(env, "sendMessage", { chat_id: env.TELEGRAM_CHAT_ID, text: "⚠️ " + text });
  } catch (e) {
    console.warn("migration notice not sent: " + e); // 私信发不出去也不影响按钮处理，Action 页上还有警告
  }
}

// ---------- 发到群 ----------
// 配置了 share_button 的主题，卡片上有「📤 发到群」按钮：点一下把这张卡片原样发到团队群
// （保留粗体、链接等格式，不带按钮），按钮变成「✅ 已发到群」；同一张卡片只发一次。
// 需要在 Worker 的变量里设 TELEGRAM_TEAM_GROUP_ID（群的 chat_id）。
async function shareToGroup(env, q, owner) {
  const answer = (text, alert = false) =>
    tg(env, "answerCallbackQuery", { callback_query_id: q.id, text, show_alert: alert });
  const group = String(env.TELEGRAM_TEAM_GROUP_ID || "").trim();
  const m = q.message;
  if (!group) return answer("Worker 还没设置 TELEGRAM_TEAM_GROUP_ID，发不到群里", true);
  if (!m?.text) return answer("这条消息不能发到群里", true);
  const flag = `shared:${m.message_id}`;
  if (await env.MARKS.get(flag)) return answer("这张卡片已经发到群里了");
  const r = await tg(env, "sendMessage", {
    chat_id: group, text: m.text, entities: m.entities, link_preview_options: { is_disabled: true } });
  if (!r?.ok) {
    const moved = r?.parameters?.migrate_to_chat_id;
    return answer(moved
      ? `群已升级为超级群组，新的 chat_id 是 ${moved}。请把 TELEGRAM_TEAM_GROUP_ID 改成它（Cloudflare 和 GitHub Secrets）`
      : `发送失败：${r?.description || "未知错误"}`, true);
  }
  await env.MARKS.put(flag, "1", { expirationTtl: 180 * 24 * 3600 });
  await answer("已发到群");
  const kb = m.reply_markup?.inline_keyboard || [];
  const rows = kb.map((row) => row.map((b) =>
    String(b.callback_data).startsWith("g") ? { text: "✅ 已发到群", callback_data: b.callback_data } : b));
  if (rows.length) {
    await tg(env, "editMessageReplyMarkup", {
      chat_id: owner, message_id: m.message_id, reply_markup: { inline_keyboard: rows } });
  }
}

async function handleUpdate(env, u) {
  await noteMigration(env, u);
  const q = u.callback_query;
  if (!q) return; // 其他普通消息不处理
  const owner = String(env.TELEGRAM_CHAT_ID).trim();
  const chat = String(q.message?.chat?.id ?? q.from?.id ?? "");
  if (chat !== owner) {
    await tg(env, "answerCallbackQuery", { callback_query_id: q.id });
    return;
  }
  const [kind, id, code] = String(q.data || "").split(":");
  if (kind === "g") return shareToGroup(env, q, owner);
  if (kind !== "m" || !id || !LABEL[code]) {
    await env.MARKS.put(`bad:${Date.now()}:${u.update_id}`, JSON.stringify({ data: q.data }));
    await tg(env, "answerCallbackQuery", { callback_query_id: q.id, text: "无法识别这个按钮" });
    return;
  }
  // 先存，再回复：存储成功才告诉你「已记录」
  await env.MARKS.put(
    `mark:${Date.now()}:${u.update_id}`,
    JSON.stringify({ id, code, at: new Date().toISOString(), update_id: u.update_id }));
  // 按钮文字以消息上的原文为准：选中的打 ✅，其余保留，可以随时改选
  const kb = q.message?.reply_markup?.inline_keyboard || [];
  let label = LABEL[code];
  const rows = kb.map((row) => row.map((b) => {
    if (String(b.callback_data).startsWith("g")) return b; // 「发到群」按钮原样保留
    const clean = String(b.text).replace(/^✅\s*/, "");
    const chosen = b.callback_data === q.data;
    if (chosen) label = clean.replace(/^[^\p{L}\p{N}]+/u, "").trim() || label;
    return { text: (chosen ? "✅ " : "") + clean, callback_data: b.callback_data };
  }));
  await tg(env, "answerCallbackQuery", { callback_query_id: q.id, text: `已记录：${label}` });
  if (q.message?.message_id && rows.length) {
    await tg(env, "editMessageReplyMarkup", {
      chat_id: owner,
      message_id: q.message.message_id,
      reply_markup: { inline_keyboard: rows },
    });
  }
}

// ---------- 入口 ----------
export default {
  async fetch(req, env) {
    const url = new URL(req.url);

    if (url.pathname === "/tg" && req.method === "POST") {
      const expect = await derive(env.TELEGRAM_BOT_TOKEN, "intel-webhook");
      if (req.headers.get("X-Telegram-Bot-Api-Secret-Token") !== expect) {
        return new Response("forbidden", { status: 403 });
      }
      await handleUpdate(env, await req.json());
      return new Response("ok");
    }

    if (url.pathname.startsWith("/marks")) {
      const expect = await derive(env.TELEGRAM_BOT_TOKEN, "intel-pull");
      if (req.headers.get("Authorization") !== `Bearer ${expect}`) {
        return new Response("forbidden", { status: 403 });
      }
      if (req.method === "GET") {
        const out = [];
        for (const prefix of ["mark:", "bad:", "migrate:"]) {
          let cursor;
          do {
            const page = await env.MARKS.list({ prefix, cursor });
            for (const k of page.keys) {
              const v = await env.MARKS.get(k.name);
              if (v) out.push({ key: k.name, ...JSON.parse(v) });
            }
            cursor = page.list_complete ? undefined : page.cursor;
          } while (cursor);
        }
        out.sort((a, b) => (a.key < b.key ? -1 : 1));
        return json(out);
      }
      if (req.method === "POST" && url.pathname === "/marks/ack") {
        const { keys = [] } = await req.json();
        for (const k of keys) {
          if (k.startsWith("mark:") || k.startsWith("bad:") || k.startsWith("migrate:")) await env.MARKS.delete(k);
        }
        return json({ deleted: keys.length });
      }
    }

    return new Response("telegram-ai-radar worker ok");
  },
};
