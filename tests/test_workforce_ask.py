"""Tests for workforce._ask size hints (offline)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import workforce  # noqa: E402


class AskTests(unittest.TestCase):
    def test_passes_size_when_supported(self):
        seen = {}

        def llm(prompt, size=None):
            seen["size"] = size
            return "ok"
        self.assertEqual(workforce._ask({"llm": llm}, "p", size="large"), "ok")
        self.assertEqual(seen["size"], "large")

    def test_old_style_callable_still_works(self):
        def llm(prompt):
            return "old"
        self.assertEqual(workforce._ask({"llm": llm}, "p", size="large"), "old")

    def test_kwargs_callable(self):
        def llm(prompt, **kw):
            return kw.get("size")
        self.assertEqual(workforce._ask({"llm": llm}, "p", size="small"), "small")

    def test_no_size_given(self):
        def llm(prompt, size=None):
            return size or "none"
        self.assertEqual(workforce._ask({"llm": llm}, "p"), "none")


if __name__ == "__main__":
    unittest.main()
