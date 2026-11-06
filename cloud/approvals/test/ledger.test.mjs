import test from "node:test";
import assert from "node:assert/strict";
import { ApprovalLedger } from "../ledger.js";

/**
 * Creates an in-memory SQL mock mimicking Cloudflare DO SQLite storage:
 * ctx.storage.sql.exec(sql, ...bindings) -> iterable / array of row objects
 */
function createMockSqlStorage() {
  const rows = [];
  let currentSeq = 0;

  return {
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
}

function createMockDOContext() {
  const sql = createMockSqlStorage();
  return {
    storage: {
      sql
    }
  };
}

test("Ledger: saves decision successfully", async () => {
  const ctx = createMockDOContext();
  const ledger = new ApprovalLedger(ctx, {});

  const res = await ledger.recordDecision({
    draftId: "101",
    action: "approve",
    reason: null,
    updateId: 1001,
    ts: "2025-01-01T00:00:00Z"
  });

  assert.equal(res.status, "saved");
  assert.equal(res.decision.draft_id, "101");
  assert.equal(res.decision.action, "approve");
  assert.equal(res.decision.seq, 1);
});

test("Ledger: duplicate update_id ignored and idempotent", async () => {
  const ctx = createMockDOContext();
  const ledger = new ApprovalLedger(ctx, {});

  const first = await ledger.recordDecision({
    draftId: "101",
    action: "approve",
    reason: null,
    updateId: 1001
  });
  assert.equal(first.status, "saved");

  const dup = await ledger.recordDecision({
    draftId: "101",
    action: "approve",
    reason: null,
    updateId: 1001
  });
  assert.equal(dup.status, "duplicate_update");
  assert.equal(dup.decision.seq, 1);
});

test("Ledger: first decision wins and subsequent decisions are ignored", async () => {
  const ctx = createMockDOContext();
  const ledger = new ApprovalLedger(ctx, {});

  const first = await ledger.recordDecision({
    draftId: "202",
    action: "approve",
    reason: null,
    updateId: 1002
  });
  assert.equal(first.status, "saved");

  const second = await ledger.recordDecision({
    draftId: "202",
    action: "reject",
    reason: "Too generic",
    updateId: 1003
  });
  assert.equal(second.status, "already_decided");
  assert.equal(second.decision.action, "approve");
  assert.equal(second.message, "already decided");
});

test("Ledger: /decisions paging", async () => {
  const ctx = createMockDOContext();
  const ledger = new ApprovalLedger(ctx, {});

  for (let i = 1; i <= 5; i++) {
    await ledger.recordDecision({
      draftId: `draft-${i}`,
      action: i % 2 === 0 ? "reject" : "approve",
      reason: i % 2 === 0 ? "Too generic" : null,
      updateId: 2000 + i
    });
  }

  // Fetch all since 0
  const reqAll = new Request("https://ledger/decisions?since=0&limit=10");
  const resAll = await ledger.fetch(reqAll);
  assert.equal(resAll.status, 200);
  const dataAll = await resAll.json();
  assert.equal(dataAll.count, 5);
  assert.equal(dataAll.decisions.length, 5);
  assert.equal(dataAll.decisions[0].seq, 1);

  // Paging: since seq 3 limit 2
  const reqPaged = new Request("https://ledger/decisions?since=3&limit=2");
  const resPaged = await ledger.fetch(reqPaged);
  assert.equal(resPaged.status, 200);
  const dataPaged = await resPaged.json();
  assert.equal(dataPaged.count, 2);
  assert.equal(dataPaged.decisions[0].seq, 4);
  assert.equal(dataPaged.decisions[1].seq, 5);
});
