/**
 * Durable Object storing owner approval / rejection decisions backed by SQLite.
 *
 * Cloudflare Workers SQLite-in-Durable-Objects API uses `this.ctx.storage.sql.exec(query, ...bindings)`.
 * Table schema:
 *   decisions(seq INTEGER PRIMARY KEY, draft_id, action, reason, ts, update_id UNIQUE)
 *
 * Rules:
 *   - Same update_id twice = stored once (idempotent, return status: "duplicate_update")
 *   - First decision for a draft wins; later ones are ignored and reported as "already decided"
 */

export class ApprovalLedger {
  constructor(ctx, env) {
    this.ctx = ctx;
    this.env = env;
    this.sql = ctx.storage.sql;
    this._initSchema();
  }

  _initSchema() {
    this.sql.exec(`
      CREATE TABLE IF NOT EXISTS decisions (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        draft_id TEXT NOT NULL,
        action TEXT NOT NULL,
        reason TEXT,
        ts TEXT NOT NULL,
        update_id INTEGER UNIQUE
      )
    `);
    this.sql.exec(`
      CREATE INDEX IF NOT EXISTS idx_decisions_draft_id ON decisions (draft_id)
    `);
  }

  async recordDecision({ draftId, action, reason, updateId, ts }) {
    const draftIdStr = String(draftId);
    const now = ts || new Date().toISOString();

    // 1. Idempotency check: same update_id
    if (updateId !== undefined && updateId !== null) {
      const existingUpdate = [...this.sql.exec(
        "SELECT seq, draft_id, action, reason, ts, update_id FROM decisions WHERE update_id = ?",
        updateId
      )];
      if (existingUpdate.length > 0) {
        return {
          status: "duplicate_update",
          message: "Update already processed",
          decision: existingUpdate[0]
        };
      }
    }

    // 2. First decision for a draft wins
    const existingDraft = [...this.sql.exec(
      "SELECT seq, draft_id, action, reason, ts, update_id FROM decisions WHERE draft_id = ? ORDER BY seq ASC LIMIT 1",
      draftIdStr
    )];
    if (existingDraft.length > 0) {
      return {
        status: "already_decided",
        message: "already decided",
        decision: existingDraft[0]
      };
    }

    // 3. Insert new decision
    this.sql.exec(
      "INSERT INTO decisions (draft_id, action, reason, ts, update_id) VALUES (?, ?, ?, ?, ?)",
      draftIdStr,
      action,
      reason ?? null,
      now,
      updateId ?? null
    );

    const inserted = [...this.sql.exec(
      "SELECT seq, draft_id, action, reason, ts, update_id FROM decisions WHERE rowid = last_insert_rowid()"
    )];

    return {
      status: "saved",
      message: "Decision recorded",
      decision: inserted[0]
    };
  }

  async getDecisions(sinceSeq = 0, limit = 100) {
    const minSeq = Number(sinceSeq) || 0;
    const maxLimit = Math.min(Math.max(Number(limit) || 50, 1), 500);

    const cursor = this.sql.exec(
      "SELECT seq, draft_id, action, reason, ts, update_id FROM decisions WHERE seq > ? ORDER BY seq ASC LIMIT ?",
      minSeq,
      maxLimit
    );
    const rows = [...cursor];

    return {
      decisions: rows,
      count: rows.length,
      since: minSeq
    };
  }

  async fetch(request) {
    const url = new URL(request.url);

    if (request.method === "POST" && url.pathname === "/record") {
      try {
        const body = await request.json();
        const result = await this.recordDecision(body);
        return new Response(JSON.stringify(result), {
          status: 200,
          headers: { "Content-Type": "application/json" }
        });
      } catch (err) {
        return new Response(JSON.stringify({ error: err.message }), {
          status: 400,
          headers: { "Content-Type": "application/json" }
        });
      }
    }

    if (request.method === "GET" && url.pathname === "/decisions") {
      const since = url.searchParams.get("since") || 0;
      const limit = url.searchParams.get("limit") || 100;
      const result = await this.getDecisions(since, limit);
      return new Response(JSON.stringify(result), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      });
    }

    return new Response("Not Found", { status: 404 });
  }
}
