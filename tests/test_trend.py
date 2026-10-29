"""Unit tests for trend.py (TREND agent)."""

import json
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import export_status
import guardrails
import notify
import trend


FAKE_GITHUB_JSON = json.dumps({
    "items": [
        {
            "full_name": "chilli/agent-tooling-framework",
            "html_url": "https://github.com/chilli/agent-tooling-framework",
            "description": "Awesome agent tooling framework for digital products."
        },
        {
            "full_name": "chilli/llm-workflow-automation",
            "html_url": "https://github.com/chilli/llm-workflow-automation",
            "description": "Multi-agent workflow automation system."
        }
    ]
})

FAKE_HF_JSON = json.dumps([
    {
        "id": "deepseek-ai/DeepSeek-V3",
        "modelId": "deepseek-ai/DeepSeek-V3"
    },
    {
        "id": "Qwen/Qwen2.5-Coder-32B",
        "modelId": "Qwen/Qwen2.5-Coder-32B"
    }
])

FAKE_RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Tech Blog</title>
    <item>
      <title>Building Digital Product Workflows with LLMs</title>
      <link>https://example.com/blog/building-digital-products</link>
      <description>Guide on creating Notion templates and printable planners using AI tools.</description>
    </item>
  </channel>
</rss>
"""


def fake_fetcher(url):
    if "api.github.com" in url:
        return FAKE_GITHUB_JSON, None
    if "huggingface.co/api" in url:
        return FAKE_HF_JSON, None
    if "rss" in url or "feed" in url or "xml" in url or "example.com" in url:
        return FAKE_RSS_XML, None
    return FAKE_RSS_XML, None


class TrendAgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test_trend.db")
        self.output_status = os.path.join(self.tmp.name, "status.json")
        conn = trend.connect(self.db_path)
        conn.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_offline_fetchers_parse_fake_responses(self):
        gh_items = trend.fetch_github_trending(fetcher=fake_fetcher)
        self.assertEqual(len(gh_items), 2)
        self.assertEqual(gh_items[0]["title"], "chilli/agent-tooling-framework")
        self.assertEqual(gh_items[0]["source"], "github")

        hf_items = trend.fetch_huggingface_trending(fetcher=fake_fetcher)
        self.assertEqual(len(hf_items), 2)
        self.assertEqual(hf_items[0]["title"], "HF Model: deepseek-ai/DeepSeek-V3")
        self.assertEqual(hf_items[0]["source"], "huggingface")

        rss_items = trend.fetch_rss_feeds(fetcher=fake_fetcher)
        self.assertGreaterEqual(len(rss_items), 1)
        self.assertIn("Digital Product", rss_items[0]["title"])

    def test_dedupe_items(self):
        items = [
            {"title": "Agent Tool", "url": "https://github.com/agent/tool"},
            {"title": "Agent Tool", "url": "https://github.com/agent/tool"},  # Duplicate
            {"title": "Another Tool", "url": "https://github.com/agent/another"},
        ]
        unique = trend.dedupe_items(items)
        self.assertEqual(len(unique), 2)

    def test_rule_based_scoring_fallback(self):
        item = {
            "title": "Autonomous Multi-Agent Framework for Digital Products",
            "snippet": "Build automated Notion templates and planners.",
            "url": "https://github.com/test/agent",
            "source": "github"
        }
        scored = trend.rule_based_score(item)
        self.assertGreaterEqual(scored["score"], 80)
        self.assertTrue(len(scored["why"]) > 5)

    def test_run_trend_stores_top_10_and_triggers_telegram(self):
        with mock.patch("notify._telegram_configured", return_value=(True, "token123", "chat456")), \
             mock.patch("telegram_inbox.send_text", return_value=True) as mock_send:
            saved = trend.run_trend(db_path=self.db_path, fetcher=fake_fetcher)
            self.assertEqual(len(saved), 10)

            conn = trend.connect(self.db_path)
            rows = conn.execute("SELECT id, title, score, status FROM trend_items").fetchall()
            conn.close()
            self.assertEqual(len(rows), 10)

            mock_send.assert_called_once()
            args, kwargs = mock_send.call_args
            self.assertEqual(args[0], "token123")
            self.assertEqual(args[1], "chat456")
            self.assertIn("TREND Digest", args[2])
            self.assertEqual(len(kwargs["buttons"]), 5)

    def test_deep_dive_callback_returns_summary(self):
        # Run trend to populate database
        trend.run_trend(db_path=self.db_path, fetcher=fake_fetcher)

        with mock.patch("telegram_inbox.send_text") as mock_send:
            reply = trend.handle_callback("token", "chat", "cb:tr:d:1", db_path=self.db_path)
            self.assertIsNotNone(reply)
            self.assertIn("Deep Dive on #1", reply)
            mock_send.assert_called()

    def test_propose_callback_creates_approval_card(self):
        trend.run_trend(db_path=self.db_path, fetcher=fake_fetcher)

        with mock.patch("telegram_inbox.send_text") as mock_send:
            reply = trend.handle_callback("token", "chat", "cb:tr:p:1", db_path=self.db_path)
            self.assertIn("Proposed #1", reply)

            # Check that item status is proposed
            conn = trend.connect(self.db_path)
            row = conn.execute("SELECT status, approval_id FROM trend_items WHERE id = 1").fetchone()
            conn.close()

            self.assertEqual(row[0], "proposed")
            approval_id = row[1]
            self.assertIsNotNone(approval_id)

            # Check approval record in queue
            app = guardrails.get_approval(approval_id, self.db_path)
            self.assertEqual(app["kind"], "trend_proposal")
            self.assertIn("[Trend Proposal]", app["title"])

    def test_sync_approved_trends(self):
        trend.run_trend(db_path=self.db_path, fetcher=fake_fetcher)
        res = trend.propose_trend_item(1, db_path=self.db_path)
        approval_id = res["approval_id"]

        # Approve the proposal
        guardrails.decide(approval_id, True, self.db_path)
        synced = trend.sync_approved_trends(self.db_path)
        self.assertEqual(synced, 1)

        conn = trend.connect(self.db_path)
        row = conn.execute("SELECT status FROM trend_items WHERE id = 1").fetchone()
        conn.close()
        self.assertEqual(row[0], "approved")

    def test_maybe_run_runs_once_per_day(self):
        res1 = trend.maybe_run(db_path=self.db_path, fetcher=fake_fetcher)
        self.assertTrue(res1["ran"])

        res2 = trend.maybe_run(db_path=self.db_path, fetcher=fake_fetcher)
        self.assertFalse(res2["ran"])
        self.assertEqual(res2["reason"], "already ran today")

    def test_get_trend_items_count_and_public_export_privacy(self):
        trend.run_trend(db_path=self.db_path, fetcher=fake_fetcher)
        trend.propose_trend_item(1, db_path=self.db_path)

        counts = trend.get_trend_items_count(self.db_path)
        self.assertEqual(counts["total"], 10)
        self.assertEqual(counts["proposed"], 1)

        # Export public status
        export_status.write_status(self.output_status, self.db_path)
        with open(self.output_status, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertIn("trend_items_count", data)
        self.assertEqual(data["trend_items_count"]["total"], 10)
        self.assertEqual(data["trend_items_count"]["proposed"], 1)

        # Verify no unapproved trend titles or URLs escape into public status
        serialized = json.dumps(data).lower()
        self.assertNotIn("chilli/agent-tooling-framework", serialized)
        self.assertNotIn("deepseek-v3", serialized)

    def test_no_currency_symbols_or_money_in_prompts(self):
        for forbidden in ("$", "USD", "EUR", "GBP", "rupee"):
            self.assertNotIn(forbidden, trend.SCORE_PROMPT)
            self.assertNotIn(forbidden, trend.SUMMARY_PROMPT)


if __name__ == "__main__":
    unittest.main()
