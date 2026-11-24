"""CXG-1: phone rendering preserves data, transports, keyboards and state."""
import os
import unittest
from unittest import mock

import tg_menu


class OwnerRenderingTests(unittest.TestCase):
    def assert_page(self, page, limit=3800):
        self.assertLessEqual(len(page.splitlines()), 15)
        self.assertLessEqual(len(page), limit)
        self.assertTrue(page.splitlines()[-1].startswith("Next:"))
        self.assertEqual(sum(line.startswith("Next:") for line in page.splitlines()), 1)

    def test_long_report_keeps_every_row_once(self):
        rows = [f"Pack #{number}: Draft chudandi" for number in range(100)]
        pages = tg_menu.owner_reply_pages("\n".join(rows))
        self.assertGreater(len(pages), 1)
        for page in pages:
            self.assert_page(page)
        actual = [line for page in pages for line in page.splitlines()[:-1]]
        self.assertEqual(actual, rows)

    def test_listing_field_longer_than_a_telegram_message_is_kept(self):
        field = "Planner vivaralu " * 1000
        pages = tg_menu.owner_reply_pages(field)
        for page in pages:
            self.assert_page(page)
        actual = " ".join(line for page in pages for line in page.splitlines()[:-1])
        self.assertEqual(" ".join(actual.split()), " ".join(field.split()))

    def test_existing_next_actions_are_at_the_end(self):
        pages = tg_menu.owner_reply_pages("Pack ready. Next: /files 1 pampandi.\nNext: /ready 1 chudandi.")
        self.assertEqual(len(pages), 1)
        self.assert_page(pages[0])
        self.assertIn("Next: /files 1 pampandi.; /ready 1 chudandi.", pages[0])

    def test_secrets_are_scrubbed_before_pagination(self):
        value = "fake_cxg_private_value_" + "a" * 900
        with mock.patch.dict(os.environ, {"WORKER_SECRET": value}):
            pages = tg_menu.owner_reply_pages("Pack: " + value)
        self.assertNotIn(value, "\n".join(pages))
        self.assertNotIn("a" * 40, "\n".join(pages))

    def test_python_values_money_and_machine_paths_are_rephrased(self):
        text = "None True False dict traceback ValueError /Users/owner/private/pack.zip " + chr(36) + str(12)
        output = "\n".join(tg_menu.owner_reply_pages(text))
        self.assertNotRegex(output, r"\b(?:None|True|False|dict|traceback|ValueError)\b")
        self.assertNotIn("/Users/", output)
        self.assertIn("[amount]", output)

    def test_public_urls_and_commands_are_kept(self):
        text = "Pack: https://example.com/store/pack\n/ready 1 pampandi."
        output = "\n".join(tg_menu.owner_reply_pages(text))
        self.assertIn("https://example.com/store/pack", output)
        self.assertIn("/ready 1", output)

    def test_store_button_label_is_not_moved_into_the_next_action(self):
        output = "\n".join(tg_menu.owner_reply_pages("'Next: Customize' click cheyandi.\nPack chudandi."))
        self.assertIn("'Next: Customize' click cheyandi.", output.splitlines()[0])
        self.assert_page(output)

    def test_transport_state_and_keyboard_are_preserved(self):
        sent, files, states = [], [], []
        keyboard = [["Approve", "Reject"]]

        @tg_menu.owner_reply
        def handle(text, ctx):
            ctx["state_set"]("choice", "waiting")
            ctx["send_text"]("\n".join(f"Draft #{n}" for n in range(40)), keyboard=keyboard)
            return {"text": "Draft chudandi.", "keyboard": keyboard}

        ctx = {"send_text": lambda text, keyboard=None: sent.append((text, keyboard)),
               "send_document": lambda path, caption="": files.append((path, caption)),
               "state_set": lambda key, value: states.append((key, value))}
        original = dict(ctx)
        reply = handle("/decide", ctx)
        self.assertEqual(ctx, original)
        self.assertEqual(states, [("choice", "waiting")])
        self.assertEqual(reply["keyboard"], keyboard)
        self.assertEqual(sent[-1][1], keyboard)
        self.assertTrue(all(k is None for _, k in sent[:-1]))
        for page, _ in sent:
            self.assert_page(page)

    def test_media_failure_and_caption_overflow_are_preserved(self):
        sent, media = [], []

        @tg_menu.owner_reply
        def handle(text, ctx):
            ok = ctx["send_photo"]("pack.png", caption="\n".join(f"Pin #{n}" for n in range(90)))
            return {"text": "Image malli pampandi." if ok is False else "Image pampinchanu."}

        def photo(path, caption=""):
            media.append((path, caption))
            return False

        reply = handle("/pins", {"send_photo": photo,
                                 "send_text": lambda text, keyboard=None: sent.append(text)})
        self.assertIn("malli pampandi", reply["text"])
        self.assertEqual(media[0][0], "pack.png")
        self.assert_page(media[0][1], limit=1000)
        content = "\n".join([media[0][1]] + sent)
        for n in range(90):
            self.assertIn(f"Pin #{n}\n", content)
        for page in sent:
            self.assert_page(page)

    def test_unhandled_command_stays_unhandled(self):
        @tg_menu.owner_reply
        def handle(text, ctx):
            return None
        self.assertIsNone(handle("unrelated", {}))

    def test_text_only_transport_does_not_receive_an_unused_keyboard_argument(self):
        sent = []
        @tg_menu.owner_reply
        def handle(text, ctx):
            ctx["send_text"]("Pack ready ga undi.")
            return {"handled": True}
        self.assertEqual(handle("/kit", {"send_text": sent.append}), {"handled": True})
        self.assert_page(sent[0])

    def test_compact_menu_includes_all_commands_in_one_page(self):
        reply = tg_menu.handle("/menu", {})["text"]
        self.assert_page(reply)
        for entry in tg_menu.collect_all_commands():
            self.assertIn(entry["cmd"], reply)

    def test_unknown_command_next_opens_the_store_setup(self):
        text=tg_menu.handle('/missing_cxg_command',{})['text']
        self.assertIn('/storekit',text.splitlines()[-1])
        for command in ('/storekit','/selltest start','/today'):
            self.assertIn(command,text)
        self.assertNotIn('/missing_cxg_command',text.splitlines()[-1])

    def test_owned_word_alias_keeps_its_command_next_action(self):
        import tg_files
        for word,command in (('carousel','/carousel'),('landing page','/landing'),('reel','/reel')):
            with self.subTest(word=word):
                alias=tg_files.handle(word,{})['text']
                direct=tg_files.handle(command,{})['text']
                self.assertEqual(alias,direct)
                self.assert_page(alias)

    def test_compact_currency_is_hidden_and_large_counts_stay_complete(self):
        text='Planner '+str(12)+'EUR; CAD'+str(12)+'; '+str(12)+' dollars; rupees '+str(12)
        self.assertEqual(tg_menu.owner_reply_pages(text)[0].count('[amount]'),4)
        digits='7'*5000
        pages=tg_menu.owner_reply_pages('Count: '+digits)
        self.assertEqual(''.join(line for page in pages for line in page.splitlines()[:-1]),'Count: '+digits)


if __name__ == "__main__":
    unittest.main()
