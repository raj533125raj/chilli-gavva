"""Unit tests for Telegram plugin loader, context builders, and multipart/keyboard helpers.

Tests cover:
  1. Plugin discovery (sorting, filtering, error handling & logging once).
  2. Plugin execution & lifecycle (dispatch, return values None vs dict vs str vs handled).
  3. Ctx methods (send_text, send_document, send_photo, send_media_group, state_get, state_set, now, db_path).
  4. Multipart request format and headers without network calls (mocked urlopen).
  5. ReplyKeyboardMarkup with resize_keyboard and ReplyKeyboardRemove.
  6. Non-owner message isolation (plugins never run for non-owner chats).
  7. Existing built-in commands remain fully functional.
"""
import datetime
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import guardrails
import telegram_inbox as ti

OWNER = "111222333"


def _upd(uid, text, chat=OWNER):
    return {"update_id": uid, "message": {"chat": {"id": int(chat)}, "text": text}}


class PluginLoaderTests(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {
            "GITHUB_ACTIONS": "",
            "RUNNER_ENVIRONMENT": "",
            "GITHUB_RUN_ID": "",
            "CHILLI_TEST": "1"
        })
        self.env.start()
        self.addCleanup(self.env.stop)

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "plugins_test.db")

        # Mock telegram calls
        self.mock_call = mock.patch.object(ti, "_call", return_value={"ok": True})
        self.mock_call.start()
        self.addCleanup(self.mock_call.stop)

    def test_discover_plugins_finds_tg_menu(self):
        plugins = ti.discover_plugins()
        plugin_names = [p.__name__ for p in plugins]
        self.assertIn("tg_menu", plugin_names)
        # Check sorting
        self.assertEqual(plugin_names, sorted(plugin_names))

    def test_discover_plugins_custom_directory(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)

        # Create two valid plugins and one non-plugin file
        p2 = os.path.join(d.name, "tg_zebra.py")
        p1 = os.path.join(d.name, "tg_apple.py")
        other = os.path.join(d.name, "other.py")

        with open(p2, "w") as f:
            f.write("def handle(text, ctx): return None\n")
        with open(p1, "w") as f:
            f.write("def handle(text, ctx): return None\n")
        with open(other, "w") as f:
            f.write("def handle(text, ctx): return None\n")

        plugins = ti.discover_plugins(repo_root=d.name)
        names = [p.__name__ for p in plugins]
        self.assertEqual(names, ["tg_apple", "tg_zebra"])

    def test_discover_plugins_broken_plugin_logged_once_and_skipped(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)

        broken = os.path.join(d.name, "tg_broken.py")
        with open(broken, "w") as f:
            f.write("syntax error here !!!\n")

        stderr_buf = io.StringIO()
        with mock.patch("sys.stderr", stderr_buf):
            plugins1 = ti.discover_plugins(repo_root=d.name)
            plugins2 = ti.discover_plugins(repo_root=d.name)

        self.assertEqual(len(plugins1), 0)
        self.assertEqual(len(plugins2), 0)
        err_output = stderr_buf.getvalue()
        self.assertEqual(err_output.count("Telegram plugin load error for tg_broken"), 1)

    def test_build_plugin_ctx_fields(self):
        now_dt = datetime.datetime(2026, 11, 1, 10, 0, 0, tzinfo=datetime.timezone.utc)
        ctx = ti.build_plugin_ctx("test_tok", OWNER, db_path=self.db, now=now_dt)

        self.assertEqual(ctx["chat_id"], OWNER)
        self.assertEqual(ctx["db_path"], self.db)
        self.assertEqual(ctx["now"], now_dt)
        self.assertTrue(callable(ctx["send_text"]))
        self.assertTrue(callable(ctx["send_document"]))
        self.assertTrue(callable(ctx["send_photo"]))
        self.assertTrue(callable(ctx["send_media_group"]))
        self.assertTrue(callable(ctx["state_get"]))
        self.assertTrue(callable(ctx["state_set"]))

    def test_plugin_ctx_state_get_and_set(self):
        ctx = ti.build_plugin_ctx("test_tok", OWNER, db_path=self.db)
        self.assertIsNone(ctx["state_get"]("custom_key"))
        self.assertEqual(ctx["state_get"]("custom_key", "default_val"), "default_val")

        ctx["state_set"]("custom_key", "val_123")
        self.assertEqual(ctx["state_get"]("custom_key"), "val_123")

    def test_call_plugins_dispatch_order(self):
        class PluginA:
            __name__ = "tg_a"
            @staticmethod
            def handle(text, ctx):
                if text == "ping":
                    return {"text": "pong from A"}
                return None

        class PluginB:
            __name__ = "tg_b"
            @staticmethod
            def handle(text, ctx):
                if text == "ping":
                    return {"text": "pong from B"}
                return None

        handled, res = ti.call_plugins("ping", "tok", OWNER, db_path=self.db, plugins=[PluginA, PluginB])
        self.assertTrue(handled)
        self.assertEqual(res, {"text": "pong from A"})

    def test_call_plugins_skips_when_returns_none(self):
        class PluginA:
            __name__ = "tg_a"
            @staticmethod
            def handle(text, ctx):
                return None

        class PluginB:
            __name__ = "tg_b"
            @staticmethod
            def handle(text, ctx):
                if text == "target":
                    return {"text": "handled by B"}
                return None

        handled, res = ti.call_plugins("target", "tok", OWNER, db_path=self.db, plugins=[PluginA, PluginB])
        self.assertTrue(handled)
        self.assertEqual(res, {"text": "handled by B"})

    def test_call_plugins_returns_false_when_unhandled(self):
        class PluginA:
            __name__ = "tg_a"
            @staticmethod
            def handle(text, ctx):
                return None

        handled, res = ti.call_plugins("unhandled", "tok", OWNER, db_path=self.db, plugins=[PluginA])
        self.assertFalse(handled)
        self.assertIsNone(res)

    def test_call_plugins_handles_str_and_handled_true(self):
        class PluginStr:
            __name__ = "tg_str"
            @staticmethod
            def handle(text, ctx):
                return "plain text reply"

        handled, res = ti.call_plugins("hi", "tok", OWNER, db_path=self.db, plugins=[PluginStr])
        self.assertTrue(handled)
        self.assertEqual(res, {"text": "plain text reply"})

        class PluginHandled:
            __name__ = "tg_h"
            @staticmethod
            def handle(text, ctx):
                return {"handled": True}

        handled, res = ti.call_plugins("hi", "tok", OWNER, db_path=self.db, plugins=[PluginHandled])
        self.assertTrue(handled)
        self.assertEqual(res, {"handled": True})

    def test_plugin_exception_is_caught_and_continues(self):
        class PluginCrash:
            @staticmethod
            def handle(text, ctx):
                raise RuntimeError("boom")
        PluginCrash.__name__ = "tg_crash"

        class PluginGood:
            @staticmethod
            def handle(text, ctx):
                return {"text": "recovered"}
        PluginGood.__name__ = "tg_good"

        stderr_buf = io.StringIO()
        with mock.patch("sys.stderr", stderr_buf):
            handled, res = ti.call_plugins("hi", "tok", OWNER, db_path=self.db, plugins=[PluginCrash, PluginGood])

        self.assertTrue(handled)
        self.assertEqual(res, {"text": "recovered"})
        self.assertIn("Plugin error in tg_crash: boom", stderr_buf.getvalue())


class SendTextKeyboardTests(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {
            "GITHUB_ACTIONS": "",
            "RUNNER_ENVIRONMENT": "",
            "GITHUB_RUN_ID": "",
            "CHILLI_TEST": "1"
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_send_text_reply_keyboard_markup_format(self):
        captured = {}
        def fake_call(tok, method, params):
            captured.update(params)
            return {"ok": True}

        with mock.patch.object(ti, "_call", side_effect=fake_call):
            ti.send_text("tok", OWNER, "Choose option", keyboard=[["✅ 91", "❌ 91"], ["/menu"]])

        self.assertEqual(captured["text"], "Choose option")
        self.assertIn("reply_markup", captured)
        rm = captured["reply_markup"]
        self.assertTrue(rm.get("resize_keyboard"))
        self.assertEqual(rm.get("keyboard"), [
            [{"text": "✅ 91"}, {"text": "❌ 91"}],
            [{"text": "/menu"}]
        ])

    def test_send_text_reply_keyboard_remove(self):
        captured = {}
        def fake_call(tok, method, params):
            captured.update(params)
            return {"ok": True}

        with mock.patch.object(ti, "_call", side_effect=fake_call):
            ti.send_text("tok", OWNER, "Cleared", keyboard="remove")

        self.assertEqual(captured["reply_markup"], {"remove_keyboard": True})

    def test_send_text_buttons_takes_precedence_over_keyboard(self):
        captured = {}
        def fake_call(tok, method, params):
            captured.update(params)
            return {"ok": True}

        with mock.patch.object(ti, "_call", side_effect=fake_call):
            ti.send_text("tok", OWNER, "Pick", buttons=[[("Btn", "data:1")]], keyboard=[["Kb"]])

        self.assertIn("inline_keyboard", captured["reply_markup"])
        self.assertNotIn("keyboard", captured["reply_markup"])


class MultipartUploadTests(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {
            "GITHUB_ACTIONS": "",
            "RUNNER_ENVIRONMENT": "",
            "GITHUB_RUN_ID": "",
            "CHILLI_TEST": "1"
        })
        self.env.start()
        self.addCleanup(self.env.stop)

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_send_document_multipart_payload(self):
        doc_path = os.path.join(self.tmp.name, "sample.txt")
        with open(doc_path, "wb") as f:
            f.write(b"Hello digital product document")

        captured_req = []
        class FakeResp:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return json.dumps({"ok": True}).encode("utf-8")

        def fake_urlopen(req, timeout=None):
            captured_req.append(req)
            return FakeResp()

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            ok = ti.send_document("tok", OWNER, doc_path, caption="Here is your document")

        self.assertTrue(ok)
        self.assertEqual(len(captured_req), 1)
        req = captured_req[0]
        self.assertIn("sendDocument", req.full_url)
        content_type = req.headers.get("Content-type") or req.headers.get("Content-Type")
        self.assertIn("multipart/form-data; boundary=", content_type)

        body = req.data
        self.assertIn(b'name="chat_id"', body)
        self.assertIn(OWNER.encode(), body)
        self.assertIn(b'name="caption"', body)
        self.assertIn(b"Here is your document", body)
        self.assertIn(b'name="document"; filename="sample.txt"', body)
        self.assertIn(b"Hello digital product document", body)

    def test_send_photo_multipart_payload(self):
        photo_path = os.path.join(self.tmp.name, "cover.png")
        with open(photo_path, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\nfake png data")

        captured_req = []
        class FakeResp:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return json.dumps({"ok": True}).encode("utf-8")

        def fake_urlopen(req, timeout=None):
            captured_req.append(req)
            return FakeResp()

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            ok = ti.send_photo("tok", OWNER, photo_path, caption="Product Cover")

        self.assertTrue(ok)
        self.assertEqual(len(captured_req), 1)
        req = captured_req[0]
        self.assertIn("sendPhoto", req.full_url)
        body = req.data
        self.assertIn(b'name="photo"; filename="cover.png"', body)
        self.assertIn(b"fake png data", body)
        self.assertIn(b"Product Cover", body)

    def test_send_media_group_multipart_payload(self):
        p1 = os.path.join(self.tmp.name, "slide1.png")
        p2 = os.path.join(self.tmp.name, "slide2.jpg")
        with open(p1, "wb") as f:
            f.write(b"png data 1")
        with open(p2, "wb") as f:
            f.write(b"jpg data 2")

        captured_req = []
        class FakeResp:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return json.dumps({"ok": True}).encode("utf-8")

        def fake_urlopen(req, timeout=None):
            captured_req.append(req)
            return FakeResp()

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            ok = ti.send_media_group("tok", OWNER, [p1, p2], caption="Slides preview")

        self.assertTrue(ok)
        self.assertEqual(len(captured_req), 1)
        req = captured_req[0]
        self.assertIn("sendMediaGroup", req.full_url)
        body = req.data
        self.assertIn(b'name="media"', body)
        self.assertIn(b'name="attach_0"; filename="slide1.png"', body)
        self.assertIn(b'name="attach_1"; filename="slide2.jpg"', body)
        self.assertIn(b"Slides preview", body)

    def test_send_document_nonexistent_file_returns_false(self):
        ok = ti.send_document("tok", OWNER, "/nonexistent/file.pdf")
        self.assertFalse(ok)

    def test_send_media_group_empty_or_excessive_returns_false(self):
        self.assertFalse(ti.send_media_group("tok", OWNER, []))
        dummy_paths = [os.path.join(self.tmp.name, f"f{i}.png") for i in range(12)]
        for p in dummy_paths:
            with open(p, "wb") as f:
                f.write(b"x")
        self.assertFalse(ti.send_media_group("tok", OWNER, dummy_paths))

    def test_send_photo_nonexistent_returns_false(self):
        self.assertFalse(ti.send_photo("tok", OWNER, "/nonexistent/photo.png"))

    def test_send_document_caption_scrubbed(self):
        doc_path = os.path.join(self.tmp.name, "sensitive.txt")
        with open(doc_path, "wb") as f:
            f.write(b"doc data")

        captured_req = []
        class FakeResp:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return json.dumps({"ok": True}).encode("utf-8")

        def fake_urlopen(req, timeout=None):
            captured_req.append(req)
            return FakeResp()

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            ok = ti.send_document("tok", OWNER, doc_path, caption="Here is Bearer secret123 token")

        self.assertTrue(ok)
        body = captured_req[0].data
        self.assertNotIn(b"secret123", body)

    def test_send_photo_caption_scrubbed(self):
        photo_path = os.path.join(self.tmp.name, "sensitive.png")
        with open(photo_path, "wb") as f:
            f.write(b"png data")

        captured_req = []
        class FakeResp:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return json.dumps({"ok": True}).encode("utf-8")

        def fake_urlopen(req, timeout=None):
            captured_req.append(req)
            return FakeResp()

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            ok = ti.send_photo("tok", OWNER, photo_path, caption="Sensitive key=secret999")

        self.assertTrue(ok)
        body = captured_req[0].data
        self.assertNotIn(b"secret999", body)

    def test_multipart_http_429_retry_success(self):
        doc_path = os.path.join(self.tmp.name, "retry.txt")
        with open(doc_path, "wb") as f:
            f.write(b"retry data")

        calls = [0]
        class FakeResp:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return json.dumps({"ok": True}).encode("utf-8")

        import urllib.error
        import io
        def fake_urlopen(req, timeout=None):
            calls[0] += 1
            if calls[0] == 1:
                err_body = json.dumps({"ok": False, "error_code": 429, "parameters": {"retry_after": 0}}).encode("utf-8")
                raise urllib.error.HTTPError(req.full_url, 429, "Rate limit", {}, io.BytesIO(err_body))
            return FakeResp()

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            ok = ti.send_document("tok", OWNER, doc_path)

        self.assertTrue(ok)
        self.assertEqual(calls[0], 2)

    def test_multipart_http_500_failure(self):
        doc_path = os.path.join(self.tmp.name, "fail.txt")
        with open(doc_path, "wb") as f:
            f.write(b"data")

        import urllib.error
        import io
        def fake_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 500, "Server Error", {}, io.BytesIO(b"{}"))

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            ok = ti.send_document("tok", OWNER, doc_path)

        self.assertFalse(ok)

    def test_multipart_network_exception_returns_false(self):
        doc_path = os.path.join(self.tmp.name, "exc.txt")
        with open(doc_path, "wb") as f:
            f.write(b"data")

        def fake_urlopen(req, timeout=None):
            raise OSError("Connection reset by peer")

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            ok = ti.send_document("tok", OWNER, doc_path)

        self.assertFalse(ok)


class NonOwnerAndExistingCommandsTests(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {
            "GITHUB_ACTIONS": "",
            "RUNNER_ENVIRONMENT": "",
            "GITHUB_RUN_ID": "",
            "CHILLI_TEST": "1"
        })
        self.env.start()
        self.addCleanup(self.env.stop)

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "isolation.db")
        self.sent = []

        def fake_send(tok, chat, text, buttons=None, keyboard=None):
            self.sent.append((chat, text))
            return True

        p1 = mock.patch.object(ti, "send_text", side_effect=fake_send)
        p1.start()
        self.addCleanup(p1.stop)

        p2 = mock.patch.object(ti, "_call", return_value={"ok": True})
        p2.start()
        self.addCleanup(p2.stop)

    def test_non_owner_ignored_by_plugins(self):
        # A stranger sending 'menu' or greeting must be completely ignored
        updates = [_upd(10, "menu", chat="9999999"), _upd(11, "hi", chat="9999999")]
        with mock.patch.object(ti, "fetch_updates", return_value=updates):
            handled = ti.process_updates("tok", OWNER, self.db)

        self.assertEqual(handled, 0)
        self.assertEqual(len(self.sent), 0)

    def test_existing_command_status_still_works(self):
        updates = [_upd(20, "/status")]
        with mock.patch.object(ti, "fetch_updates", return_value=updates):
            handled = ti.process_updates("tok", OWNER, self.db)

        self.assertEqual(handled, 1)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("🤖 Chilli Gavva Status", self.sent[0][1])

    def test_existing_command_pause_resume_still_works(self):
        updates = [_upd(30, "/pause")]
        with mock.patch.object(ti, "fetch_updates", return_value=updates):
            ti.process_updates("tok", OWNER, self.db)

        self.assertTrue(guardrails.is_paused(self.db))

        updates2 = [_upd(31, "/resume")]
        with mock.patch.object(ti, "fetch_updates", return_value=updates2):
            ti.process_updates("tok", OWNER, self.db)

        self.assertFalse(guardrails.is_paused(self.db))

    def test_existing_command_packs_still_works(self):
        updates = [_upd(40, "/packs")]
        with mock.patch.object(ti, "fetch_updates", return_value=updates):
            handled = ti.process_updates("tok", OWNER, self.db)

        self.assertEqual(handled, 1)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("packs", self.sent[0][1].lower())

    def test_existing_command_today_still_works(self):
        updates = [_upd(41, "/today")]
        with mock.patch.object(ti, "fetch_updates", return_value=updates):
            handled = ti.process_updates("tok", OWNER, self.db)

        self.assertEqual(handled, 1)
        self.assertEqual(len(self.sent), 1)

    def test_existing_command_pipeline_still_works(self):
        updates = [_upd(42, "/pipeline")]
        with mock.patch.object(ti, "fetch_updates", return_value=updates):
            handled = ti.process_updates("tok", OWNER, self.db)

        self.assertEqual(handled, 1)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Product Pipeline Status", self.sent[0][1])

    def test_existing_command_week_still_works(self):
        updates = [_upd(43, "/week")]
        with mock.patch.object(ti, "fetch_updates", return_value=updates):
            handled = ti.process_updates("tok", OWNER, self.db)

        self.assertEqual(handled, 1)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Ee Vaaram Summary", self.sent[0][1])

    def test_existing_command_doctor_still_works(self):
        updates = [_upd(44, "/doctor")]
        with mock.patch.object(ti, "fetch_updates", return_value=updates):
            handled = ti.process_updates("tok", OWNER, self.db)

        self.assertEqual(handled, 1)
        self.assertEqual(len(self.sent), 1)


if __name__ == "__main__":
    unittest.main()
