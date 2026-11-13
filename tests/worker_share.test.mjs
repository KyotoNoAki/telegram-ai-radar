// 「📤 发到群」按钮测试。运行：node tests/worker_share.test.mjs
import assert from "node:assert/strict";
import { GROUP, OWNER, setup } from "./worker_helpers.mjs";

const SHARE = { text: "📤 发到群", callback_data: "g" };
const MARKS = [{ text: "⭐ 感兴趣", callback_data: "m:abc:i" }, { text: "✖ 不需要", callback_data: "m:abc:n" }];

function click(data, keyboard, from = OWNER, update_id = 1) {
  return {
    update_id,
    callback_query: {
      id: `cb${update_id}`, data, from: { id: Number(from) },
      message: {
        message_id: 77, chat: { id: Number(from) },
        text: "💡 示例展览开幕\n→ 打开原文",
        entities: [{ type: "bold", offset: 3, length: 6 }, { type: "text_link", offset: 11, length: 6, url: "https://x.example.org" }],
        reply_markup: { inline_keyboard: keyboard },
      },
    },
  };
}
const answers = (t) => t.sent("answerCallbackQuery").map((a) => a.text);

// 1. 点「发到群」：原样（含格式、不带按钮）发到群，按钮变成「✅ 已发到群」，标记按钮不动
{
  const t = setup({ env: { TELEGRAM_TEAM_GROUP_ID: GROUP } });
  assert.equal((await t.post(click("g", [MARKS, [SHARE]]))).status, 200);
  const [msg] = t.sent("sendMessage");
  assert.equal(msg.chat_id, GROUP);
  assert.equal(msg.text, "💡 示例展览开幕\n→ 打开原文");
  assert.equal(msg.entities.length, 2, "保留粗体和链接");
  assert.equal(msg.reply_markup, undefined, "群里不带按钮");
  assert.deepEqual(answers(t), ["已发到群"]);
  const [edit] = t.sent("editMessageReplyMarkup");
  assert.deepEqual(edit.reply_markup.inline_keyboard, [MARKS, [{ text: "✅ 已发到群", callback_data: "g" }]]);
  assert.deepEqual(await t.marks(), [], "发到群不算按钮标记");

  // 再点一次：不重复发
  await t.post(click("g", edit.reply_markup.inline_keyboard, OWNER, 2));
  assert.equal(t.sent("sendMessage").length, 1, "同一张卡片只发一次");
  assert.equal(answers(t)[1], "这张卡片已经发到群里了");

  // 之后点标记按钮：「✅ 已发到群」保持原样，不被重置
  await t.post(click("m:abc:i", edit.reply_markup.inline_keyboard, OWNER, 3));
  const kb = t.sent("editMessageReplyMarkup").at(-1).reply_markup.inline_keyboard;
  assert.equal(kb[0][0].text, "✅ ⭐ 感兴趣");
  assert.deepEqual(kb[1][0], { text: "✅ 已发到群", callback_data: "g" });
}

// 2. Worker 没设群 ID：提示，不发
{
  const t = setup();
  await t.post(click("g", [[SHARE]]));
  assert.equal(t.sent("sendMessage").length, 0);
  assert.match(answers(t)[0], /还没设置 TELEGRAM_TEAM_GROUP_ID/);
}

// 3. 群已升级：弹窗写明新的 chat_id，不标记为已发，修好后还能再点
{
  const t = setup({
    env: { TELEGRAM_TEAM_GROUP_ID: GROUP },
    reply: (m) => (m === "sendMessage"
      ? { ok: false, description: "Bad Request: group chat was upgraded to a supergroup chat",
          parameters: { migrate_to_chat_id: -1009876543210 } } : null),
  });
  await t.post(click("g", [[SHARE]]));
  assert.match(answers(t)[0], /新的 chat_id 是 -1009876543210/);
  assert.equal(t.sent("editMessageReplyMarkup").length, 0, "失败时按钮不变");
  assert.equal(t.env.MARKS.m.size, 0, "失败时不记为已发");
}

// 4. 不是本人点的：不发
{
  const t = setup({ env: { TELEGRAM_TEAM_GROUP_ID: GROUP } });
  await t.post(click("g", [[SHARE]], "7"));
  assert.equal(t.sent("sendMessage").length, 0);
}

console.log("worker share tests: OK");
