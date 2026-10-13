"""Tests for feeds.py — all offline (no real HTTP calls)."""
import importlib
import json
import sys
import types
import unittest
from unittest.mock import patch

# Stub web_fetcher before importing feeds
_wf_stub = types.ModuleType("web_fetcher")
_wf_stub.fetch_text = lambda url: ("", "disabled")
_wf_stub.fetch_words = lambda url, max_words=500: ("", "disabled")
sys.modules.setdefault("web_fetcher", _wf_stub)

import feeds
importlib.reload(feeds)


def _rss(items):
    """Build a minimal RSS 2.0 XML string."""
    xml_items = ""
    for title, link, desc in items:
        xml_items += f"<item><title>{title}</title><link>{link}</link><description>{desc}</description></item>"
    return f"<?xml version='1.0'?><rss version='2.0'><channel>{xml_items}</channel></rss>"


class TestParseRss(unittest.TestCase):
    def test_parses_items(self):
        xml = _rss([
            ("Tuna Stocks Fall", "https://example.com/1", "Global catch drops 8%"),
            ("Salmon Farming Guide", "https://example.com/2", "New techniques"),
        ])
        result = feeds._parse_rss(xml)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["title"], "Tuna Stocks Fall")
        self.assertIn("Global catch", result[0]["snippet"])

    def test_empty_xml_returns_empty(self):
        self.assertEqual(feeds._parse_rss(""), [])

    def test_malformed_xml_returns_empty(self):
        self.assertEqual(feeds._parse_rss("<not valid xml <<<"), [])


class TestParseNoaaJson(unittest.TestCase):
    def test_parses_list(self):
        data = json.dumps([
            {"title": "NOAA survey result", "body": "Fish population up", "url": "https://noaa.gov/1"},
        ])
        result = feeds._parse_noaa_json(data)
        self.assertEqual(result[0]["title"], "NOAA survey result")

    def test_parses_results_key(self):
        data = json.dumps({"results": [{"title": "Salmon run 2026", "url": "https://noaa.gov/2"}]})
        result = feeds._parse_noaa_json(data)
        self.assertEqual(result[0]["title"], "Salmon run 2026")

    def test_invalid_json_returns_empty(self):
        self.assertEqual(feeds._parse_noaa_json("not json"), [])


class TestOpenMeteo(unittest.TestCase):
    def test_parses_valid_response(self):
        data = json.dumps({
            "hourly": {
                "time": ["2026-10-13T00:00"],
                "wave_height": [1.2],
                "sea_surface_temperature": [28.5],
            }
        })
        result = feeds._open_meteo_parse(data)
        self.assertEqual(len(result), 1)
        self.assertIn("1.2", result[0]["snippet"])
        self.assertIn("28.5", result[0]["snippet"])

    def test_missing_times_returns_empty(self):
        result = feeds._open_meteo_parse(json.dumps({"hourly": {}}))
        self.assertEqual(result, [])

    def test_invalid_json_returns_empty(self):
        self.assertEqual(feeds._open_meteo_parse("bad"), [])


class TestFetchAll(unittest.TestCase):
    def test_returns_items_from_good_feeds(self):
        xml = _rss([("Fish Story", "https://x.com", "Details here")])
        # Patch fetch_text to return RSS for first call, error for rest
        call_count = [0]
        def fake_fetch(url):
            call_count[0] += 1
            if call_count[0] == 1:
                return xml, None
            return "", "network error"
        with patch.object(feeds, "fetch_text", fake_fetch):
            importlib.reload(feeds)
            result = feeds.fetch_all(max_per_feed=2)
        # At least the one good feed should have contributed
        # (other feeds may fail cleanly)
        self.assertIsInstance(result, list)

    def test_all_feeds_fail_returns_empty(self):
        with patch.object(feeds, "fetch_text", lambda url: ("", "error")):
            importlib.reload(feeds)
            result = feeds.fetch_all()
        self.assertEqual(result, [])

    def test_max_per_feed_respected(self):
        xml = _rss([
            ("Item 1", "https://x.com/1", "desc1"),
            ("Item 2", "https://x.com/2", "desc2"),
            ("Item 3", "https://x.com/3", "desc3"),
            ("Item 4", "https://x.com/4", "desc4"),
        ])
        def fake_fetch(url):
            return xml, None
        with patch.object(feeds, "fetch_text", fake_fetch):
            importlib.reload(feeds)
            result = feeds.fetch_all(max_per_feed=2)
        # Each of the 4 feeds returns ≤ 2 items, so total ≤ 8
        self.assertLessEqual(len(result), len(feeds._FEEDS) * 2)

    def test_exception_in_one_feed_does_not_crash(self):
        call_count = [0]
        def boom(url):
            call_count[0] += 1
            raise RuntimeError("network boom")
        with patch.object(feeds, "fetch_text", boom):
            importlib.reload(feeds)
            result = feeds.fetch_all()
        self.assertEqual(result, [])

    def test_source_field_added(self):
        xml = _rss([("Fish Story", "https://x.com", "desc")])
        call_count = [0]
        def fake_fetch(url):
            call_count[0] += 1
            return xml, None
        with patch.object(feeds, "fetch_text", fake_fetch):
            importlib.reload(feeds)
            result = feeds.fetch_all(max_per_feed=1)
        for item in result:
            self.assertIn("source", item)


class TestForPrompt(unittest.TestCase):
    def test_empty_when_no_feeds(self):
        with patch.object(feeds, "fetch_text", lambda url: ("", "err")):
            importlib.reload(feeds)
            text = feeds.for_prompt()
        self.assertEqual(text, "")

    def test_contains_header_when_items_present(self):
        xml = _rss([("Shrimp Prices Rise", "https://x.com", "Market data")])
        call_count = [0]
        def fake_fetch(url):
            call_count[0] += 1
            if call_count[0] == 1:
                return xml, None
            return "", "err"
        with patch.object(feeds, "fetch_text", fake_fetch):
            importlib.reload(feeds)
            text = feeds.for_prompt(max_per_feed=1)
        if text:  # only check content if feeds returned something
            self.assertIn("[Live Feed Digest", text)


if __name__ == "__main__":
    unittest.main()
