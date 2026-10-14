// Chilli Gavva alarm clock + door bell — a Cloudflare Worker on the FREE plan.
// Setup: docs/ALWAYS_ON.md. Paste this whole file into the Worker editor.
//
// Every minute (Cron Trigger "* * * * *"):
//   1. If the Mac worker sent a heartbeat in the last 12 minutes -> do nothing (one writer at a time).
//   2. Door bell: peek at Telegram (getUpdates WITHOUT offset, so nothing is consumed). A new message
//      from the owner chat starts the GitHub workflow at once.
//   3. Alarm: if no run was started for 30 minutes, start the GitHub workflow.
//      A monthly minutes budget (default 1,700) paces the alarm so GitHub's free 2,000 minutes last.
//
// Settings (Worker -> Settings -> Variables and Secrets):
//   Secrets : GITHUB_TOKEN (fine-grained, this repo only, Actions read/write), WORKER_SECRET,
//             TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID (optional, turns on the door bell)
//   Binding : KV namespace bound as STATE
// Nothing here posts, publishes or sells anything. Tokens are only sent in request headers / the
// Telegram URL and are never logged or returned.

const REPO = "raj533125raj/chilli-gavva";
const WORKFLOW = "agent.yml";
const MINUTE = 60 * 1000;

export function settings(env) {
  return {
    repo: env.GITHUB_REPO || REPO,
    alarmMinutes: Math.max(30, Number(env.ALARM_MINUTES) || 30), // 30 minutes is the fastest allowed
    macStaleMinutes: Number(env.MAC_STALE_MINUTES) || 12,
    budget: Number(env.MONTHLY_MINUTES_BUDGET) || 1700,
    hardCap: Number(env.MONTHLY_MINUTES_HARD_CAP) || 1900,
    runTimeoutMinutes: 15,
  };
}

// ---- small KV helpers: three keys so writers never overwrite each other ----------------
//   "mac"   heartbeat time (written by the Mac every ~5 min)
//   "clock" {lastDispatch, lastUpdateId}  (written only by the cron, and only when it changes)
//   "runs"  {month, minutes, runs, runStarted, runEnded}  (written only by the workflow)
async function getJson(env, key) {
  try {
    return JSON.parse((await env.STATE.get(key)) || "{}");
  } catch {
    return {};
  }
}

export function monthKey(now) {
  return new Date(now).toISOString().slice(0, 7);
}

export function monthFraction(now) {
  const d = new Date(now);
  const start = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1);
  const end = Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1);
  return (now - start) / (end - start);
}

export function monthMinutes(runs, now) {
  return runs.month === monthKey(now) ? Number(runs.minutes) || 0 : 0;
}

// Alarm runs stay on pace: used minutes may not run ahead of the share of the month gone by.
export function alarmAllowed(used, now, s) {
  return used < s.budget && used <= s.budget * monthFraction(now) + 20;
}

export function macAlive(macValue, now, s) {
  const t = Number(macValue);
  return Boolean(t) && now - t < s.macStaleMinutes * MINUTE;
}

export function runInProgress(runs, now, s) {
  return Boolean(runs.runStarted) && runs.runStarted > (runs.runEnded || 0)
    && now - runs.runStarted < s.runTimeoutMinutes * MINUTE;
}

// ---- outside calls -----------------------------------------------------------------------
export async function newestOwnerUpdate(env, fetchFn) {
  // No offset => Telegram only shows pending updates and does NOT mark them as read,
  // so telegram_inbox.py still receives every command.
  const r = await fetchFn(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/getUpdates?timeout=0&limit=100`);
  if (!r.ok) return null;
  const data = await r.json();
  let newest = null;
  for (const u of data.result || []) {
    const chat = u.message && u.message.chat ? String(u.message.chat.id) : "";
    if (chat === String(env.TELEGRAM_CHAT_ID) && (newest === null || u.update_id > newest)) newest = u.update_id;
  }
  return newest;
}

export async function dispatch(env, s, reason, fetchFn) {
  const r = await fetchFn(`https://api.github.com/repos/${s.repo}/actions/workflows/${WORKFLOW}/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "chilli-gavva-clock",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ref: "main", inputs: { reason } }),
  });
  return r.status === 204;
}

// ---- the every-minute tick ---------------------------------------------------------------
export async function tick(env, now, fetchFn = fetch) {
  const s = settings(env);
  if (macAlive(await env.STATE.get("mac"), now, s)) return { action: "mac-alive" };
  const clock = await getJson(env, "clock");
  const runs = await getJson(env, "runs");
  const used = monthMinutes(runs, now);

  if (env.TELEGRAM_BOT_TOKEN && env.TELEGRAM_CHAT_ID && used < s.hardCap) {
    const newest = await newestOwnerUpdate(env, fetchFn).catch(() => null);
    if (newest !== null && newest > (clock.lastUpdateId ?? -1)) {
      if (!(await dispatch(env, s, "doorbell", fetchFn))) return { action: "doorbell-failed" };
      await env.STATE.put("clock", JSON.stringify({ ...clock, lastUpdateId: newest, lastDispatch: now }));
      return { action: "doorbell" };
    }
  }

  if (now - (clock.lastDispatch || 0) < s.alarmMinutes * MINUTE - 30 * 1000) return { action: "idle" };
  if (!alarmAllowed(used, now, s)) return { action: "budget-wait", used };
  if (runInProgress(runs, now, s)) return { action: "run-in-progress" };
  if (!(await dispatch(env, s, "alarm", fetchFn))) return { action: "alarm-failed" };
  await env.STATE.put("clock", JSON.stringify({ ...clock, lastDispatch: now }));
  return { action: "alarm" };
}

// ---- HTTP endpoints (all need "Authorization: Bearer <WORKER_SECRET>") ------------------
function json(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

export function authorized(request, env) {
  const want = `Bearer ${env.WORKER_SECRET || ""}`;
  const got = request.headers.get("Authorization") || "";
  if (!env.WORKER_SECRET || got.length !== want.length) return false;
  let diff = 0;
  for (let i = 0; i < want.length; i++) diff |= want.charCodeAt(i) ^ got.charCodeAt(i);
  return diff === 0;
}

export async function handle(request, env, now) {
  const url = new URL(request.url);
  if (url.pathname === "/") return new Response("Chilli Gavva clock is running.\n");
  if (!authorized(request, env)) return json({ error: "unauthorized" }, 401);
  const s = settings(env);
  const body = request.method === "POST" ? await request.json().catch(() => ({})) : {};

  if (url.pathname === "/heartbeat" && request.method === "POST") {
    if (body.stopping) await env.STATE.delete("mac");
    else await env.STATE.put("mac", String(now));
    return json({ ok: true });
  }
  if (url.pathname === "/run" && request.method === "POST") {
    const runs = await getJson(env, "runs");
    if (body.phase === "start") {
      if (macAlive(await env.STATE.get("mac"), now, s)) return json({ mac_alive: true });
      await env.STATE.put("runs", JSON.stringify({ ...runs, runStarted: now }));
      return json({ mac_alive: false });
    }
    if (body.phase === "end") {
      const minutes = Math.min(30, Math.max(1, Math.round(Number(body.minutes) || 1)));
      const month = monthKey(now);
      const fresh = runs.month === month ? runs : { month, minutes: 0, runs: 0 };
      const next = { ...fresh, month, minutes: fresh.minutes + minutes, runs: (fresh.runs || 0) + 1, runEnded: now,
        runStarted: runs.runStarted };
      await env.STATE.put("runs", JSON.stringify(next));
      return json({ ok: true, month_minutes: next.minutes });
    }
    return json({ error: "phase must be start or end" }, 400);
  }
  if (url.pathname === "/status" && request.method === "GET") {
    const mac = await env.STATE.get("mac");
    const clock = await getJson(env, "clock");
    const runs = await getJson(env, "runs");
    return json({
      mac_alive: macAlive(mac, now, s),
      mac_last_seen_minutes: mac ? Math.round((now - Number(mac)) / MINUTE) : null,
      run_in_progress: runInProgress(runs, now, s),
      minutes_since_last_dispatch: clock.lastDispatch ? Math.round((now - clock.lastDispatch) / MINUTE) : null,
      month_minutes: monthMinutes(runs, now),
      month_runs: runs.month === monthKey(now) ? runs.runs || 0 : 0,
      budget: s.budget,
      door_bell: Boolean(env.TELEGRAM_BOT_TOKEN && env.TELEGRAM_CHAT_ID),
    });
  }
  return json({ error: "not found" }, 404);
}

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(tick(env, Date.now())
      .then((r) => console.log(JSON.stringify(r)))
      .catch((e) => console.log("tick error: " + String(e && e.message).slice(0, 120))));
  },
  async fetch(request, env) {
    return handle(request, env, Date.now());
  },
};
