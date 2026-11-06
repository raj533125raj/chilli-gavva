import test from "node:test";
import assert from "node:assert/strict";
import worker, { parseCallbackData } from "../worker.js";
import { ApprovalLedger } from "../ledger.js";

// Dummy test constant values kept short (<12 chars) to satisfy secret_guard regex
const FAKE_SECRET = "sec123";
const FAKE_OWNER = "12345";
const FAKE_READ = "rd123";

function createMockDOContext() {
  const rows = [];
  let currentSeq = 0;

  const sql = {
    exec(query, ...bindings) {
      const q = query.trim().replace(/\s+/g, " ");

      if (q.startsWith("CREATE TABLE") || q.startsWith("CREATE INDEX")) {
        return [];
      }

      if (q.startsWith("SELECT") && q.includes("WHERE update_id = ?")) {
        const updateId = bindings[0];
        const match = rows.filter((r) => r.update_id === updateId);
        return match;
      }

      if (q.startsWith("SELECT") && q.includes("WHERE draft_id = ?")) {
        const draftId = String(bindings[0]);
        const match = rows.filter((r) => r.draft_id === draftId);
        return match.slice(0, 1);
      }

      if (q.startsWith("SELECT") && q.includes("WHERE rowid = last_insert_rowid()")) {
        return [rows[rows.length - 1]];
      }

      if (q.startsWith("SELECT") && q.includes("WHERE seq > ?")) {
        const sinceSeq = Number(bindings[0]);
        const limit = Number(bindings[1]);
        const matching = rows
          .filter((r) => r.seq > sinceSeq)
          .sort((a, b) => a.seq - b.seq)
          .slice(0, limit);
        return matching;
      }

      if (q.startsWith("INSERT INTO decisions")) {
        currentSeq += 1;
        const [draft_id, action, reason, ts, update_id] = bindings;
        const record = {
          seq: currentSeq,
          draft_id: String(draft_id),
          action: String(action),
          reason: reason ?? null,
          ts: String(ts),
          update_id: update_id ?? null
        };
        rows.push(record);
        return [];
      }

      throw new Error(`Unhandled SQL query in mock: ${query}`);
    }
  };

  return {
    storage: { sql }
  };
}

function createMockEnv(overrides = {}) {
  const ledgerInstance = new ApprovalLedger(createMockDOContext(), {});

  const doNamespace = {
    idFromName(name) {
      return { name };
    },
    get(id) {
      return {
        fetch(url, init) {
          const req = new Request(url, init);
          return ledgerInstance.fetch(req);
        }
      };
    }
  };

  return {
    WEBHOOK_SECRET: FAKE_SECRET,
    OWNER_CHAT_ID: FAKE_OWNER,
    ROBOT_READ_TOKEN: FAKE_READ,
    APPROVAL_LEDGER: doNamespace,
    ...overrides
  };
}

test("parseCallbackData helper", () => {
  assert.deepEqual(parseCallbackData("approve:10"), {
    action: "approve",
    draftId: "10",
    reason: null
  });

  assert.deepEqual(parseCallbackData("reject:10:Too generic"), {
    action: "reject",
    draftId: "10",
    reason: "Too generic"
  });

  assert.deepEqual(parseCallbackData("reject:10"), {
    action: "reject",
    draftId: "10",
    reason: null
  });

  assert.equal(parseCallbackData("random"), null);
  assert.equal(parseCallbackData(null), null);
});

test("POST /telegram: wrong secret returns 401", async () => {
  const env = createMockEnv();
  const headers = new Headers();
  headers.set("Content-Type", "application/json");
  headers.set("X-Telegram-Bot-Api-Secret-Token", "wrong");

  const req = new Request("https://worker.local/telegram", {
    method: "POST",
    headers,
    body: JSON.stringify({ update_id: 1 })
  });

  const res = await worker.fetch(req, env, {});
  assert.equal(res.status, 401);
});

test("POST /telegram: bad JSON returns 400", async () => {
  const env = createMockEnv();
  const headers = new Headers();
  headers.set("Content-Type", "application/json");
  headers.set("X-Telegram-Bot-Api-Secret-Token", FAKE_SECRET);

  const req = new Request("https://worker.local/telegram", {
    method: "POST",
    headers,
    body: "invalid-json-payload {"
  });

  const res = await worker.fetch(req, env, {});
  assert.equal(res.status, 400);
  const data = await res.json();
  assert.equal(data.error, "Invalid JSON");
});

test("POST /telegram: non-owner ignored", async () => {
  const env = createMockEnv();
  const headers = new Headers();
  headers.set("Content-Type", "application/json");
  headers.set("X-Telegram-Bot-Api-Secret-Token", FAKE_SECRET);

  const req = new Request("https://worker.local/telegram", {
    method: "POST",
    headers,
    body: JSON.stringify({
      update_id: 2,
      callback_query: {
        id: "cb_1",
        from: { id: 999 },
        message: { chat: { id: 999 } },
        data: "approve:10"
      }
    })
  });

  const res = await worker.fetch(req, env, {});
  assert.equal(res.status, 200);
  const data = await res.json();
  assert.equal(data.ok, true);
  assert.equal(data.ignored, "non_owner");
});

test("POST /telegram: approve saved", async () => {
  const env = createMockEnv();
  const headers = new Headers();
  headers.set("Content-Type", "application/json");
  headers.set("X-Telegram-Bot-Api-Secret-Token", FAKE_SECRET);

  const req = new Request("https://worker.local/telegram", {
    method: "POST",
    headers,
    body: JSON.stringify({
      update_id: 3,
      callback_query: {
        id: "cb_2",
        from: { id: 12345 },
        message: { chat: { id: 12345 } },
        data: "approve:42"
      }
    })
  });

  const res = await worker.fetch(req, env, {});
  assert.equal(res.status, 200);
  const data = await res.json();
  assert.equal(data.ok, true);
  assert.equal(data.result.status, "saved");
  assert.equal(data.result.decision.draft_id, "42");
  assert.equal(data.result.decision.action, "approve");
});

test("POST /telegram: duplicate update ignored", async () => {
  const env = createMockEnv();
  const payload = {
    update_id: 4,
    callback_query: {
      id: "cb_3",
      from: { id: 12345 },
      message: { chat: { id: 12345 } },
      data: "approve:43"
    }
  };

  const headers = new Headers();
  headers.set("Content-Type", "application/json");
  headers.set("X-Telegram-Bot-Api-Secret-Token", FAKE_SECRET);

  const req1 = new Request("https://worker.local/telegram", {
    method: "POST",
    headers,
    body: JSON.stringify(payload)
  });
  const res1 = await worker.fetch(req1, env, {});
  assert.equal(res1.status, 200);
  const data1 = await res1.json();
  assert.equal(data1.result.status, "saved");

  const req2 = new Request("https://worker.local/telegram", {
    method: "POST",
    headers,
    body: JSON.stringify(payload)
  });
  const res2 = await worker.fetch(req2, env, {});
  assert.equal(res2.status, 200);
  const data2 = await res2.json();
  assert.equal(data2.result.status, "duplicate_update");
});

test("POST /telegram: first decision wins", async () => {
  const env = createMockEnv();
  const headers = new Headers();
  headers.set("Content-Type", "application/json");
  headers.set("X-Telegram-Bot-Api-Secret-Token", FAKE_SECRET);

  // First: approve 44
  const req1 = new Request("https://worker.local/telegram", {
    method: "POST",
    headers,
    body: JSON.stringify({
      update_id: 5,
      callback_query: {
        id: "cb_5",
        from: { id: 12345 },
        message: { chat: { id: 12345 } },
        data: "approve:44"
      }
    })
  });
  const res1 = await worker.fetch(req1, env, {});
  assert.equal(res1.status, 200);
  const data1 = await res1.json();
  assert.equal(data1.result.status, "saved");

  // Second: attempt reject 44 with different update_id
  const req2 = new Request("https://worker.local/telegram", {
    method: "POST",
    headers,
    body: JSON.stringify({
      update_id: 6,
      callback_query: {
        id: "cb_6",
        from: { id: 12345 },
        message: { chat: { id: 12345 } },
        data: "reject:44:Too generic"
      }
    })
  });
  const res2 = await worker.fetch(req2, env, {});
  assert.equal(res2.status, 200);
  const data2 = await res2.json();
  assert.equal(data2.result.status, "already_decided");
  assert.equal(data2.result.decision.action, "approve");
});

test("GET /decisions: unauthorized without robot read token", async () => {
  const env = createMockEnv();
  const req = new Request("https://worker.local/decisions?since=0");
  const res = await worker.fetch(req, env, {});
  assert.equal(res.status, 401);
});

test("GET /decisions: paging returns decisions after since", async () => {
  const env = createMockEnv();
  const headers = new Headers();
  headers.set("Content-Type", "application/json");
  headers.set("X-Telegram-Bot-Api-Secret-Token", FAKE_SECRET);

  // Populate two decisions
  await worker.fetch(new Request("https://worker.local/telegram", {
    method: "POST",
    headers,
    body: JSON.stringify({
      update_id: 11,
      callback_query: {
        from: { id: 12345 },
        message: { chat: { id: 12345 } },
        data: "approve:51"
      }
    })
  }), env, {});

  await worker.fetch(new Request("https://worker.local/telegram", {
    method: "POST",
    headers,
    body: JSON.stringify({
      update_id: 12,
      callback_query: {
        from: { id: 12345 },
        message: { chat: { id: 12345 } },
        data: "reject:52:Weak niche"
      }
    })
  }), env, {});

  const readHeaders = new Headers();
  readHeaders.set("Authorization", `Bearer ${FAKE_READ}`);

  const req = new Request("https://worker.local/decisions?since=1", {
    headers: readHeaders
  });
  const res = await worker.fetch(req, env, {});
  assert.equal(res.status, 200);
  const data = await res.json();
  assert.equal(data.decisions.length, 1);
  assert.equal(data.decisions[0].draft_id, "52");
  assert.equal(data.decisions[0].action, "reject");
  assert.equal(data.decisions[0].reason, "Weak niche");
});

test("Unknown path returns 404", async () => {
  const env = createMockEnv();
  const req = new Request("https://worker.local/unknown", { method: "GET" });
  const res = await worker.fetch(req, env, {});
  assert.equal(res.status, 404);
});
