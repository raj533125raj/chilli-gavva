"""Lead fix (2026-11-21): /health always tells the owner the next step.

GitHub runs were red from 18 Nov: on the real robot database the health card had yellow/red lines
and no next-step words, and tests/test_ocg_all_commands_real_packs requires a next step in every reply.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import health_check  # noqa: E402
import tg_health  # noqa: E402


class HealthNextStep(unittest.TestCase):
    def check(self, card):
        with mock.patch.object(health_check, "get_health_summary", return_value=card):
            out = tg_health.handle_health("/health", {"db_path": None})
        self.assertIn("Next: /logs", out["text"])
        self.assertTrue(out["text"].startswith(card))

    def test_red_card_without_hint_words(self):
        self.check("🏥 Chilli Gavva Robot Health Card\n\n🟡 Door bell: unknown\n🔴 Disk: full")

    def test_green_card(self):
        self.check("🏥 Chilli Gavva Robot Health Card\n\n🟢 DB opens")


if __name__ == "__main__":
    unittest.main()
