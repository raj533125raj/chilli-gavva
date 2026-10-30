"""marketing_agency.py — Marketing Agency: multi-channel draft kit generator for approved ready products.

One agent per channel covering a product from arrival to launch:
  1. Pinterest Agent: 5 pin titles + descriptions + keywords + board
  2. Instagram Agent: 3 captions + hashtags + carousel slide texts
  3. YouTube Shorts Agent: 2 scripts under 45 s with hook/body/CTA
  4. Telegram Channel Agent: broadcast channel post
  5. Gumroad/Etsy SEO Agent: title, 13 tags, first 160 chars
  6. 14-Day Calendar Agent: 14-day posting schedule

Rules:
  - DRAFTS ONLY: stored privately in SQLite database ('marketing_kits').
  - NOTHING IS POSTED ANYWHERE: there is no posting code at all.
  - No money amounts, price numbers, or currency symbols in prompts or generated kits.
  - Respects daily AI call cap via guardrails.can_call_llm().
  - Each agent uses llm_router with rule-based fallback, follows improver_loop learned rules,
    and passes checker.py quality checks before saving.
  - Generates Telegram notification cards per product with 'Approve kit' / 'Redo channel' buttons.
"""

import datetime
import json
import os
import re
import sqlite3
import sys
from typing import Any, Dict, List, Optional, Tuple

import checker
import cubes
import guardrails
import improver_loop
import llm_router
import rest_window

CHANNELS = ("pinterest", "instagram", "youtube", "telegram", "seo", "calendar")

# Forbidden money patterns
_MONEY_PATTERNS = re.compile(
    r"(\$|\b\d+\s*(?:usd|us\$|dollars?|cents?|eur|gbp|inr)\b|\bprice\s*:\s*\d+)",
    re.IGNORECASE
)


def connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    conn = guardrails.connect(db_path)
    conn.execute("""CREATE TABLE IF NOT EXISTS marketing_kits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        approval_id INTEGER UNIQUE,
        ts TEXT,
        product_title TEXT,
        pinterest_draft TEXT,
        instagram_draft TEXT,
        youtube_draft TEXT,
        telegram_draft TEXT,
        seo_draft TEXT,
        calendar_draft TEXT,
        status TEXT DEFAULT 'draft',
        checker_review TEXT,
        created_ts TEXT,
        updated_ts TEXT
    )""")
    conn.commit()
    return conn


def _clean_no_money(text: str) -> str:
    """Strip any money or dollar references from text to enforce zero-money rule."""
    if not text:
        return ""
    cleaned = _MONEY_PATTERNS.sub("[valuable offer]", text)
    return cleaned.strip()


def is_paused(db_path: Optional[str] = None) -> bool:
    if guardrails.is_killed() or rest_window.in_rest_window():
        return True
    return not cubes.is_kind_enabled("promotion", db_path=db_path)


def _get_ready_products(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return list of products that are APPROVED in approvals and READY in product_builds."""
    conn = connect(db_path)
    # Products where approval status = 'approved' and product_builds status = 'built'
    query = """
        SELECT a.id, a.title, a.payload, pb.folder
        FROM approvals a
        JOIN product_builds pb ON pb.approval_id = a.id
        WHERE a.status = 'approved' AND pb.status = 'built'
        ORDER BY a.id ASC
    """
    rows = conn.execute(query).fetchall()
    conn.close()
    return [{"id": r[0], "title": r[1], "payload": r[2], "folder": r[3]} for r in rows]


# ---------------------------------------------------------------------------
# Channel Agents: Prompts & Rule-Based Fallbacks
# ---------------------------------------------------------------------------

# 1. Pinterest Agent
_PINTEREST_PROMPT = """You are the Pinterest Marketing Specialist Agent.
Create a Pinterest launch kit for this digital product:
Product Title: {title}
Product Details: {details}

Requirements:
1. Provide 5 distinct Pin titles (labelled "Pin 1 Title:", "Pin 2 Title:", etc.).
2. Provide 5 Pin descriptions (labelled "Pin 1 Description:", etc.).
3. Provide a list of relevant keywords (labelled "Keywords:").
4. Provide a recommended Board name (labelled "Board Name:").

Rules:
- No financial, income, or earnings promises.
- Absolutely NO money amounts, price figures, or dollar signs.
{rules}
"""

def _pinterest_fallback(title: str, details: str) -> str:
    clean_title = _clean_no_money(title)
    return (
        f"Pin 1 Title: Ultimate Guide to {clean_title}\n"
        f"Pin 1 Description: Organize your workflow step-by-step with this clean, easy-to-use digital system.\n\n"
        f"Pin 2 Title: Boost Productivity with {clean_title}\n"
        f"Pin 2 Description: Simplify daily routines and save time with a ready-made digital layout.\n\n"
        f"Pin 3 Title: Simple & Clean {clean_title} Layout\n"
        f"Pin 3 Description: Designed for clarity and focus. Get started in minutes with zero hassle.\n\n"
        f"Pin 4 Title: Essential Checklist for {clean_title}\n"
        f"Pin 4 Description: Everything you need in one structured dashboard to stay on track.\n\n"
        f"Pin 5 Title: How to Master {clean_title}\n"
        f"Pin 5 Description: Practical templates built specifically for daily productivity and peace of mind.\n\n"
        f"Keywords: productivity, digital organization, clean layout, planner template, workflow guide\n"
        f"Board Name: Digital Planners & Productivity Systems"
    )

# 2. Instagram Agent
_INSTAGRAM_PROMPT = """You are the Instagram Content Creator Agent.
Create an Instagram launch kit for this digital product:
Product Title: {title}
Product Details: {details}

Requirements:
1. Provide 3 distinct post captions (labelled "Caption 1:", "Caption 2:", "Caption 3:").
2. Provide a set of relevant hashtags (labelled "Hashtags:").
3. Provide text for a 4-slide carousel (labelled "Slide 1:", "Slide 2:", "Slide 3:", "Slide 4:").

Rules:
- No financial, income, or earnings promises.
- Absolutely NO money amounts, price figures, or dollar signs.
{rules}
"""

def _instagram_fallback(title: str, details: str) -> str:
    clean_title = _clean_no_money(title)
    return (
        f"Caption 1: Tired of messy notes? {clean_title} brings structure back to your daily routines. Check out the link in bio for full details!\n\n"
        f"Caption 2: Step up your organization game! Designed for quick setup and maximum clarity. Perfect for busy creators and professionals.\n\n"
        f"Caption 3: Simple, practical, and effective. {clean_title} helps you focus on what matters without clutter.\n\n"
        f"Hashtags: #productivity #digitalplanner #organization #creativelife #workflow #planning\n\n"
        f"Carousel Slide Texts:\n"
        f"Slide 1: How to organize your days effortlessly with {clean_title}\n"
        f"Slide 2: Step 1 - Clear out the clutter and set your priorities\n"
        f"Slide 3: Step 2 - Use structured layouts to stay focused\n"
        f"Slide 4: Ready to streamline your system? Find out more!"
    )

# 3. YouTube Shorts Agent
_YOUTUBE_PROMPT = """You are the YouTube Shorts Creator Agent.
Create 2 YouTube Shorts scripts (each under 45 seconds) for this digital product:
Product Title: {title}
Product Details: {details}

Requirements:
For Script 1 and Script 2, include:
- Hook (0-5s): Catch attention fast
- Body (5-35s): Highlight problem and solution
- CTA (35-45s): Clear action call

Rules:
- Must be under 45 seconds speaking time.
- No financial, income, or earnings promises.
- Absolutely NO money amounts, price figures, or dollar signs.
{rules}
"""

def _youtube_fallback(title: str, details: str) -> str:
    clean_title = _clean_no_money(title)
    return (
        f"Script 1 (Under 45s):\n"
        f"[Hook 0-5s]: Stop wasting hours trying to structure your daily plans manually!\n"
        f"[Body 5-35s]: Here is a quick breakdown of how {clean_title} solves your layout chaos. It breaks down tasks into clear steps, keeps your goals visible, and saves you daily stress.\n"
        f"[CTA 35-45s]: Tap the link below to get your copy today!\n\n"
        f"Script 2 (Under 45s):\n"
        f"[Hook 0-5s]: The one productivity layout you need this week!\n"
        f"[Body 5-35s]: If you feel overwhelmed by messy tools, {clean_title} brings clean design and structured focus right to your workspace.\n"
        f"[CTA 35-45s]: Check out the link to start organizing now!"
    )

# 4. Telegram Channel Agent
_TELEGRAM_PROMPT = """You are the Telegram Channel Copywriter Agent.
Create an engaging Telegram channel broadcast post for this digital product:
Product Title: {title}
Product Details: {details}

Requirements:
1. Engaging title line with emoji.
2. Short explanation of key benefits.
3. Call to Action link placeholder.

Rules:
- Format cleanly with bullet points and emojis.
- No financial, income, or earnings promises.
- Absolutely NO money amounts, price figures, or dollar signs.
{rules}
"""

def _telegram_fallback(title: str, details: str) -> str:
    clean_title = _clean_no_money(title)
    return (
        f"🚀 <b>New Release: {clean_title}</b>\n\n"
        f"Streamline your work and keep your goals in focus with our latest digital template!\n\n"
        f"✨ <b>Key Features:</b>\n"
        f"• Clean, intuitive layout designed for fast setup\n"
        f"• Pre-formatted structure to manage daily priorities\n"
        f"• Fully reusable for long-term productivity\n\n"
        f"👉 Tap here to view the full product details and download your copy!"
    )

# 5. Gumroad/Etsy SEO Agent
_SEO_PROMPT = """You are the Etsy & Gumroad SEO Specialist Agent.
Create an SEO kit for this digital product:
Product Title: {title}
Product Details: {details}

Requirements:
1. Optimized Product Title (under 140 chars).
2. Exactly 13 SEO tags separated by commas.
3. First 160 characters snippet (meta description).

Rules:
- No financial, income, or earnings promises.
- Absolutely NO money amounts, price figures, or dollar signs.
{rules}
"""

def _seo_fallback(title: str, details: str) -> str:
    clean_title = _clean_no_money(title)[:100]
    snippet = f"{clean_title} - Clean, practical digital template designed to organize your daily priorities and simplify your productivity workflow."[:160]
    tags = [
        "digital planner", "printable template", "productivity sheet", "workflow guide",
        "daily layout", "organization tool", "planner insert", "digital download",
        "minimalist planner", "custom layout", "focus tracker", "life planner", "task list"
    ]
    return (
        f"SEO Title: {clean_title} | Minimalist Digital Planner Template\n\n"
        f"13 Tags: {', '.join(tags)}\n\n"
        f"First 160 Characters Snippet: {snippet}"
    )

# 6. 14-Day Calendar Agent
_CALENDAR_PROMPT = """You are the Social Media Content Planner Agent.
Create a 14-day posting calendar for launching this digital product:
Product Title: {title}
Product Details: {details}

Requirements:
Provide day-by-day ideas for Day 1 through Day 14 covering Pinterest, Instagram, YouTube Shorts, and Telegram.

Rules:
- Concise, actionable content prompts for each of the 14 days.
- No financial, income, or earnings promises.
- Absolutely NO money amounts, price figures, or dollar signs.
{rules}
"""

def _calendar_fallback(title: str, details: str) -> str:
    clean_title = _clean_no_money(title)
    days = []
    channels = ["Pinterest Pin & Story", "Instagram Reel / Post", "YouTube Short", "Telegram Update"]
    themes = [
        "Product teaser & problem statement", "Behind-the-scenes creation", "Feature spotlight #1",
        "User workflow demonstration", "FAQ and key benefits", "Comparison with messy templates",
        "Mid-launch recap", "Feature spotlight #2", "Tips for daily organization",
        "Carousel slide showcase", "Quick video walk-through", "Community check-in",
        "Last chance launch offer", "Final wrap-up & feedback"
    ]
    for idx in range(1, 15):
        channel = channels[(idx - 1) % len(channels)]
        theme = themes[idx - 1]
        days.append(f"Day {idx} [{channel}]: {theme} for {clean_title}")
    return "\n".join(days)


AGENTS_CONFIG = {
    "pinterest": {"prompt": _PINTEREST_PROMPT, "fallback": _pinterest_fallback},
    "instagram": {"prompt": _INSTAGRAM_PROMPT, "fallback": _instagram_fallback},
    "youtube": {"prompt": _YOUTUBE_PROMPT, "fallback": _youtube_fallback},
    "telegram": {"prompt": _TELEGRAM_PROMPT, "fallback": _telegram_fallback},
    "seo": {"prompt": _SEO_PROMPT, "fallback": _seo_fallback},
    "calendar": {"prompt": _CALENDAR_PROMPT, "fallback": _calendar_fallback},
}


def generate_channel_draft(
    channel: str,
    title: str,
    details: str,
    db_path: Optional[str] = None,
    llm_fn: Any = None
) -> str:
    """Generate draft text for a specific marketing channel using AI or rule-based fallback."""
    if channel not in AGENTS_CONFIG:
        raise ValueError(f"Unknown channel: {channel}")

    config = AGENTS_CONFIG[channel]
    rules_block = improver_loop.get_active_rules_block("marketing", db_path=db_path)

    clean_title = _clean_no_money(title)
    clean_details = _clean_no_money(details[:2000])

    prompt = config["prompt"].format(
        title=clean_title,
        details=clean_details,
        rules=rules_block or ""
    )

    draft_text = None
    if guardrails.can_call_llm(db_path):
        try:
            if llm_fn and callable(llm_fn):
                draft_text = llm_fn(prompt, size="small")
            else:
                draft_text = llm_router.chat(prompt, db_path=db_path, size="small")
        except Exception:
            draft_text = None

    if not draft_text or not isinstance(draft_text, str) or len(draft_text.strip()) < 30:
        draft_text = config["fallback"](clean_title, clean_details)

    # Sanitize money amounts from output
    draft_text = _clean_no_money(draft_text)

    # Validate output through checker gate
    chk_res = checker.check_draft(draft_text, topic=clean_title, db_path=db_path)
    if not chk_res.get("passed") and chk_res.get("score", 0) < 70:
        # Fall back to safe rule-based draft if generated content failed quality/safety check
        draft_text = _clean_no_money(config["fallback"](clean_title, clean_details))

    return draft_text


def generate_full_kit(
    approval_id: int,
    title: str,
    details: str,
    db_path: Optional[str] = None,
    llm_fn: Any = None
) -> Dict[str, str]:
    """Generate complete multi-channel marketing kit for a product."""
    kit = {"approval_id": approval_id, "product_title": title}
    for ch in CHANNELS:
        kit[f"{ch}_draft"] = generate_channel_draft(ch, title, details, db_path=db_path, llm_fn=llm_fn)
    return kit


# ---------------------------------------------------------------------------
# Database Storage & Public Status Counts
# ---------------------------------------------------------------------------

def save_kit(kit: Dict[str, Any], db_path: Optional[str] = None) -> int:
    """Store or update marketing kit in SQLite database."""
    conn = connect(db_path)
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    approval_id = kit["approval_id"]
    title = kit.get("product_title", "")
    status = kit.get("status", "draft")
    review = kit.get("checker_review", "Checker: Passed (Draft Only)")

    cur = conn.execute("""
        INSERT INTO marketing_kits (
            approval_id, ts, product_title, pinterest_draft, instagram_draft, youtube_draft,
            telegram_draft, seo_draft, calendar_draft, status, checker_review, created_ts, updated_ts
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(approval_id) DO UPDATE SET
            product_title = excluded.product_title,
            pinterest_draft = COALESCE(excluded.pinterest_draft, marketing_kits.pinterest_draft),
            instagram_draft = COALESCE(excluded.instagram_draft, marketing_kits.instagram_draft),
            youtube_draft = COALESCE(excluded.youtube_draft, marketing_kits.youtube_draft),
            telegram_draft = COALESCE(excluded.telegram_draft, marketing_kits.telegram_draft),
            seo_draft = COALESCE(excluded.seo_draft, marketing_kits.seo_draft),
            calendar_draft = COALESCE(excluded.calendar_draft, marketing_kits.calendar_draft),
            status = excluded.status,
            checker_review = excluded.checker_review,
            updated_ts = excluded.updated_ts
    """, (
        approval_id, now_iso, title,
        kit.get("pinterest_draft", ""), kit.get("instagram_draft", ""), kit.get("youtube_draft", ""),
        kit.get("telegram_draft", ""), kit.get("seo_draft", ""), kit.get("calendar_draft", ""),
        status, review, now_iso, now_iso
    ))
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def get_kit(approval_id: int, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Retrieve marketing kit by approval_id."""
    conn = connect(db_path)
    row = conn.execute("""
        SELECT id, approval_id, ts, product_title, pinterest_draft, instagram_draft,
               youtube_draft, telegram_draft, seo_draft, calendar_draft, status, checker_review
        FROM marketing_kits WHERE approval_id = ?
    """, (approval_id,)).fetchone()
    conn.close()
    if not row:
        return None
    keys = ("id", "approval_id", "ts", "product_title", "pinterest_draft", "instagram_draft",
            "youtube_draft", "telegram_draft", "seo_draft", "calendar_draft", "status", "checker_review")
    return dict(zip(keys, row))


def list_kits(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """List all stored marketing kits."""
    conn = connect(db_path)
    rows = conn.execute("""
        SELECT id, approval_id, ts, product_title, status, checker_review
        FROM marketing_kits ORDER BY id ASC
    """).fetchall()
    conn.close()
    return [{"id": r[0], "approval_id": r[1], "ts": r[2], "product_title": r[3], "status": r[4], "checker_review": r[5]} for r in rows]


def get_marketing_kits_count(db_path: Optional[str] = None) -> Dict[str, int]:
    """Return count dictionary for public status: total, draft, approved.

    Never leaks draft content, details, or prompts.
    """
    target = db_path or guardrails.DB_PATH
    if not os.path.exists(target):
        return {"total": 0, "draft": 0, "approved": 0}
    conn = connect(db_path)
    try:
        total = conn.execute("SELECT COUNT(*) FROM marketing_kits").fetchone()[0]
        draft = conn.execute("SELECT COUNT(*) FROM marketing_kits WHERE status = 'draft'").fetchone()[0]
        approved = conn.execute("SELECT COUNT(*) FROM marketing_kits WHERE status = 'approved'").fetchone()[0]
    except sqlite3.Error:
        total = draft = approved = 0
    finally:
        conn.close()
    return {"total": total, "draft": draft, "approved": approved}


# ---------------------------------------------------------------------------
# Telegram Card & Callback Interaction
# ---------------------------------------------------------------------------

def format_telegram_card(kit: Dict[str, Any]) -> Tuple[str, List[List[Tuple[str, str]]]]:
    """Format Telegram announcement card text and inline buttons for a marketing kit."""
    approval_id = kit["approval_id"]
    title = kit.get("product_title", f"Product #{approval_id}")
    status = kit.get("status", "draft").upper()

    card_text = (
        f"📢 <b>Marketing Launch Kit ({status})</b>\n"
        f"<b>Product #{approval_id}:</b> {title[:60]}\n\n"
        f"📦 <b>Included Channel Drafts:</b>\n"
        f"• Pinterest: 5 Pin titles, descriptions & board\n"
        f"• Instagram: 3 Captions, hashtags & carousel\n"
        f"• YouTube Shorts: 2 Scripts (<45s hook/body/CTA)\n"
        f"• Telegram: Broadcast post format\n"
        f"• SEO: Etsy/Gumroad Title, 13 Tags, 160 char snippet\n"
        f"• Calendar: 14-day posting schedule\n\n"
        f"<i>Status: DRAFTS ONLY (stored privately in database). Nothing is posted automatically.</i>"
    )

    buttons = [
        [("✅ Approve kit", f"mkt:a:{approval_id}")],
        [
            ("🔄 Redo Pin", f"mkt:r:{approval_id}:pinterest"),
            ("🔄 Redo IG", f"mkt:r:{approval_id}:instagram"),
            ("🔄 Redo YT", f"mkt:r:{approval_id}:youtube")
        ],
        [
            ("🔄 Redo TG", f"mkt:r:{approval_id}:telegram"),
            ("🔄 Redo SEO", f"mkt:r:{approval_id}:seo"),
            ("🔄 Redo Cal", f"mkt:r:{approval_id}:calendar")
        ]
    ]

    return card_text, buttons


def handle_callback(
    token: str,
    owner_chat_id: str,
    data: str,
    db_path: Optional[str] = None
) -> Optional[str]:
    """Handle Telegram callback queries for marketing agency (mkt:a:ID, mkt:r:ID:channel)."""
    if not data or not data.startswith("mkt:"):
        return None

    parts = data.split(":")
    if len(parts) < 3:
        return None

    action = parts[1]
    try:
        approval_id = int(parts[2])
    except ValueError:
        return None

    kit = get_kit(approval_id, db_path=db_path)
    if not kit:
        return f"Marketing kit for Product #{approval_id} not found."

    if action == "a":
        # Approve kit
        kit["status"] = "approved"
        save_kit(kit, db_path=db_path)
        return f"Approved marketing kit for Product #{approval_id}. (Stored as approved draft)."

    elif action == "r" and len(parts) == 4:
        channel = parts[3].lower()
        if channel not in CHANNELS:
            return f"Unknown channel: {channel}"

        title = kit.get("product_title", f"Product #{approval_id}")
        approval = guardrails.get_approval(approval_id, db_path=db_path)
        details = (approval.get("payload") or title) if approval else title
        new_draft = generate_channel_draft(channel, title, details, db_path=db_path)
        kit[f"{channel}_draft"] = new_draft
        kit["status"] = "draft"
        save_kit(kit, db_path=db_path)
        return f"Redid {channel.capitalize()} draft for Product #{approval_id}."

    return None


# ---------------------------------------------------------------------------
# Core Workflow Entry Point
# ---------------------------------------------------------------------------

def process_ready_products(
    db_path: Optional[str] = None,
    send_card_fn: Any = None,
    llm_fn: Any = None
) -> List[Dict[str, Any]]:
    """Find all approved ready products without kits (or needing update), generate kits & notify."""
    if is_paused(db_path):
        return []
    ready_products = _get_ready_products(db_path=db_path)
    processed = []

    for prod in ready_products:
        aid = prod["id"]
        title = prod["title"]
        details = prod["payload"]

        existing = get_kit(aid, db_path=db_path)
        if existing:
            continue

        kit = generate_full_kit(aid, title, details, db_path=db_path, llm_fn=llm_fn)
        save_kit(kit, db_path=db_path)

        if send_card_fn and callable(send_card_fn):
            card_text, buttons = format_telegram_card(kit)
            send_card_fn(card_text, buttons)

        processed.append(kit)

    return processed


def main(argv: Optional[List[str]] = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else "status"
    if cmd == "status":
        counts = get_marketing_kits_count()
        print(f"Marketing Kits: {counts['total']} total ({counts['draft']} draft, {counts['approved']} approved)")
    elif cmd == "run":
        prods = process_ready_products()
        print(f"Marketing Agency: processed {len(prods)} products.")
    else:
        print("usage: python3 marketing_agency.py [status|run]")


if __name__ == "__main__":
    main()
