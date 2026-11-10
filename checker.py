"""Checker agent: product quality review before Telegram approval.

Evaluates product drafts against rules and an updated robust rubric:
  Sub-scores (sum = 100):
    1. Buyer clarity (weight 20): identifies a clear buyer persona with real use cases.
    2. Specificity of audience (weight 15): audience is concrete and targeted (not vague 'people', 'users', 'everyone').
    3. Buildable format (weight 25): format the builder actually supports (sheets, notion, planner/pdf, resume).
    4. Differentiation vs recent drafts (weight 15): unique angle, beats basic free template, avoids repetitive audience phrase.
    5. Listing honesty (weight 15): promises only real features, zero money/income claims, zero leaks, structure present.
    6. Seasonality fit (weight 10): seasonal or evergreen fit with demand cues.

Hard Caps & Penalties:
  - Generic title -> max score 60
  - Same audience phrase as 3+ recent drafts (e.g. 'for Remote Creators') -> -25 penalty
  - Format the builder cannot make (e.g. mobile app, saas, physical goods, video course) -> max score 40
  - Vague 'template pack' with no contents / empty structure -> max score 55
  - Financial/earnings claims or unapproved leaks -> heavy deduction / capped

LLM review may only adjust the score +/- 10 from the rule score; parse failures fall back to rules.
Preserves check_draft / format_review / parse_review / review_passes signatures and behavior for callers.
"""

import json
import os
import re
import sqlite3
from typing import Dict, List, Any, Optional, Tuple

import llm_router

# =====================================================================
# Dictionaries, Patterns, and Constants
# =====================================================================

# Sub-score maximum weights (sum = 100)
WEIGHT_BUYER_CLARITY = 20
WEIGHT_SPECIFICITY_AUDIENCE = 15
WEIGHT_BUILDABLE_FORMAT = 25
WEIGHT_DIFFERENTIATION = 15
WEIGHT_LISTING_HONESTY = 15
WEIGHT_SEASONALITY_FIT = 10

# Valid buildable formats that product_builder / chilli-gavva supports
_BUILDABLE_FORMAT_KEYWORDS = (
    "spreadsheet", "sheets", "google sheets", "excel", "budget", "tracker",
    "notion", "notion template", "workspace", "dashboard",
    "planner", "printable", "pdf", "dated planner", "calendar",
    "resume", "cv", "cover letter", "ats resume", "ats-friendly", "job tracker",
    "checklist", "worksheet", "journal"
)

# Formats the builder CANNOT make (SaaS, apps, physical items, video course, heavy software)
_UNBUILDABLE_FORMAT_PATTERNS = re.compile(
    r"\b(mobile app|ios app|android app|software|saas|web app|web application|"
    r"video course|audio course|masterclass|physical book|hardcover|paperback|"
    r"t-shirt|merchandise|hardware|consulting call|1-on-1 coaching|membership site|"
    r"chrome extension|figma plugin|api service|backend service|database hosting)\b",
    re.IGNORECASE
)

# Generic title words/patterns
_GENERIC_TITLE_PATTERNS = re.compile(
    r"^(?:\[.*?\]\s*)?(?:digital product|template pack|product template|bundle pack|"
    r"ultimate template|starter pack|productivity template|planner template|"
    r"tracker|planner|template|digital planner|all-in-one pack|useful template|"
    r"good template|simple template|digital bundle|daily tracker)$|"
    r"^(?:template|planner|tracker|bundle|pack|product|file)$",
    re.IGNORECASE
)

# Specific audience keywords (targeted)
_SPECIFIC_AUDIENCE_WORDS = (
    "freelancers", "freelancer", "teachers", "teacher", "students", "student",
    "neurodivergent", "adhd", "nurses", "nurse", "job seekers", "job seeker",
    "homeowners", "renters", "renter", "parents", "parent", "moms", "dads",
    "writers", "writer", "authors", "author", "designers", "designer",
    "coaches", "coach", "consultants", "consultant", "small business owners",
    "etsy sellers", "shop owners", "remote workers", "creators", "podcasters",
    "developers", "graduates", "travelers", "couples", "families", "accountants",
    "solopreneurs", "agencies", "photographers"
)

# Vague audience terms that lack specificity
_VAGUE_AUDIENCE_WORDS = (
    "people", "user", "users", "everyone", "anybody", "someone", "buyers",
    "buyer", "customers", "all", "general", "person", "folks", "anyone"
)

_BEATS_FREE_PATTERNS = re.compile(
    r"\b(unlike|instead of|not just|what makes|compared (?:to|with)|no other|"
    r"(?:built|designed|made) specifically|better than|over a free|free template|"
    r"pre-?filled|automated|dashboard|formulas|step[- ]by[- ]step|ready[- ]made|"
    r"simple|honest|tool|browser|seconds|no account|no sign-up|benefit|saves hours|"
    r"workflow|streamlined|curated|comprehensive|all-in-one system)\b",
    re.IGNORECASE
)

_MONEY_CLAIM_PATTERNS = re.compile(
    r"\b(guaranteed|get rich|passive income|make money|earn|financial freedom|"
    r"income promise|revenue guarantee|fast cash|zero risk earnings|"
    r"make \$|1000/day|rich quick|guaranteed returns)\b",
    re.IGNORECASE
)

_LEAK_PATTERNS = re.compile(
    r"\b(confidential|internal only|secret strategy|unreleased|do not share|"
    r"private key|api_key|token=|password=)\b",
    re.IGNORECASE
)

_PRODUCT_STRUCTURE_LABELS = (
    "what's inside", "whats inside", "features:", "structure:", "contents:",
    "what you get", "includes:", "modules:", "components:", "deliverables:",
    "inside this:", "table of contents", "sections:"
)

# Seasonality terms
_SEASONAL_WORDS = (
    "diwali", "new year", "back to school", "exam", "exams", "wedding",
    "summer", "holiday", "christmas", "tax season", "q1", "q2", "q3", "q4",
    "annual", "2027", "2026", "spring", "winter", "fall", "autumn"
)

# Evergreen terms
_EVERGREEN_WORDS = (
    "budget", "invoice", "planner", "tracker", "notion", "resume", "ats",
    "daily", "weekly", "monthly", "routine", "habit", "client", "project",
    "freelancer", "adhd", "focus", "finance"
)


# =====================================================================
# Database Helpers for Recent Drafts Auditing
# =====================================================================

def _get_recent_drafts(db_path: Optional[str] = None, limit: int = 30) -> List[Dict[str, str]]:
    """Retrieve recent draft titles and payloads from approvals table if available."""
    if not db_path:
        import guardrails
        db_path = getattr(guardrails, "_effective_db", lambda p: p)(db_path)
    if not db_path or not os.path.exists(db_path):
        return []
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        # Query recent draft items
        cur.execute(
            "SELECT id, title, payload FROM approvals ORDER BY id DESC LIMIT ?",
            (limit,)
        )
        rows = cur.fetchall()
        conn.close()
        return [{"id": r[0], "title": r[1] or "", "payload": r[2] or ""} for r in rows]
    except Exception:
        return []


def _extract_audience_phrases(text: str) -> List[str]:
    """Extract candidate audience phrases (e.g., 'for remote creators', 'target: freelancers')."""
    if not text:
        return []
    phrases = []
    # Match patterns like 'BUYER: ...', 'for <audience>', 'target audience: ...', 'designed for ...'
    matches = re.findall(
        r"(?:buyer|audience|target audience|designed for|built for|for)\s*[:\-]?\s*([a-zA-Z0-9\s]{3,35})",
        text,
        re.IGNORECASE
    )
    for m in matches:
        clean = m.strip().lower()
        # Remove trailing words like 'who', 'that', 'with'
        clean = re.sub(r"\s+(?:who|that|which|with|to|in|and)\b.*$", "", clean).strip()
        if len(clean) >= 4 and len(clean.split()) <= 4:
            phrases.append(clean)
    return phrases


def check_audience_phrase_repetition(
    audience_phrases: List[str],
    recent_drafts: List[Dict[str, str]]
) -> Tuple[bool, Optional[str]]:
    """Check if any audience phrase appeared in 3 or more recent drafts."""
    if not audience_phrases or not recent_drafts:
        return False, None

    for phrase in audience_phrases:
        if len(phrase) < 4:
            continue
        phrase_re = re.compile(r"\b" + re.escape(phrase) + r"\b", re.IGNORECASE)
        count = 0
        for draft in recent_drafts:
            combined = (draft.get("title", "") + " " + draft.get("payload", "")).lower()
            if phrase_re.search(combined):
                count += 1
                if count >= 3:
                    return True, phrase
    return False, None


# =====================================================================
# Rubric Sub-scores Calculation & Rule-based Scoring
# =====================================================================

def evaluate_subscores(
    draft_text: str,
    topic: str = "",
    recent_drafts: Optional[List[Dict[str, str]]] = None
) -> Dict[str, Any]:
    """Calculate separate sub-scores, hard caps, and penalties.

    Returns:
      {
        "subscores": {
          "buyer_clarity": int,
          "specificity_audience": int,
          "buildable_format": int,
          "differentiation": int,
          "listing_honesty": int,
          "seasonality_fit": int
        },
        "raw_sum": int,
        "penalties": list,
        "caps": list,
        "final_score": int,
        "reasons": list,
        "flags": dict
      }
    """
    text = draft_text or ""
    lower = text.lower()
    topic_lower = (topic or "").lower()

    # Extract title from draft if available
    title_match = re.search(r"(?:product name|listing title|title)\s*[:\-]\s*(.+)", text, re.IGNORECASE)
    extracted_title = title_match.group(1).strip() if title_match else topic

    # 1. Buyer clarity (weight 20)
    # Checks for explicit BUYER field or buyer use case description
    specific_hits = [w for w in _SPECIFIC_AUDIENCE_WORDS if re.search(r"\b" + re.escape(w) + r"\b", lower)]
    vague_hits = [w for w in _VAGUE_AUDIENCE_WORDS if re.search(r"\b" + re.escape(w) + r"\b", lower)]

    has_buyer_header = bool(re.search(r"\b(buyer|target buyer|target audience|who it's for|who this is for)\b", lower))
    has_use_case = bool(re.search(r"\b(who need to|who want to|use case|helps them|to manage|to organize|to track)\b", lower))

    if has_buyer_header and has_use_case:
        buyer_score = 20 if specific_hits else 8
    elif has_buyer_header or has_use_case:
        buyer_score = 15 if specific_hits else 6
    elif specific_hits:
        buyer_score = 12
    else:
        buyer_score = 6

    # 2. Specificity of audience (weight 15)
    # Checks if audience is specific rather than generic
    if len(specific_hits) >= 2:
        audience_score = 15
    elif len(specific_hits) == 1:
        audience_score = 12
    elif len(vague_hits) > 0 or "buyer" in lower:
        audience_score = 6
    else:
        audience_score = 4

    # 3. Buildable format (weight 25)
    # Checks if format matches builder's capabilities
    is_unbuildable = bool(_UNBUILDABLE_FORMAT_PATTERNS.search(lower) or _UNBUILDABLE_FORMAT_PATTERNS.search(topic_lower))
    buildable_hits = [kw for kw in _BUILDABLE_FORMAT_KEYWORDS if re.search(r"\b" + re.escape(kw) + r"\b", lower) or re.search(r"\b" + re.escape(kw) + r"\b", topic_lower)]

    if is_unbuildable:
        format_score = 5
    elif len(buildable_hits) >= 2:
        format_score = 25
    elif len(buildable_hits) == 1:
        format_score = 18
    else:
        format_score = 12

    # 4. Differentiation vs recent drafts (weight 15)
    beats_free = bool(_BEATS_FREE_PATTERNS.search(lower))
    has_unique_features = len(re.findall(r"[-*•]\s+[^\n]+", text)) >= 3
    if beats_free and has_unique_features:
        diff_score = 15
    elif beats_free or has_unique_features:
        diff_score = 10
    else:
        diff_score = 6

    # 5. Listing honesty (weight 15)
    bullet_count = len(re.findall(r"[-*•]\s+[^\n]+", text))
    has_structure = (any(label in lower for label in _PRODUCT_STRUCTURE_LABELS) and bullet_count >= 2) or (len(text.strip()) >= 250 and bullet_count >= 2)
    money_claims = _MONEY_CLAIM_PATTERNS.findall(lower)
    leaks = _LEAK_PATTERNS.findall(lower)

    honesty_score = 15
    if not has_structure:
        honesty_score -= 9
    if money_claims:
        honesty_score -= 10
    if leaks:
        honesty_score -= 10
    honesty_score = max(0, honesty_score)

    # 6. Seasonality fit (weight 10)
    has_season = any(re.search(r"\b" + re.escape(w) + r"\b", lower) for w in _SEASONAL_WORDS)
    has_evergreen = any(re.search(r"\b" + re.escape(w) + r"\b", lower) for w in _EVERGREEN_WORDS)

    if has_season and has_evergreen:
        season_score = 10
    elif has_season:
        season_score = 8
    elif has_evergreen:
        season_score = 7
    else:
        season_score = 4

    subscores = {
        "buyer_clarity": buyer_score,
        "specificity_audience": audience_score,
        "buildable_format": format_score,
        "differentiation": diff_score,
        "listing_honesty": honesty_score,
        "seasonality_fit": season_score,
    }
    raw_sum = sum(subscores.values())

    # =====================================================================
    # Evaluate Hard Caps and Penalties
    # =====================================================================
    penalties = []
    caps = []
    reasons = []

    # Check unbuildable format -> hard cap 40
    if is_unbuildable:
        caps.append(("unbuildable_format", 40))
        reasons.append("Format the builder cannot make (app/physical/course)")

    # Check generic title -> hard cap 60
    # Test extracted title and topic
    title_to_check = extracted_title.strip()
    is_generic_title = False
    if title_to_check:
        clean_t = re.sub(r"^\[.*?\]\s*", "", title_to_check).strip()
        if _GENERIC_TITLE_PATTERNS.match(clean_t) or len(clean_t.split()) <= 2 and clean_t.lower() in ("template", "planner", "tracker", "digital product", "template pack", "starter pack"):
            is_generic_title = True
    else:
        is_generic_title = True

    if is_generic_title:
        caps.append(("generic_title", 60))
        reasons.append("Generic title lacks differentiation")

    # Check vague 'template pack' with no contents -> hard cap 55
    is_vague_template_pack = False
    lower_title = title_to_check.lower()
    has_contents_list = any(label in lower for label in _PRODUCT_STRUCTURE_LABELS) and len(re.findall(r"[-*•]\s+[^\n]+", text)) >= 2
    if ("template pack" in lower or "template pack" in lower_title or "starter pack" in lower) and not has_contents_list:
        is_vague_template_pack = True
        caps.append(("vague_template_pack_no_contents", 55))
        reasons.append("Vague template pack with no contents listed")
    elif not has_structure and len(text.strip()) < 80:
        caps.append(("vague_template_pack_no_contents", 55))
        reasons.append("Missing detailed product features or structure")

    # Check audience phrase repetition across 3+ recent drafts -> -25 penalty
    audience_phrases = _extract_audience_phrases(text)
    if recent_drafts:
        is_repeated, rep_phrase = check_audience_phrase_repetition(audience_phrases, recent_drafts)
        if is_repeated:
            penalties.append(("repeated_audience_phrase", 25, rep_phrase))
            reasons.append(f"Same audience phrase as 3+ recent drafts ('{rep_phrase}')")

    # Financial / leak deductions
    if money_claims:
        penalties.append(("money_claims", 35, money_claims[0]))
        reasons.append("Contains prohibited financial or earnings promises")
    if leaks:
        penalties.append(("leaks", 35, leaks[0]))
        reasons.append("Contains unapproved internal information or leak")

    # Compute final score
    score = raw_sum
    for p in penalties:
        score -= p[1]

    for c in caps:
        score = min(score, c[1])

    score = max(0, min(100, score))

    if not reasons and score >= 70:
        reasons = ["Clear audience and features", "Explains value over free template"]

    flags = {
        "is_unbuildable": is_unbuildable,
        "is_generic_title": is_generic_title,
        "is_vague_template_pack": is_vague_template_pack,
        "money_claims": bool(money_claims),
        "leaks": bool(leaks),
    }

    return {
        "subscores": subscores,
        "raw_sum": raw_sum,
        "penalties": penalties,
        "caps": caps,
        "final_score": score,
        "reasons": reasons,
        "flags": flags
    }


def rule_based_fallback(
    draft_text: str,
    topic: str = "",
    recent_drafts: Optional[List[Dict[str, str]]] = None,
    db_path: Optional[str] = None
) -> Dict[str, Any]:
    """Rule-based quality check when AI router is unavailable. Never raises."""
    if recent_drafts is None and db_path:
        recent_drafts = _get_recent_drafts(db_path)

    eval_res = evaluate_subscores(draft_text, topic=topic, recent_drafts=recent_drafts)
    score = eval_res["final_score"]
    reasons = eval_res["reasons"]
    flags = eval_res["flags"]

    money_claims = flags["money_claims"]
    leaks = flags["leaks"]

    verdict = "pass" if score >= 70 else ("reject" if score < 40 or money_claims or leaks or flags.get("is_unbuildable") else "revise")

    return {
        "score": score,
        "verdict": verdict,
        "reasons": reasons[:4],
        "passed": score >= 70 and verdict == "pass",
        "fallback": True,
        "subscores": eval_res["subscores"],
        "caps": eval_res["caps"],
        "penalties": eval_res["penalties"],
    }


_CHECKER_PROMPT = """You are the QA Checker for a small digital-product team. Check this draft before a human sees it.
Be strict and honest. Rubric:
1. Promises real features: clear, realistic features and structure (no impossible or fake promises).
2. Clear audience: identifies a specific target buyer persona.
3. Beats a free template: explains why this offers more value than a basic free template.
4. No money claims: ZERO income promises, earnings guarantees, or financial gain claims.
5. No unapproved leaks: no confidential information, internal notes, or unapproved ideas leaked.

Verdict: "pass" = ready for a human (score >= 70), "revise" = fixable with rewrite (score 40-69), "reject" = unsafe/unusable (score < 40).
Reply with ONLY this JSON and nothing else:
{{"score": <0-100>, "verdict": "pass" | "revise" | "reject", "issues": ["short reason 1", "short reason 2"]}}

Brief: {topic}

Draft:
{draft}"""


def parse_checker_response(response_text: str) -> Optional[Dict[str, Any]]:
    """Parse JSON response from the LLM."""
    if not response_text or not isinstance(response_text, str):
        return None
    start = response_text.find("{")
    end = response_text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(response_text[start:end + 1])
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    score = data.get("score")
    verdict = str(data.get("verdict", "")).strip().lower()
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return None
    issues = data.get("issues") or data.get("reasons") or []
    if not isinstance(issues, list):
        issues = [str(issues)]
    clean_reasons = [str(r).strip()[:100] for r in issues if str(r).strip()][:5]
    score_int = max(0, min(100, int(round(score))))
    if verdict not in ("pass", "revise", "reject"):
        verdict = "pass" if score_int >= 70 else ("reject" if score_int < 40 else "revise")
    return {
        "score": score_int,
        "verdict": verdict,
        "reasons": clean_reasons,
        "passed": score_int >= 70 and verdict == "pass",
        "fallback": False
    }


def check_draft(
    draft_text: str,
    topic: str = "",
    ctx: Optional[Dict[str, Any]] = None,
    db_path: Optional[str] = None,
    recent_drafts: Optional[List[Dict[str, str]]] = None
) -> Dict[str, Any]:
    """Check a product draft using the rebuilt rubric + AI review bound within +/- 10.

    Returns dict with keys: 'score' (int 0-100), 'verdict' (str), 'reasons' (list of str), 'passed' (bool), 'fallback' (bool).
    """
    if not draft_text:
        return {
            "score": 0,
            "verdict": "reject",
            "reasons": ["Empty draft content"],
            "passed": False,
            "fallback": True,
            "subscores": {
                "buyer_clarity": 0,
                "specificity_audience": 0,
                "buildable_format": 0,
                "differentiation": 0,
                "listing_honesty": 0,
                "seasonality_fit": 0,
            }
        }

    # 1. Run rule-based rubric evaluation first to obtain baseline score & sub-scores
    if recent_drafts is None and db_path:
        recent_drafts = _get_recent_drafts(db_path)

    rule_result = rule_based_fallback(draft_text, topic=topic, recent_drafts=recent_drafts, db_path=db_path)
    rule_score = rule_result["score"]

    llm_fn = None
    if ctx and callable(ctx.get("llm")):
        llm_fn = ctx["llm"]

    rules_block = ""
    try:
        import improver_loop
        rules_block = improver_loop.get_active_rules_block("checker", db_path=db_path)
    except Exception:
        pass

    prompt = _CHECKER_PROMPT.format(topic=topic or "Digital Product", draft=draft_text[:6000])
    if rules_block:
        prompt += "\n" + rules_block

    response = None
    try:
        if llm_fn:
            response = llm_fn(prompt, size="large")
        else:
            response = llm_router.chat(prompt, db_path=db_path, size="large")
    except Exception:
        response = None

    parsed = parse_checker_response(response)
    if parsed is not None:
        # LLM review may ONLY move the score +-10 from the rule score
        raw_llm_score = parsed["score"]
        bounded_score = max(rule_score - 10, min(rule_score + 10, raw_llm_score))
        bounded_score = max(0, min(100, bounded_score))

        # Respect any hard caps on bounded score
        for cap in rule_result.get("caps", []):
            bounded_score = min(bounded_score, cap[1])

        # Merge reasons: keep LLM insights if any, alongside rule reasons
        merged_reasons = []
        for r in parsed.get("reasons", []):
            if r and r not in merged_reasons:
                merged_reasons.append(r)
        for r in rule_result.get("reasons", []):
            if r and r not in merged_reasons:
                merged_reasons.append(r)

        verdict = "pass" if bounded_score >= 70 else ("reject" if bounded_score < 40 else "revise")
        return {
            "score": bounded_score,
            "verdict": verdict,
            "reasons": merged_reasons[:4],
            "passed": bounded_score >= 70 and verdict == "pass",
            "fallback": False,
            "subscores": rule_result.get("subscores"),
            "rule_score": rule_score,
            "raw_llm_score": raw_llm_score
        }

    # Parse failure or LLM unavailable falls back to rules
    return rule_result


def format_review(score: int, reasons: List[str], verdict: str = "pass", fallback: bool = False) -> str:
    """Format review summary string for storage and display."""
    score = max(0, min(100, score))
    top_reasons = "; ".join(reasons[:2]) if reasons else "Looks good"
    v_str = verdict.upper() if not fallback else "unavailable (checklist only)"
    res = f"score {score}/100 | Checker: {score}/100 | CHECKER: {v_str}"
    if not fallback:
        res += f" (AI {score})"
    if reasons:
        res += f" | top: {top_reasons}"
        res += f" | issues: " + "; ".join(reasons[:5])
    return res


def _raw_review_score(review_text: Optional[str]) -> Optional[int]:
    """Prefer the Checker score; retain the raw value for approval eligibility checks."""
    if not isinstance(review_text, str):
        return None
    m = re.search(r"Checker:\s*([+-]?\d+)\s*/\s*100\b", review_text, re.IGNORECASE)
    if not m:
        m = re.search(r"(?<![\d.])([+-]?\d+)\s*/\s*100\b", review_text, re.IGNORECASE)
    try:
        return int(m.group(1)) if m else None
    except ValueError:
        return None


def parse_review(review_text: Optional[str]) -> Tuple[Optional[int], List[str]]:
    """Extract score and top reasons from review text string."""
    if not review_text or not isinstance(review_text, str):
        return None, []

    # Old stored Scout reviews may contain a preference bonus above 100.
    raw_score = _raw_review_score(review_text)
    score = max(0, min(100, raw_score)) if raw_score is not None else None

    # Look for top reasons after '| top:' or '| issues:'
    reasons = []
    top_m = re.search(r"\|\s*(?:top|issues):\s*([^|]+)", review_text, re.IGNORECASE)
    if top_m:
        raw_reasons = top_m.group(1).split(";")
        reasons = [r.strip() for r in raw_reasons if r.strip()]

    return score, reasons


def review_passes(review_text: Optional[str]) -> bool:
    """A high checklist score never overrides a rejected or unavailable checker."""
    score = _raw_review_score(review_text)
    verdict = re.search(r"CHECKER:\s*(PASS|APPROVE|REVISE|REJECT|UNAVAILABLE)\b",
                        review_text or "", re.IGNORECASE)
    return bool(score is not None and 70 <= score <= 100 and verdict
                and verdict[1].upper() in ("PASS", "APPROVE"))
