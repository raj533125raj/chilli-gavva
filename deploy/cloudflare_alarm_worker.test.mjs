// Offline tests for cloudflare_alarm_worker.js. Run: node --test deploy/cloudflare_alarm_worker.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { tick, handle, alarmAllowed, settings, telegramTokenOk } from "./cloudflare_alarm_worker.js";

const MIN = 60 * 1000;
const T0 = Date.UTC(2026, 9, 15, 12, 0); // 15 Oct 2026, about half the month gone

function kv() {
  const data = new Map();
  const log = { puts: 0, deletes: 0 };
  return {
    data, log,
    async get(k) { return data.has(k) ? data.get(k) : null; },
    async put(k, v) { log.puts++; data.set(k, v); },
    async delete(k) { log.deletes++; data.delete(k); },
  };
}

function env(extra = {}) {
  return { STATE: kv(), GITHUB_TOKEN: "gh-secret", WORKER_SECRET: "s3cret", ...extra };
}

function fakeFetch(updates = []) {
  const calls = [];
  const fn = async (url, init = {}) => {
    calls.push({ url: String(url), init });
    if (String(url).includes("api.telegram.org")) {
      return new Response(JSON.stringify({ ok: true, result: updates }), { status: 200 });
    }
    return new Response(null, { status: 204 });
  };
  fn.calls = calls;
  fn.dispatches = () => calls.filter((c) => c.url.includes("/dispatches")).map((c) => JSON.parse(c.init.body).inputs.reason);
  return fn;
}

const msg = (id, chat) => ({ update_id: id, message: { chat: { id: chat }, text: "/list" } });

test("alarm starts a run, then waits 30 minutes", async () => {
  const e = env(), f = fakeFetch();
  assert.equal((await tick(e, T0, f)).action, "alarm");
  assert.equal((await tick(e, T0 + 10 * MIN, f)).action, "idle");
  assert.equal((await tick(e, T0 + 30 * MIN, f)).action, "alarm");
  assert.deepEqual(f.dispatches(), ["alarm", "alarm"]);
  const call = f.calls.find((c) => c.url.includes("/dispatches"));
  assert.match(call.url, /repos\/raj533125raj\/chilli-gavva\/actions\/workflows\/agent.yml\/dispatches$/);
  assert.equal(call.init.headers.Authorization, "Bearer gh-secret");
  assert.equal(JSON.parse(call.init.body).ref, "main");
});

test("idle ticks never write to KV", async () => {
  const e = env(), f = fakeFetch();
  await tick(e, T0, f);
  const puts = e.STATE.log.puts;
  for (let i = 1; i < 20; i++) await tick(e, T0 + i * MIN, f);
  assert.equal(e.STATE.log.puts, puts);
});

test("nothing starts while the Mac worker is alive; cloud takes over when it goes quiet", async () => {
  const e = env({ TELEGRAM_BOT_TOKEN: "tg", TELEGRAM_CHAT_ID: "42" }), f = fakeFetch([msg(5, 42)]);
  await e.STATE.put("mac", String(T0 - 4 * MIN));
  assert.equal((await tick(e, T0, f)).action, "mac-alive");
  assert.equal(f.calls.length, 0); // not even a Telegram peek
  assert.equal((await tick(e, T0 + 9 * MIN, f)).action, "doorbell"); // heartbeat now 13 min old
});

test("door bell: owner message starts a run once; others are ignored; nothing is consumed", async () => {
  const e = env({ TELEGRAM_BOT_TOKEN: "tg", TELEGRAM_CHAT_ID: "42" });
  await e.STATE.put("clock", JSON.stringify({ lastDispatch: T0 - 5 * MIN }));
  const stranger = fakeFetch([msg(7, 99)]);
  assert.equal((await tick(e, T0, stranger)).action, "idle");
  const owner = fakeFetch([msg(7, 99), msg(8, 42)]);
  assert.equal((await tick(e, T0 + MIN, owner)).action, "doorbell");
  assert.equal((await tick(e, T0 + 2 * MIN, owner)).action, "idle"); // same message, no second run
  const peek = owner.calls.find((c) => c.url.includes("getUpdates")).url;
  assert.ok(!peek.includes("offset"), "peek must not confirm updates");
  assert.deepEqual(owner.dispatches(), ["doorbell"]);
});

test("door bell resets the alarm timer", async () => {
  const e = env({ TELEGRAM_BOT_TOKEN: "tg", TELEGRAM_CHAT_ID: "42" });
  await e.STATE.put("clock", JSON.stringify({ lastDispatch: T0 - 29 * MIN }));
  await tick(e, T0, fakeFetch([msg(1, 42)]));
  assert.equal((await tick(e, T0 + 2 * MIN, fakeFetch())).action, "idle");
});

test("budget pacing and hard cap", async () => {
  const s = settings({});
  assert.equal(alarmAllowed(800, T0, s), true);    // ~half the budget by mid-month is fine
  assert.equal(alarmAllowed(1100, T0, s), false);  // ahead of pace -> wait
  assert.equal(alarmAllowed(1700, Date.UTC(2026, 9, 31, 23), s), false);
  const e = env({ TELEGRAM_BOT_TOKEN: "tg", TELEGRAM_CHAT_ID: "42" });
  await e.STATE.put("runs", JSON.stringify({ month: "2026-10", minutes: 1950 }));
  const f = fakeFetch([msg(3, 42)]);
  assert.equal((await tick(e, T0, f)).action, "budget-wait");
  assert.deepEqual(f.dispatches(), []);
});

test("a run in progress blocks the alarm", async () => {
  const e = env();
  await e.STATE.put("runs", JSON.stringify({ runStarted: T0 - 2 * MIN, runEnded: T0 - 40 * MIN }));
  assert.equal((await tick(e, T0, fakeFetch())).action, "run-in-progress");
  assert.equal((await tick(e, T0 + 20 * MIN, fakeFetch())).action, "alarm"); // stale after 15 min
});

const req = (path, { method = "GET", body, secret = "s3cret" } = {}) =>
  new Request("https://clock.example" + path, {
    method, body: body ? JSON.stringify(body) : undefined,
    headers: secret ? { Authorization: `Bearer ${secret}` } : {},
  });

test("endpoints need the secret", async () => {
  const e = env();
  assert.equal((await handle(req("/status", { secret: "" }), e, T0)).status, 401);
  assert.equal((await handle(req("/status", { secret: "wrong!" }), e, T0)).status, 401);
  assert.equal((await handle(req("/status"), e, T0)).status, 200);
  assert.equal((await handle(req("/status"), { ...e, WORKER_SECRET: "" }, T0)).status, 401);
  assert.equal((await handle(req("/", { secret: "" }), e, T0)).status, 200);
});

test("heartbeat, run start/end and status", async () => {
  const e = env();
  await handle(req("/heartbeat", { method: "POST", body: {} }), e, T0);
  let r = await (await handle(req("/run", { method: "POST", body: { phase: "start" } }), e, T0)).json();
  assert.equal(r.mac_alive, true);
  await handle(req("/heartbeat", { method: "POST", body: { stopping: true } }), e, T0);
  r = await (await handle(req("/run", { method: "POST", body: { phase: "start" } }), e, T0)).json();
  assert.equal(r.mac_alive, false);
  let st = await (await handle(req("/status"), e, T0 + MIN)).json();
  assert.equal(st.run_in_progress, true);
  await handle(req("/run", { method: "POST", body: { phase: "end", minutes: 2 } }), e, T0 + 2 * MIN);
  await handle(req("/run", { method: "POST", body: { phase: "end", minutes: 999 } }), e, T0 + 3 * MIN);
  st = await (await handle(req("/status"), e, T0 + 3 * MIN)).json();
  assert.equal(st.run_in_progress, false);
  assert.equal(st.month_minutes, 32); // 2 + capped 30
  assert.equal(st.month_runs, 2);
  const text = JSON.stringify(st);
  assert.ok(!text.includes("gh-secret") && !text.includes("s3cret"));
  // a new month starts from zero
  await handle(req("/run", { method: "POST", body: { phase: "end", minutes: 1 } }), e, Date.UTC(2026, 10, 1, 1));
  st = await (await handle(req("/status"), e, Date.UTC(2026, 10, 1, 2))).json();
  assert.equal(st.month_minutes, 1);
});

const press = (id, chat) => ({ update_id: id, callback_query: { id: "q", data: "v:4", from: { id: 5 },
  message: { chat: { id: chat } } } });

test("door bell also rings for a button press from the owner chat, and asks for button presses", async () => {
  const e = env({ TELEGRAM_BOT_TOKEN: "tg", TELEGRAM_CHAT_ID: "42" });
  await e.STATE.put("clock", JSON.stringify({ lastDispatch: T0 - 5 * MIN }));
  const other = fakeFetch([press(9, 99)]);
  assert.equal((await tick(e, T0, other)).action, "idle");
  const owner = fakeFetch([press(10, 42)]);
  assert.equal((await tick(e, T0 + MIN, owner)).action, "doorbell");
  const peek = decodeURIComponent(owner.calls.find((c) => c.url.includes("getUpdates")).url);
  assert.ok(peek.includes('allowed_updates=["message","callback_query"]'));
  assert.ok(!peek.includes("offset"));
});

test("status says whether the Telegram token works, never shows it", async () => {
  const fetchOk = async (url) => new Response(JSON.stringify({ ok: String(url).includes("/botGOOD/") }),
    { status: String(url).includes("/botGOOD/") ? 200 : 401 });
  assert.equal(await telegramTokenOk(env(), fetchOk), "not set");
  assert.equal(await telegramTokenOk(env({ TELEGRAM_BOT_TOKEN: "OLD", TELEGRAM_CHAT_ID: "42" }), fetchOk), "no");
  const good = env({ TELEGRAM_BOT_TOKEN: "GOOD", TELEGRAM_CHAT_ID: "42" });
  assert.equal(await telegramTokenOk(good, fetchOk), "yes");
  assert.equal(await telegramTokenOk(good, async () => { throw new Error("net"); }), "no");
  const st = await (await handle(req("/status"), good, T0, fetchOk)).json();
  assert.equal(st.telegram_token_ok, "yes");
  assert.equal(st.door_bell, true);
  const text = JSON.stringify(st);
  assert.ok(!text.includes("GOOD") && !text.includes("42"));
});
