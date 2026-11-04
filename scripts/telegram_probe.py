"""Private terminal check: no credentials/identifiers/text are printed or saved.

By default this only peeks at pending updates, without acknowledging them.
--send-test sends one fixed message. Never run this in a public CI log.
"""
import getpass
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import telegram_diagnostics as diagnostics
import telegram_inbox as inbox


def probe(token, chat_id, expected_username, send_test=False):
    stats = inbox.new_stats()
    with diagnostics.session() as state:
        diagnostics.configuration(bool(token and chat_id))
        try:
            me = inbox._call(token, "getMe", {})
            identity = (me or {}).get("result") or {}
            actual = identity.get("username", "")
            matched = bool(actual and expected_username and
                           actual.casefold() == expected_username.lstrip("@").casefold())
            label = "match" if matched else "mismatch" if actual else "unknown"
            print("Telegram probe: bot_identity=" + label)
            info = inbox._call(token, "getWebhookInfo", {})
            info = (info or {}).get("result") or {}
            diagnostics.webhook(info)
            chat = inbox._call(token, "getChat", {"chat_id": chat_id})
            chat_ok = isinstance(chat, dict) and chat.get("ok") is True
            # Do not consume or acknowledge updates, even if a stale local DB has an offset.
            if matched and chat_ok and not info.get("url"):
                result = inbox._call(token, "getUpdates", {"timeout": 0, "limit": 100,
                                                           "allowed_updates": inbox.ALLOWED_UPDATES})
                updates = (result or {}).get("result") or []
                if isinstance(updates, list):
                    stats["received"] = len(updates)
                    for update in updates:
                        cb = update.get("callback_query") or {}
                        msg = cb.get("message") or update.get("message") or {}
                        if str((msg.get("chat") or {}).get("id", "")) != chat_id:
                            stats["skipped"]["not owner chat"] += 1
                        elif cb:
                            stats["button"] += 1
                        elif inbox.parse_command(msg.get("text"))[0]:
                            stats["commands"] += 1
                if send_test:
                    return inbox.send_text(token, chat_id, "Chilli Gavva: Telegram delivery check.")
            return matched and chat_ok and not info.get("url")
        finally:
            diagnostics.print_summary(state, stats)


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() or getpass.getpass("Bot token (hidden): ").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip() or getpass.getpass("Owner chat ID (hidden): ").strip()
    expected = input("Expected bot username (without @): ").strip()
    return 0 if probe(token, chat_id, expected, "--send-test" in sys.argv) else 1


if __name__ == "__main__":
    raise SystemExit(main())
