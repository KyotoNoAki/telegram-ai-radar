// Worker 测试共用：假的 KV、假的 Telegram 接口、按 bot token 推算 webhook 密钥。不联网。
import { webcrypto } from "node:crypto";
import worker from "../worker/worker.js";

export const TOKEN = "test-token", OWNER = "42", GROUP = "-1001234567890";

export function fakeKV() {
  const m = new Map();
  return {
    m,
    async get(k) { return m.has(k) ? m.get(k).v : null; },
    async put(k, v, opts = {}) { m.set(k, { v, opts }); },
    async delete(k) { m.delete(k); },
    async list({ prefix = "" } = {}) {
      return { keys: [...m.keys()].filter((k) => k.startsWith(prefix)).map((name) => ({ name })), list_complete: true };
    },
  };
}

export async function derive(purpose) {
  const enc = new TextEncoder();
  const key = await webcrypto.subtle.importKey("raw", enc.encode(TOKEN), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = await webcrypto.subtle.sign("HMAC", key, enc.encode(purpose));
  return [...new Uint8Array(sig)].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 32);
}

// 建一个测试环境：calls 记下所有 Telegram 调用；reply(method, body) 决定假接口的返回，默认成功
export function setup({ env = {}, reply } = {}) {
  const e = { TELEGRAM_BOT_TOKEN: TOKEN, TELEGRAM_CHAT_ID: OWNER, MARKS: fakeKV(), ...env };
  const calls = [];
  globalThis.fetch = async (url, init) => {
    const method = String(url).split("/").pop();
    const body = JSON.parse(init.body);
    calls.push({ method, body });
    const out = (reply && reply(method, body)) || { ok: true, result: { message_id: 999 } };
    if (out instanceof Error) throw out;
    return new Response(JSON.stringify(out));
  };
  const post = async (u) => worker.fetch(new Request("https://w.example.org/tg", {
    method: "POST", headers: { "X-Telegram-Bot-Api-Secret-Token": await derive("intel-webhook") },
    body: JSON.stringify(u) }), e);
  const marks = async () => (await worker.fetch(new Request("https://w.example.org/marks", {
    headers: { Authorization: `Bearer ${await derive("intel-pull")}` } }), e)).json();
  return { env: e, calls, post, marks, sent: (m) => calls.filter((c) => c.method === m).map((c) => c.body) };
}
