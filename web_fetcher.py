"""Simple web research fetcher using stdlib only (urllib, html.parser).

Agents use this to fetch public web pages for research — no third-party packages.
Returns plain text stripped of HTML tags. Respects robots.txt is NOT checked
(this is a research tool, not a crawler — use sparingly, one URL at a time).

Rate-limited: max 1 request per 3 seconds via the module-level throttle.
Timeout: 10 seconds per request.

CLI:
  python3 web_fetcher.py <url>          # fetch and print plain text
  python3 web_fetcher.py <url> <words>  # print first N words only
"""

import secret_guard
import html
import html.parser
import os
import re
import sys
import time
import urllib.error
import urllib.request

_LAST_FETCH = 0.0
_MIN_GAP = 3.0  # seconds between requests
_TIMEOUT = 10

USER_AGENT = "ChilliGavvaBot/1.0 (research; +https://chilligavva.com)"


class _Stripper(html.parser.HTMLParser):
    """Extract visible text from HTML."""
    _SKIP = {"script", "style", "noscript", "head", "nav", "footer", "aside"}

    def __init__(self):
        super().__init__()
        self._parts = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in self._SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag.lower() in self._SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if not self._skip_depth:
            self._parts.append(data)

    def get_text(self) -> str:
        raw = " ".join(self._parts)
        # collapse whitespace
        raw = re.sub(r"\s+", " ", raw).strip()
        return html.unescape(raw)


def fetch_text(url: str, max_bytes: int = 200_000) -> tuple[str, str | None]:
    """Fetch URL and return (plain_text, error_or_None).

    Returns at most `max_bytes` of response body to avoid memory blowup.
    """
    global _LAST_FETCH
    elapsed = time.monotonic() - _LAST_FETCH
    if elapsed < _MIN_GAP:
        time.sleep(_MIN_GAP - elapsed)

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            _LAST_FETCH = time.monotonic()
            content_type = resp.headers.get("Content-Type", "")
            if "html" not in content_type and "text" not in content_type:
                return "", f"Non-text content-type: {content_type}"
            raw = resp.read(max_bytes).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return "", secret_guard.scrub_text(f"HTTP {e.code}: {e.reason}")
    except urllib.error.URLError as e:
        return "", secret_guard.scrub_text(f"URL error: {e.reason}")
    except Exception as e:
        return "", secret_guard.scrub_text(f"Fetch error: {e}")

    stripper = _Stripper()
    try:
        stripper.feed(raw)
        text = stripper.get_text()
    except Exception as e:
        return "", secret_guard.scrub_text(f"Parse error: {e}")

    return text, None


def fetch_words(url: str, max_words: int = 500) -> tuple[str, str | None]:
    """Fetch URL and return first `max_words` words of plain text."""
    text, err = fetch_text(url)
    if err:
        return "", err
    words = text.split()
    snippet = " ".join(words[:max_words])
    return snippet, None


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 web_fetcher.py <url> [max_words]")
        sys.exit(1)
    url = sys.argv[1]
    max_w = int(sys.argv[2]) if len(sys.argv) >= 3 else 300
    text, err = fetch_words(url, max_words=max_w)
    if err:
        print(f"Error: {err}", file=sys.stderr)
        sys.exit(1)
    print(text)
