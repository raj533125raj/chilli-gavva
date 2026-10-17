"""Free public API feeds relevant to Chilli Gavva content (Indian food, spices, cooking).

All endpoints are truly free — no key required unless noted.
Used by the Research Worker to pull real-world data into article drafts.

Usage:
    import feeds
    items = feeds.fetch_all(max_per_feed=3)
    # Returns list of {"source": str, "title": str, "snippet": str, "url": str}

    text = feeds.for_prompt(max_per_feed=2)
    # Returns a formatted string ready to inject into an LLM prompt.

Prompt-injection guard (roadmap #55), OFF by default (CHILLI_GUARD_ENABLED=1 turns it on). When on, each
item's title and snippet is scanned with injection_guard; an item with a HIGH-severity finding is dropped
and a "dropped: <source>#<hash> ..." line is recorded instead. That line never contains the item's title,
snippet or URL text, only the source name and a short sha256 hash of the URL. When off, for_prompt()
output is exactly as before.
"""

import hashlib
import json
import os
import xml.etree.ElementTree as ET

import injection_guard
from web_fetcher import fetch_text

GUARD_ENABLED = os.environ.get("CHILLI_GUARD_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


def _high_guard_reasons(text):
    """High-severity injection pattern names found in ``text`` (never the match text)."""
    if not text:
        return []
    try:
        findings = injection_guard.scan_text(text)
    except Exception:
        return []
    reasons = []
    for finding in findings:
        if finding.severity == "high" and finding.pattern not in reasons:
            reasons.append(finding.pattern)
    return reasons


def _dropped_label(item):
    """A non-sensitive identifier for a dropped item: source name + a short sha256 hash of its URL.
    The raw URL is untrusted external text (it can carry a token or an injection phrase), so it is
    never echoed into the digest."""
    source = (item.get("source") or "unknown").strip() or "unknown"
    digest = hashlib.sha256((item.get("url") or "").encode("utf-8", "surrogatepass")).hexdigest()[:8]
    return f"{source}#{digest}"

# ---------------------------------------------------------------------------
# Feed definitions — each is (name, url, parser_fn)
# parser_fn(raw_text) -> list of {"title", "snippet", "url"}
# ---------------------------------------------------------------------------

def _parse_rss(text: str) -> list[dict]:
    """Generic RSS/Atom parser using stdlib xml.etree."""
    items = []
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return items
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    # RSS 2.0
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link  = (item.findtext("link")  or "").strip()
        desc  = (item.findtext("description") or "").strip()[:300]
        if title:
            items.append({"title": title, "snippet": desc, "url": link})
    # Atom
    if not items:
        for entry in root.iter("{http://www.w3.org/2005/Atom}entry"):
            title = (entry.findtext("{http://www.w3.org/2005/Atom}title") or "").strip()
            link_el = entry.find("{http://www.w3.org/2005/Atom}link")
            link  = (link_el.get("href") if link_el is not None else "") or ""
            summ  = (entry.findtext("{http://www.w3.org/2005/Atom}summary") or "").strip()[:300]
            if title:
                items.append({"title": title, "snippet": summ, "url": link})
    return items


def _parse_noaa_json(text: str) -> list[dict]:
    """NOAA food/agriculture news — JSON array or object with results."""
    items = []
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return items
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = data.get("results") or data.get("items") or []
    else:
        rows = []
    for row in rows:
        title   = str(row.get("title") or row.get("name") or "").strip()
        snippet = str(row.get("body") or row.get("description") or row.get("teaser") or "").strip()[:300]
        url     = str(row.get("url") or row.get("link") or "").strip()
        if title:
            items.append({"title": title, "snippet": snippet, "url": url})
    return items


_FEEDS = [
    # FAO Fisheries & Aquaculture news RSS (UN, no key)
    (
        "Times Food",
        "https://recipes.timesofindia.com/rss/topstories",
        _parse_rss,
    ),
    # NOAA Fisheries news RSS
    (
        "Archana Kitchen",
        "https://www.archanaskitchen.com/feeds/posts/default",
        _parse_rss,
    ),
    # Global Fishing Watch blog RSS (open data platform)
    (
        "Spice Up The Curry",
        "https://www.spiceupthecurry.com/feed/",
        _parse_rss,
    ),
    # Open Meteo — free weather API (no key, JSON)
    # Returns current marine conditions — useful for aquaculture content
    (
        "Open-Meteo Marine",
        (
            "https://marine-api.open-meteo.com/v1/marine"
            "?latitude=13.08&longitude=80.27"   # Bay of Bengal near Chennai
            "&hourly=wave_height,sea_surface_temperature&forecast_days=1"
        ),
        lambda text: _open_meteo_parse(text),
    ),
    # REST Countries — not food but useful for market/trade angle
    # (kept as a lightweight connectivity check)
]


def _open_meteo_parse(text: str) -> list[dict]:
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return []
    hourly = data.get("hourly", {})
    times   = hourly.get("time", [])
    waves   = hourly.get("wave_height", [])
    sst     = hourly.get("sea_surface_temperature", [])
    if not times:
        return []
    # Report just the first available entry
    t = times[0] if times else "?"
    w = waves[0] if waves else "?"
    s = sst[0] if sst else "?"
    snippet = f"Wave height {w}m, sea surface temp {s}°C at {t} (Bay of Bengal)"
    return [{"title": "Live Marine Conditions — Bay of Bengal", "snippet": snippet, "url": "https://open-meteo.com"}]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_all(max_per_feed: int = 3) -> list[dict]:
    """Fetch all feeds; return combined list of items (best-effort, never raises)."""
    results = []
    for name, url, parser in _FEEDS:
        try:
            raw, err = fetch_text(url)
            if err or not raw:
                continue
            items = parser(raw)
            for item in items[:max_per_feed]:
                item["source"] = name
                results.append(item)
        except Exception:
            continue
    return results


def for_prompt(max_per_feed: int = 2) -> str:
    """Return a compact feed digest ready to paste into an LLM prompt.

    Returns empty string if all feeds fail (so callers can skip injection).
    When GUARD_ENABLED is on, items whose title or snippet trips a HIGH-severity injection pattern are
    dropped and recorded as "dropped: <source>#<hash> ..." lines (never the title, snippet or URL text).
    """
    items = fetch_all(max_per_feed=max_per_feed)
    if not items:
        return ""
    kept, dropped = [], []
    for item in items:
        if GUARD_ENABLED:
            reasons = _high_guard_reasons(item.get("title") or "")
            reasons += [r for r in _high_guard_reasons(item.get("snippet") or "") if r not in reasons]
            if reasons:
                dropped.append(f"dropped: {_dropped_label(item)} reason: high-severity "
                               f"prompt-injection pattern(s): {', '.join(reasons)}")
                continue
        kept.append(item)
    lines = ["[Live Feed Digest — use as background context only]"]
    for item in kept:
        lines.append(f"• [{item['source']}] {item['title']}")
        if item.get("snippet"):
            lines.append(f"  {item['snippet'][:150]}")
    lines.extend(dropped)
    return "\n".join(lines)
