// Worker 的群迁移提示测试：模拟两条服务消息打到 /tg 上。运行：node tests/worker_migration.test.mjs
import assert from "node:assert/strict";
import { OWNER, setup } from "./worker_helpers.mjs";

const OLD = -123456789, NEW = -1001234567890;

async function run(failSend = false) {
  const t = setup({ reply: (m) => (failSend && m === "sendMessage" ? new Error("network down") : null) });
  const res = [];
  res.push(await t.post({ update_id: 1, message: { chat: { id: OLD }, migrate_to_chat_id: NEW } }));
  res.push(await t.post({ update_id: 2, message: { chat: { id: NEW }, migrate_from_chat_id: OLD } }));
  res.push(await t.post({ update_id: 3, message: { chat: { id: 1 }, text: "/start" } }));
  return { ...t, res, rows: await t.marks() };
}

const warn = console.warn; console.warn = () => {};
try {
  // 1. 两条服务消息 → 私信只发一次，发给本人，内容写清新旧 chat_id
  const a = await run();
  assert.ok(a.res.every((r) => r.status === 200), "webhook 都应返回 200");
  const sent = a.sent("sendMessage");
  assert.equal(sent.length, 1, "同一次迁移只发一次私信");
  assert.equal(sent[0].chat_id, OWNER);
  assert.match(sent[0].text, new RegExp(`${OLD} → ${NEW}`));
  // 2. 迁移记录交给 sync，私信标记不交出去，标记 30 天后过期
  assert.deepEqual(a.rows.map((r) => r.key), [`migrate:${OLD}:${NEW}`]);
  assert.equal(a.rows[0].from, OLD); assert.equal(a.rows[0].to, NEW);
  assert.equal(a.env.MARKS.m.get(`migrate-notified:${OLD}:${NEW}`).opts.expirationTtl, 30 * 24 * 3600);
  // 3. 私信发送失败也不影响 webhook（Telegram 不会因 500 反复重发）
  const b = await run(true);
  assert.ok(b.res.every((r) => r.status === 200), "发私信失败时 webhook 仍应返回 200");
  assert.equal(b.rows.length, 1, "发送失败时迁移记录照样存下");
  console.log("worker migration tests: OK");
} finally {
  console.warn = warn;
}
