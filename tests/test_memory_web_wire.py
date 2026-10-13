"""Tests for agent_memory + web_fetcher wiring in workforce step functions.

Strategy: patch workforce's lazy imports (agent_memory, web_fetcher) at the
point workforce actually imports them — using patch() on the built-in __import__
is fragile, so we patch the attributes on the already-imported workforce module
directly after giving it a no-op version of each dependency.
"""
import importlib
import random
import sys
import types
import unittest
from unittest.mock import patch, MagicMock


# ---------- Ensure real modules load cleanly before we touch sys.modules ----------
# Import the real modules first so other tests aren't affected
import guardrails  # real
import improver    # real


# ---------- Build lightweight in-memory stubs for the two NEW deps ----------
def _fresh_agent_memory_stub():
    m = types.ModuleType("agent_memory")
    m.facts_for_prompt = lambda tag, db_path=None: ""
    m.add_fact = lambda agent, tag, fact, confidence=1.0, db_path=None: 1
    return m


def _fresh_web_fetcher_stub():
    m = types.ModuleType("web_fetcher")
    m.fetch_words = lambda url, max_words=500: ("", "disabled in test")
    return m


# ---------- helpers ----------
def _ctx(llm=None, db_path=None):
    return {
        "db_path": db_path,
        "llm": llm or (lambda p: "dummy LLM output"),
        "rng": random.Random(42),
    }


def _task(kind, payload):
    return {"kind": kind, "payload": payload, "id": 1}


def _long_good_text():
    return (
        "Get started today with our free salmon farming calculator. "
        "It helps first-time fish farmers plan feed costs and pond size. "
        "Try it now and subscribe for updates when new tools launch. "
    ) * 10


# ==========================================================================
class TestStepResearch(unittest.TestCase):
    """step_research: memory + web_fetcher injection."""

    def setUp(self):
        self._am = _fresh_agent_memory_stub()
        self._wf = _fresh_web_fetcher_stub()
        self._patches = [
            patch.dict(sys.modules, {"agent_memory": self._am, "web_fetcher": self._wf}),
        ]
        for p in self._patches:
            p.start()
        # Reload workforce so it picks up the patched sys.modules on lazy import
        import workforce as wf
        importlib.reload(wf)
        self._workforce = wf

    def tearDown(self):
        for p in self._patches:
            p.stop()
        # Reload workforce back to its real state so other test modules see real workforce
        import workforce as wf
        importlib.reload(wf)

    def _run(self, mem="", web=("", "no web"), llm=None):
        self._am.facts_for_prompt = lambda tag, db_path=None: mem
        self._wf.fetch_words = lambda url, max_words=500: web
        captured = []
        def recording_llm(prompt):
            captured.append(prompt)
            return "research result"
        result = self._workforce.step_research(
            _task("research", {"topic": "salmon farming tool"}),
            _ctx(llm or recording_llm),
        )
        return result, captured

    def test_returns_text_and_next_draft(self):
        result, _ = self._run()
        self.assertEqual(result["text"], "research result")
        self.assertEqual(len(result["next"]), 1)
        self.assertEqual(result["next"][0]["kind"], "draft")

    def test_memory_injected_when_present(self):
        _, captured = self._run(mem="[Memory: research]\n- fact1\n- fact2")
        self.assertIn("[Memory: research]", captured[0])

    def test_no_memory_no_injection(self):
        _, captured = self._run(mem="")
        self.assertNotIn("[Memory:", captured[0])

    def test_web_context_injected_when_available(self):
        _, captured = self._run(web=("web snippet about fish farming tools", None))
        self.assertIn("web snippet about fish farming tools", captured[0])

    def test_web_error_still_works(self):
        _, captured = self._run(web=("", "timeout"))
        self.assertNotIn("Web context", captured[0])

    def test_fallback_when_llm_fails(self):
        result, _ = self._run(llm=lambda p: None)
        self.assertEqual(result["mode"], "fallback")
        self.assertIn("salmon farming tool", result["text"])

    def test_memory_exception_does_not_crash(self):
        def broken(tag, db_path=None): raise RuntimeError("broken")
        self._am.facts_for_prompt = broken
        result, _ = self._run()
        self.assertIn("text", result)

    def test_web_fetcher_exception_does_not_crash(self):
        def broken(url, max_words=500): raise RuntimeError("broken")
        self._wf.fetch_words = broken
        result, _ = self._run()
        self.assertIn("text", result)


# ==========================================================================
class TestStepDraft(unittest.TestCase):
    """step_draft: memory injection into draft prompt."""

    def setUp(self):
        self._am = _fresh_agent_memory_stub()
        self._patches = [
            patch.dict(sys.modules, {"agent_memory": self._am,
                                      "web_fetcher": _fresh_web_fetcher_stub()}),
        ]
        for p in self._patches:
            p.start()
        import workforce as wf
        importlib.reload(wf)
        self._workforce = wf

    def tearDown(self):
        for p in self._patches:
            p.stop()
        import workforce as wf
        importlib.reload(wf)

    def _run(self, mem=""):
        self._am.facts_for_prompt = lambda tag, db_path=None: mem
        captured = []
        def recording_llm(prompt):
            captured.append(prompt)
            return "draft content " * 20
        payload = {"topic": "salmon farming tool", "research": "key research notes"}
        result = self._workforce.step_draft(_task("draft", payload), _ctx(recording_llm))
        return result, captured

    def test_returns_approval_id_and_next_review(self):
        result, _ = self._run()
        self.assertIn("approval_id", result)
        self.assertEqual(result["next"][0]["kind"], "review")

    def test_memory_injected_into_draft_prompt(self):
        _, captured = self._run(mem="[Memory: approved]\n- keep it short")
        self.assertIn("[Memory: approved]", captured[0])

    def test_research_notes_still_in_prompt(self):
        _, captured = self._run()
        self.assertIn("key research notes", captured[0])

    def test_memory_exception_does_not_crash(self):
        def boom(tag, db_path=None): raise RuntimeError("boom")
        self._am.facts_for_prompt = boom
        payload = {"topic": "test", "research": "notes"}
        result = self._workforce.step_draft(_task("draft", payload), _ctx())
        self.assertIn("approval_id", result)


# ==========================================================================
class TestStepReview(unittest.TestCase):
    """step_review: high-score → memory fact stored; low-score → nothing stored."""

    def setUp(self):
        self._stored = []
        self._am = _fresh_agent_memory_stub()
        self._am.add_fact = lambda agent, tag, fact, confidence=1.0, db_path=None:             self._stored.append({"agent": agent, "tag": tag, "fact": fact, "confidence": confidence})
        self._patches = [
            patch.dict(sys.modules, {"agent_memory": self._am,
                                      "web_fetcher": _fresh_web_fetcher_stub()}),
        ]
        for p in self._patches:
            p.start()
        import workforce as wf
        importlib.reload(wf)
        self._workforce = wf

    def tearDown(self):
        for p in self._patches:
            p.stop()
        import workforce as wf
        importlib.reload(wf)

    def test_high_score_stores_memory_fact(self):
        with patch("guardrails.get_approval", return_value={"payload": _long_good_text()}):
            self._workforce.step_review(
                _task("review", {"approval_id": 1, "version_id": 1}), _ctx()
            )
        high = [s for s in self._stored if s["confidence"] >= 0.70]
        self.assertTrue(len(high) >= 1)

    def test_low_score_does_not_store_fact(self):
        with patch("guardrails.get_approval", return_value={"payload": "too short"}):
            self._workforce.step_review(
                _task("review", {"approval_id": 1, "version_id": 1}), _ctx()
            )
        self.assertEqual(len(self._stored), 0)

    def test_add_fact_exception_does_not_crash(self):
        def boom(*a, **kw): raise RuntimeError("storage broken")
        self._am.add_fact = boom
        with patch("guardrails.get_approval", return_value={"payload": _long_good_text()}):
            result = self._workforce.step_review(
                _task("review", {"approval_id": 1, "version_id": 1}), _ctx()
            )
        self.assertIn("score", result)


if __name__ == "__main__":
    unittest.main()
