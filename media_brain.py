"""media_brain.py — Media & Content Intelligence for Chilli Gavva Telegram Inbox.

Processes photos, voice notes/audio, links/URLs, and PDFs/text files sent to the Telegram bot
by the owner. Analyzes content safely, summarizes business value, and presents action buttons:
  - Save to memory
  - Make it a task
  - Discuss more

Safety:
  - Owner chat only.
  - Size cap: 2 MB limit for downloads and text extractions.
  - Timeouts on all network calls.
  - Prompt-injection guard: all extracted/fetched text is untrusted data.
  - Secrets and currency scrubbed.
  - Rule-based fallbacks when AI is unavailable.
  - Failures produce friendly messages; never crash.
  - Public status exports only item count.
"""

import datetime
import html
import html.parser
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import agent_memory
import cubes
import guardrails
import injection_guard
import llm_router
import notify
import rest_window

MAX_SIZE_BYTES = 2 * 1024 * 1024  # 2 MB limit
NETWORK_TIMEOUT = 15  # seconds

# Currency regex to scrub currency amounts
CURRENCY_PATTERN = re.compile(
    r"(\$|₹|€|£)\s*\d+(?:\.\d+)?|\b\d+(?:\.\d+)?\s*(?:USD|INR|EUR|GBP|dollars|rupees)\b",
    re.IGNORECASE
)


def is_paused(db_path=None) -> bool:
    if guardrails.is_killed() or rest_window.in_rest_window():
        return True
    return not cubes.is_kind_enabled("research", db_path=db_path)


def _connect(db_path=None):
    conn = guardrails.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS media_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            summary TEXT,
            key_points TEXT,
            chilli_gavva_value TEXT,
            raw_text TEXT,
            ts TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


def get_media_items_count(db_path=None) -> dict:
    """Return total count of media items processed (safe for public exports)."""
    if db_path and not os.path.exists(db_path):
        return {"total": 0}
    try:
        conn = _connect(db_path)
        row = conn.execute("SELECT COUNT(*) FROM media_items").fetchone()
        conn.close()
        return {"total": row[0] if row else 0}
    except Exception:
        return {"total": 0}


def scrub_secrets_and_money(text: str) -> str:
    if not text:
        return ""
    text = llm_router.scrub(text)
    text = CURRENCY_PATTERN.sub("[hidden]", text)
    return text


def _sanitize_untrusted_text(text: str) -> str:
    """Sanitize content to neutralize prompt injections before injecting into prompts."""
    if not text:
        return ""
    # Truncate if exceedingly long
    text = text[:10000]
    # Scan for prompt injections
    findings = injection_guard.scan_text(text)
    if any(f.severity == "high" for f in findings):
        # Neutralize high severity injections by prefixing warning
        text = "[UNTRUSTED CONTENT - PROMPT INJECTION PATTERNS DETECTED AND NEUTRALIZED]\n" + re.sub(r"ignore", "i_g_n_o_r_e", text, flags=re.IGNORECASE)
    return text


# ---- Telegram Bot API File Downloader ----

def download_telegram_file(token: str, file_id: str, max_bytes: int = MAX_SIZE_BYTES) -> tuple[bytes | None, str | None]:
    """Download a file from Telegram using Bot API (getFile -> download).

    Returns (bytes, mime_type_or_None) or (None, error_str).
    """
    url = f"https://api.telegram.org/bot{token}/getFile"
    params = json.dumps({"file_id": file_id}).encode("utf-8")
    req = urllib.request.Request(url, data=params, headers={"Content-Type": "application/json"})

    try:
        with urllib.request.urlopen(req, timeout=NETWORK_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if not data.get("ok"):
                return None, llm_router.scrub(f"Telegram getFile error: {data.get('description', 'failed')}")
            file_path = data.get("result", {}).get("file_path")
            file_size = data.get("result", {}).get("file_size", 0)
            if file_size > max_bytes:
                return None, f"File exceeds size cap (max {max_bytes // (1024*1024)} MB)"
            if not file_path:
                return None, "File path missing in getFile response"
    except Exception as exc:
        return None, f"getFile failed: {type(exc).__name__}"

    download_url = f"https://api.telegram.org/file/bot{token}/{file_path}"
    try:
        d_req = urllib.request.Request(download_url, headers={"User-Agent": "ChilliGavvaBot/1.0"})
        with urllib.request.urlopen(d_req, timeout=NETWORK_TIMEOUT) as resp:
            content = resp.read(max_bytes + 1)
            if len(content) > max_bytes:
                return None, f"File content exceeds size cap of {max_bytes // (1024*1024)} MB"
            return content, None
    except Exception as exc:
        return None, f"Download failed: {type(exc).__name__}"


# ---- Handlers for Media Types ----

def process_photo(token: str, photo_list: list, caption: str = "", db_path=None) -> tuple[str, list, int]:
    """Download photo (largest resolution), analyze using Gemini vision via llm_router."""
    if is_paused(db_path):
        return "Media Intelligence is currently paused (kill switch, rest window, or Research cube Off).", [], 0
    if not photo_list:
        return "No photo found in message.", [], 0
    # Pick largest photo
    best_photo = max(photo_list, key=lambda p: p.get("file_size", 0))
    file_id = best_photo.get("file_id")

    img_bytes, err = download_telegram_file(token, file_id)
    if err or not img_bytes:
        summary = f"Photo received. (Download note: {err or 'file unavailable'})"
        if caption:
            summary += f" Caption: {caption}"
        key_points = ["1. Photo asset received.", "2. Download/vision provider unavailable.", "3. Saved in session context."]
        chilli_value = "Visual reference saved for Chilli Gavva digital products."
        return _format_and_save("photo", summary, key_points, chilli_value, caption or summary, db_path=db_path)

    # Analyze with Gemini vision
    prompt = "Describe and analyze this image in detail for a business owner."
    if caption:
        prompt += f" Owner note: {caption}"

    llm_res = llm_router.chat_vision(img_bytes, "image/jpeg", prompt=prompt, db_path=db_path)

    if not llm_res or "blocked_by_guard" in llm_res:
        # Friendly message fallback when no vision provider is up
        summary = f"Photo received ({len(img_bytes)} bytes)."
        if caption:
            summary += f" Caption: {caption}"
        key_points = [
            "1. Photo received safely.",
            "2. AI vision processing unavailable or offline.",
            "3. Stored for review when AI vision is online."
        ]
        chilli_value = "Can be used as mockups, design reference, or social promotional media for digital products."
        return _format_and_save("photo", summary, key_points, chilli_value, caption or summary, db_path=db_path)

    # Parse LLM response into structured sections
    return _parse_ai_analysis("photo", llm_res, raw_text=caption or "Photo analysis", db_path=db_path)


def process_audio(token: str, voice_or_audio: dict, caption: str = "", db_path=None) -> tuple[str, list, int]:
    """Download voice note or audio, transcribe and analyze using Gemini audio via llm_router."""
    if is_paused(db_path):
        return "Media Intelligence is currently paused (kill switch, rest window, or Research cube Off).", [], 0
    file_id = voice_or_audio.get("file_id")
    mime_type = voice_or_audio.get("mime_type") or "audio/ogg"

    audio_bytes, err = download_telegram_file(token, file_id)
    if err or not audio_bytes:
        summary = f"Voice note / audio received. (Download note: {err or 'file unavailable'})"
        if caption:
            summary += f" Caption: {caption}"
        key_points = ["1. Voice note received.", "2. Audio download unavailable.", "3. Stored in session context."]
        chilli_value = "Contains direct voice feedback or feature ideas from the business owner."
        return _format_and_save("audio", summary, key_points, chilli_value, caption or summary, db_path=db_path)

    prompt = (
        "Transcribe this voice note / audio accurately (it may contain English, Telugu, or a Telugu-English mix/Tenglish). "
        "Then explain the request or idea inside it and how to fulfill it."
    )
    if caption:
        prompt += f" Owner note: {caption}"

    llm_res = llm_router.chat_audio(audio_bytes, mime_type, prompt=prompt, db_path=db_path)

    if not llm_res or "blocked_by_guard" in llm_res:
        # Friendly message fallback when no audio provider is up
        summary = f"Audio message received ({voice_or_audio.get('duration', 0)}s)."
        if caption:
            summary += f" Caption: {caption}"
        key_points = [
            "1. Audio recording received.",
            "2. Audio AI transcription provider is currently offline or unavailable.",
            "3. Stored for review when AI audio is online."
        ]
        chilli_value = "Contains direct voice feedback or feature ideas from the business owner."
        return _format_and_save("audio", summary, key_points, chilli_value, caption or summary, db_path=db_path)

    return _parse_ai_analysis("audio", llm_res, raw_text=llm_res, db_path=db_path)


class _HTMLTextExtractor(html.parser.HTMLParser):
    _SKIP = {"script", "style", "noscript", "head", "nav", "footer", "aside"}

    def __init__(self):
        super().__init__()
        self._parts = []
        self._skip_depth = 0
        self.title = ""
        self.meta_desc = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        tag_l = tag.lower()
        if tag_l in self._SKIP:
            self._skip_depth += 1
        if tag_l == "title":
            self._in_title = True
        if tag_l == "meta":
            attr_dict = {k.lower(): v for k, v in attrs if k and v}
            if attr_dict.get("name") in ("description", "og:description", "twitter:description"):
                self.meta_desc = attr_dict.get("content", "")

    def handle_endtag(self, tag):
        tag_l = tag.lower()
        if tag_l in self._SKIP and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag_l == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip_depth:
            self._parts.append(data)

    def get_text(self) -> str:
        raw = " ".join(self._parts)
        return html.unescape(re.sub(r"\s+", " ", raw).strip())


def process_url(url: str, db_path=None) -> tuple[str, list, int]:
    """Fetch webpage, summarize, and evaluate revenue potential for Chilli Gavva.

    For YouTube / Instagram links: uses ONLY public oEmbed or page title/meta metadata without video downloading.
    """
    if is_paused(db_path):
        return "Media Intelligence is currently paused (kill switch, rest window, or Research cube Off).", [], 0
    domain = urllib.parse.urlparse(url).netloc.lower()

    if "youtube.com" in domain or "youtu.be" in domain:
        return _process_youtube_url(url, db_path)
    if "instagram.com" in domain:
        return _process_instagram_url(url, db_path)

    # General web page fetch
    req = urllib.request.Request(url, headers={"User-Agent": "ChilliGavvaBot/1.0 (research)"})
    try:
        with urllib.request.urlopen(req, timeout=NETWORK_TIMEOUT) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if "html" not in content_type and "text" not in content_type:
                msg = f"Skipped URL: Content-Type '{content_type}' is non-text."
                return msg, [], 0
            raw_data = resp.read(MAX_SIZE_BYTES).decode("utf-8", errors="replace")
    except Exception as exc:
        msg = f"Could not fetch URL: {type(exc).__name__}"
        return msg, [], 0

    parser = _HTMLTextExtractor()
    try:
        parser.feed(raw_data)
        title = parser.title.strip() or "Web Page"
        text = parser.get_text()[:4000]
    except Exception as exc:
        msg = f"Could not parse webpage: {type(exc).__name__}"
        return msg, [], 0

    return _analyze_link_content("link", title, text, url, db_path=db_path)


def _process_youtube_url(url: str, db_path=None) -> tuple[str, list, int]:
    """Public oEmbed metadata fetch for YouTube links (no downloading videos)."""
    oembed_url = f"https://www.youtube.com/oembed?url={urllib.parse.quote(url)}&format=json"
    title, author, text = "YouTube Video", "", ""
    try:
        req = urllib.request.Request(oembed_url, headers={"User-Agent": "ChilliGavvaBot/1.0"})
        with urllib.request.urlopen(req, timeout=NETWORK_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            title = data.get("title", "YouTube Video")
            author = data.get("author_name", "")
            text = f"Title: {title}. Channel: {author}."
    except Exception:
        text = f"YouTube Link: {url}"

    return _analyze_link_content("youtube_link", title, text, url, db_path=db_path)


def _process_instagram_url(url: str, db_path=None) -> tuple[str, list, int]:
    """Public oEmbed or meta tag metadata for Instagram links (no downloading videos/reels)."""
    title = "Instagram Post"
    text = f"Instagram Post: {url}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ChilliGavvaBot/1.0 (research)"})
        with urllib.request.urlopen(req, timeout=NETWORK_TIMEOUT) as resp:
            raw_data = resp.read(MAX_SIZE_BYTES).decode("utf-8", errors="replace")
            parser = _HTMLTextExtractor()
            parser.feed(raw_data)
            if parser.title:
                title = parser.title.strip()
            if parser.meta_desc:
                text = f"{title}. Description: {parser.meta_desc}"
    except Exception:
        pass

    return _analyze_link_content("instagram_link", title, text, url, db_path=db_path)


def _analyze_link_content(kind: str, title: str, text: str, url: str, db_path=None) -> tuple[str, list, int]:
    sanitized_text = _sanitize_untrusted_text(text)
    prompt = (
        f"You are analyzing a link shared by the Chilli Gavva business owner.\n"
        f"Title: {title}\n"
        f"URL: {url}\n"
        f"Extracted content:\n{sanitized_text}\n\n"
        f"Provide:\n"
        f"1. A short summary of what this page/link is about.\n"
        f"2. Exactly 3 key points.\n"
        f"3. 'How this helps Chilli Gavva': explain concrete ways this could help Chilli Gavva earn revenue (e.g., product idea, marketing angle, automation tool)."
    )

    llm_res = llm_router.chat(prompt, db_path=db_path, size="small")
    if not llm_res or "blocked_by_guard" in llm_res:
        summary = f"Link: {title} ({url})"
        key_points = [f"1. Source URL: {url}", f"2. Title: {title}", "3. Extracted plain text page content."]
        chilli_value = "Can inspire new digital product templates, niche topics, or traffic strategies."
        return _format_and_save(kind, summary, key_points, chilli_value, text, db_path=db_path)

    return _parse_ai_analysis(kind, llm_res, raw_text=text, db_path=db_path)


def process_document(token: str, doc: dict, db_path=None) -> tuple[str, list, int]:
    """Process PDF or plain text document: download (capped 2 MB), extract text, summarize."""
    if is_paused(db_path):
        return "Media Intelligence is currently paused (kill switch, rest window, or Research cube Off).", [], 0
    file_id = doc.get("file_id")
    file_name = doc.get("file_name", "document")

    doc_bytes, err = download_telegram_file(token, file_id)
    if err or not doc_bytes:
        msg = f"Could not download document: {err or 'empty file'}"
        return msg, [], 0

    extracted_text = ""
    if file_name.lower().endswith(".pdf"):
        extracted_text = _extract_pdf_text(doc_bytes)
    else:
        try:
            extracted_text = doc_bytes.decode("utf-8", errors="replace")
        except Exception:
            extracted_text = "Binary or unreadable document content."

    extracted_text = extracted_text[:10000]  # capped
    if not extracted_text.strip():
        msg = f"Document '{file_name}' appears to have no readable text."
        return msg, [], 0

    sanitized_text = _sanitize_untrusted_text(extracted_text)
    prompt = (
        f"Analyze this document ({file_name}) sent by the business owner:\n"
        f"{sanitized_text}\n\n"
        f"Provide:\n"
        f"1. Short summary\n"
        f"2. Exactly 3 key points\n"
        f"3. 'How this helps Chilli Gavva': explain how this document helps Chilli Gavva earn."
    )

    llm_res = llm_router.chat(prompt, db_path=db_path, size="small")
    if not llm_res or "blocked_by_guard" in llm_res:
        summary = f"Document '{file_name}' processed ({len(doc_bytes)} bytes)."
        key_points = [f"1. File: {file_name}", "2. Text extracted from document.", "3. Safe text preview available."]
        chilli_value = "Contains reference materials, specs, or product content ideas."
        return _format_and_save("document", summary, key_points, chilli_value, extracted_text, db_path=db_path)

    return _parse_ai_analysis("document", llm_res, raw_text=extracted_text, db_path=db_path)


def _extract_pdf_text(pdf_bytes: bytes) -> str:
    """Extract text from PDF bytes using pure Python latin-1 / PDF stream regex parsing (same pattern as truth_check.py)."""
    try:
        data = pdf_bytes.decode("latin-1", errors="replace")
        texts = []
        op = re.compile(r"BT /(F1|F2) ([\d.]+) Tf ([\d.]+) ([\d.]+) Td \((.*)\) Tj ET$")
        for stream in re.findall(r"stream\n(.*?)\nendstream", data, re.S):
            for line in stream.splitlines():
                m = op.match(line)
                if m:
                    raw = m.group(5).replace("\\(", "(").replace("\\)", ")").replace("\\\\", "\\")
                    text = raw.encode("latin-1", "replace").decode("cp1252", "replace")
                    texts.append(text)
        if texts:
            return "\n".join(texts)
    except Exception:
        pass
    return "PDF binary content (text stream extraction returned no text)."


# ---- Helpers for Parsing AI Output and Formatting Reply ----

def _parse_ai_analysis(kind: str, ai_text: str, raw_text: str = "", db_path=None) -> tuple[str, list, int]:
    """Parse structured sections from AI output or fallback gracefully."""
    ai_text = scrub_secrets_and_money(ai_text)

    # Attempt regex extraction
    summary_match = re.search(r"(?:summary|short summary)[:\s]*(.+?)(?=\n\n|\n\d|\nkey points|how this helps|\Z)", ai_text, re.IGNORECASE | re.DOTALL)
    summary = summary_match.group(1).strip() if summary_match else ai_text[:200].replace("\n", " ")

    # Extract 3 key points
    key_points = []
    for line in ai_text.splitlines():
        line_s = line.strip()
        if re.match(r"^[\d\*\-\•]\s*", line_s) and len(line_s) > 5:
            key_points.append(line_s)
        if len(key_points) == 3:
            break

    if len(key_points) < 3:
        key_points = [
            "1. Analyzed submitted content.",
            "2. Identified key strategic themes.",
            "3. Ready for discussion or task creation."
        ]

    value_match = re.search(r"(?:how this helps chilli gavva|helps chilli gavva|earning potential|revenue)[:\s]*(.+)", ai_text, re.IGNORECASE | re.DOTALL)
    chilli_value = value_match.group(1).strip() if value_match else "Provides high-value content or product strategy for Chilli Gavva."

    return _format_and_save(kind, summary, key_points, chilli_value, raw_text, db_path=db_path)


def _format_and_save(kind: str, summary: str, key_points: list, chilli_value: str, raw_text: str = "", db_path=None) -> tuple[str, list, int]:
    """Save item into media_items table and format Telegram reply with action buttons."""
    summary = scrub_secrets_and_money(summary)
    chilli_value = scrub_secrets_and_money(chilli_value)
    clean_key_points = [scrub_secrets_and_money(p) for p in key_points]

    conn = _connect(db_path)
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    cur = conn.execute(
        "INSERT INTO media_items (chat_id, kind, summary, key_points, chilli_gavva_value, raw_text, ts) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("owner", kind, summary, json.dumps(clean_key_points), chilli_value, llm_router.scrub(raw_text)[:5000], ts)
    )
    conn.commit()
    item_id = cur.lastrowid
    conn.close()

    lines = [
        "🧠 **Media Intelligence Analysis**",
        "",
        f"**Summary:** {summary[:300]}",
        "",
        "**3 Key Points:**",
        "\n".join(clean_key_points[:3]),
        "",
        f"**How this helps Chilli Gavva:**\n{chilli_value[:300]}"
    ]

    reply_text = "\n".join(lines)

    buttons = [[
        ("💾 Save to memory", f"mb:mem:{item_id}"),
        ("📋 Make it a task", f"mb:task:{item_id}"),
        ("💬 Discuss more", f"mb:disc:{item_id}")
    ]]

    return reply_text, buttons, item_id


def get_media_item(item_id: int, db_path=None) -> dict | None:
    conn = _connect(db_path)
    row = conn.execute("SELECT * FROM media_items WHERE id = ?", (item_id,)).fetchone()
    conn.close()
    if not row:
        return None
    return dict(row)


def handle_callback(token: str, owner_chat_id: str, data: str, db_path=None) -> str | None:
    """Handle callback buttons starting with 'mb:'."""
    if not data or not data.startswith("mb:"):
        return None

    import telegram_inbox

    parts = data.split(":", 2)
    if len(parts) < 3:
        return None

    action = parts[1]
    try:
        item_id = int(parts[2])
    except ValueError:
        return None

    item = get_media_item(item_id, db_path=db_path)
    if not item:
        reply = f"Media item #{item_id} not found."
        telegram_inbox.send_text(token, owner_chat_id, reply)
        return reply

    if action == "mem":
        # Save note into agent_memory
        fact = f"[{item['kind'].upper()}] {item['summary']} | Value: {item['chilli_gavva_value']}"
        fact = scrub_secrets_and_money(fact)
        mem_id = agent_memory.add_fact("owner_media", "media_notes", fact, confidence=1.0, db_path=db_path)
        reply = f"✅ Saved to memory as fact #{mem_id}!"
        telegram_inbox.send_text(token, owner_chat_id, reply)
        return reply

    if action == "task":
        # Create an Approve/Reject card in approval queue
        title = f"Media Task: {item['summary'][:50]}"
        payload = (
            f"Task proposal from media analysis (#{item_id}):\n"
            f"Kind: {item['kind']}\n"
            f"Summary: {item['summary']}\n"
            f"Value: {item['chilli_gavva_value']}\n"
            f"Raw context: {item.get('raw_text', '')[:500]}"
        )
        title = scrub_secrets_and_money(title)
        payload = scrub_secrets_and_money(payload)

        approval_id = guardrails.request_approval("media_task", title, payload, db_path=db_path)
        reply = f"📋 Created task card #{approval_id} in approval queue!\nUse /list or button to approve or reject."
        telegram_inbox.send_text(token, owner_chat_id, reply, telegram_inbox.draft_buttons(approval_id))
        return reply

    if action == "disc":
        # Store context in chat_brain and prompt owner for discussion
        import chat_brain
        disc_prompt = (
            f"Context set for discussion: Media Item #{item_id} ({item['kind']}):\n"
            f"Summary: {item['summary']}\n"
            f"Key Points: {item['key_points']}\n"
            f"Value: {item['chilli_gavva_value']}\n"
            f"What would you like to discuss or build with this?"
        )
        disc_prompt = scrub_secrets_and_money(disc_prompt)
        chat_brain.add_chat_memory(owner_chat_id, "assistant", disc_prompt, db_path=db_path)
        telegram_inbox.send_text(token, owner_chat_id, f"💬 Context set for Media #{item_id}!\nWhat would you like to discuss?")
        return f"Discussing media item #{item_id}"

    return None
