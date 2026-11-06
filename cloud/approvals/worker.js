/**
 * Cloudflare Worker entry point for approval webhook and robot ledger query.
 *
 * Endpoints:
 * - POST /telegram: Telegram webhook receiver
 *     - Header X-Telegram-Bot-Api-Secret-Token must equal env.WEBHOOK_SECRET (else 401)
 *     - Accept callback_query only if from env.OWNER_CHAT_ID
 *     - Parse callback data: "approve:<id>" or "reject:<id>:<reason>"
 *     - Save to Durable Object (APPROVAL_LEDGER)
 *     - Answer 200 fast (CPU < 10ms, minimal work)
 * - GET /decisions?since=<n>:
 *     - Header Authorization: Bearer <env.ROBOT_READ_TOKEN> (else 401)
 *     - Returns decisions after sequence n as JSON
 * - Everything else: 404
 */

export { ApprovalLedger } from "./ledger.js";

/**
 * Parse callback data format:
 * "approve:<id>" or "reject:<id>:<reason>"
 * Examples:
 *   "approve:12" -> { action: "approve", draftId: "12", reason: null }
 *   "reject:12:Too generic" -> { action: "reject", draftId: "12", reason: "Too generic" }
 */
export function parseCallbackData(data) {
  if (typeof data !== "string") {
    return null;
  }

  if (data.startsWith("approve:")) {
    const draftId = data.slice("approve:".length).trim();
    if (!draftId) return null;
    return {
      action: "approve",
      draftId,
      reason: null
    };
  }

  if (data.startsWith("reject:")) {
    const rest = data.slice("reject:".length);
    const colonIndex = rest.indexOf(":");
    if (colonIndex === -1) {
      const draftId = rest.trim();
      if (!draftId) return null;
      return {
        action: "reject",
        draftId,
        reason: null
      };
    }
    const draftId = rest.slice(0, colonIndex).trim();
    const reason = rest.slice(colonIndex + 1).trim();
    if (!draftId) return null;
    return {
      action: "reject",
      draftId,
      reason: reason || null
    };
  }

  return null;
}

function getLedgerStub(env) {
  // Use a singleton Durable Object ID for the approvals ledger
  const id = env.APPROVAL_LEDGER.idFromName("primary-ledger");
  return env.APPROVAL_LEDGER.get(id);
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // 1. POST /telegram
    if (request.method === "POST" && url.pathname === "/telegram") {
      const secretHeader = request.headers.get("X-Telegram-Bot-Api-Secret-Token");
      if (!env.WEBHOOK_SECRET || secretHeader !== env.WEBHOOK_SECRET) {
        return new Response("Unauthorized", { status: 401 });
      }

      let payload;
      try {
        payload = await request.json();
      } catch (e) {
        return new Response(JSON.stringify({ error: "Invalid JSON" }), {
          status: 400,
          headers: { "Content-Type": "application/json" }
        });
      }

      // Check callback_query
      const callbackQuery = payload?.callback_query;
      if (!callbackQuery) {
        // Non-callback updates (like normal text messages) are ignored safely and acknowledged fast
        return new Response(JSON.stringify({ ok: true, ignored: "not_callback_query" }), {
          status: 200,
          headers: { "Content-Type": "application/json" }
        });
      }

      // Accept only from env.OWNER_CHAT_ID
      const messageChatId = callbackQuery.message?.chat?.id;
      const fromId = callbackQuery.from?.id;
      const ownerChatIdStr = String(env.OWNER_CHAT_ID);

      const isFromOwner =
        (messageChatId !== undefined && String(messageChatId) === ownerChatIdStr) ||
        (fromId !== undefined && String(fromId) === ownerChatIdStr);

      if (!isFromOwner) {
        return new Response(JSON.stringify({ ok: true, ignored: "non_owner" }), {
          status: 200,
          headers: { "Content-Type": "application/json" }
        });
      }

      const parsed = parseCallbackData(callbackQuery.data);
      if (!parsed) {
        return new Response(JSON.stringify({ ok: true, ignored: "unrecognized_callback_data" }), {
          status: 200,
          headers: { "Content-Type": "application/json" }
        });
      }

      // Save to Durable Object
      const stub = getLedgerStub(env);
      const recordPayload = {
        draftId: parsed.draftId,
        action: parsed.action,
        reason: parsed.reason,
        updateId: payload.update_id ?? null,
        ts: new Date().toISOString()
      };

      const doResponse = await stub.fetch("https://ledger/record", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(recordPayload)
      });

      const result = await doResponse.json();

      return new Response(JSON.stringify({ ok: true, result }), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      });
    }

    // 2. GET /decisions
    if (request.method === "GET" && url.pathname === "/decisions") {
      const authHeader = request.headers.get("Authorization");
      const expectedToken = env.ROBOT_READ_TOKEN;

      if (!expectedToken || !authHeader || authHeader !== `Bearer ${expectedToken}`) {
        return new Response("Unauthorized", { status: 401 });
      }

      const since = url.searchParams.get("since") || "0";
      const limit = url.searchParams.get("limit") || "100";

      const stub = getLedgerStub(env);
      const doResponse = await stub.fetch(`https://ledger/decisions?since=${encodeURIComponent(since)}&limit=${encodeURIComponent(limit)}`);
      const body = await doResponse.text();

      return new Response(body, {
        status: doResponse.status,
        headers: { "Content-Type": "application/json" }
      });
    }

    // 3. Everything else: 404
    return new Response("Not Found", { status: 404 });
  }
};
