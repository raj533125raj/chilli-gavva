"""Checker agent: product quality review before Telegram approval.

Evaluates product drafts against rules from docs:
  1. Listing promises only real features
  2. Clear audience
  3. Why it beats a free template
  4. No money claims or earnings promises
  5. No unapproved product ideas leaked publicly

Gives a score 0-100 plus a short list of reasons.
Drafts under 70 go back to the writer automatically with the reasons (max 2 retries).

Uses llm_router for AI calls with a rule-based fallback when AI is unavailable.
"""

import json
import re
from typing import Dict, List, Any, Optional, Tuple

import llm_router

_AUDIENCE_WORDS = (
    "students", "freelancers", "parents", "teachers", "creators", "owners",
    "workers", "nurses", "couples", "families", "designers", "writers",
    "developers", "coaches", "consultants", "beginners", "professionals",
    "teams", "moms", "dads", "renters", "planners", "entrepreneurs", "sellers",
    "artists", "readers", "travelers", "graduates", "job seekers", "creators",
    "user", "people", "you", "buyer", "small creators"
)

_BEATS_FREE_PATTERNS = re.compile(
    r"\b(unlike|instead of|not just|what makes|compared (?:to|with)|no other|"
    r"(?:built|designed|made) specifically|better than|over a free|free template|"
    r"pre-?filled|automated|dashboard|formulas|step[- ]by[- ]step|ready[- ]made|"
    r"simple|honest|tool|browser|seconds|no account|no sign-up|benefit)\b",
    re.IGNORECASE
)

_MONEY_CLAIM_PATTERNS = re.compile(
    r"\b(guaranteed|get rich|passive income|make money|earn|financial freedom|"
    r"income promise|revenue guarantee|fast cash|zero risk earnings)\b",
    re.IGNORECASE
)

_LEAK_PATTERNS = re.compile(
    r"\b(confidential|internal only|secret strategy|unreleased|do not share|"
    r"private key|api_key|token=|password=)\b",
    re.IGNORECASE
)

_PRODUCT_STRUCTURE_LABELS = ("what's inside", "whats inside", "features", "structure", "contents", "benefit")


def rule_based_fallback(draft_text: str, topic: str = "") -> Dict[str, Any]:
    """Rule-based quality check when AI router is unavailable. Never raises."""
    text = draft_text or ""
    lower = text.lower()
    score = 100
    reasons: List[str] = []

    # 1. Real features / structure check
    has_structure = any(label in lower for label in _PRODUCT_STRUCTURE_LABELS) or len(text.strip()) >= 80
    if not has_structure:
        score -= 20
        reasons.append("Missing detailed product features or structure")

    # 2. Clear audience check
    has_audience = "buyer" in lower or any(w in lower for w in _AUDIENCE_WORDS)
    if not has_audience:
        score -= 20
        reasons.append("Target audience is unclear")

    # 3. Why it beats a free template
    beats_free = bool(_BEATS_FREE_PATTERNS.search(lower))
    if not beats_free:
        score -= 20
        reasons.append("Does not explain why it beats a free template")

    # 4. No money claims
    money_claims = _MONEY_CLAIM_PATTERNS.findall(lower)
    if money_claims:
        score -= 35
        reasons.append("Contains prohibited financial or earnings promises")

    # 5. No unapproved leaks
    leaks = _LEAK_PATTERNS.findall(lower)
    if leaks:
        score -= 35
        reasons.append("Contains unapproved internal information or leak")

    score = max(0, min(100, score))
    if not reasons and score == 100:
        reasons = ["Clear audience and features", "Explains value over free template"]

    verdict = "pass" if score >= 70 else ("reject" if score < 40 or money_claims or leaks else "revise")

    return {
        "score": score,
        "verdict": verdict,
        "reasons": reasons[:3],
        "passed": score >= 70 and verdict == "pass",
        "fallback": True
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


def check_draft(draft_text: str, topic: str = "", ctx: Optional[Dict[str, Any]] = None, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Check a product draft using AI router with rule-based fallback.

    Returns dict with keys: 'score' (int 0-100), 'verdict' (str), 'reasons' (list of str), 'passed' (bool), 'fallback' (bool).
    """
    if not draft_text:
        return {
            "score": 0,
            "verdict": "reject",
            "reasons": ["Empty draft content"],
            "passed": False,
            "fallback": True
        }

    llm_fn = None
    if ctx and callable(ctx.get("llm")):
        llm_fn = ctx["llm"]

    import improver_loop
    rules_block = improver_loop.get_active_rules_block("checker", db_path=db_path)

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
        return parsed

    # Fallback to rule-based evaluation when AI unavailable or malformed
    fb = rule_based_fallback(draft_text, topic=topic)
    return fb


def format_review(score: int, reasons: List[str], verdict: str = "pass", fallback: bool = False) -> str:
    """Format review summary string for storage and display."""
    top_reasons = "; ".join(reasons[:2]) if reasons else "Looks good"
    v_str = verdict.upper() if not fallback else "unavailable (checklist only)"
    res = f"score {score}/100 | Checker: {score}/100 | CHECKER: {v_str}"
    if not fallback:
        res += f" (AI {score})"
    if reasons:
        res += f" | top: {top_reasons}"
        res += f" | issues: " + "; ".join(reasons[:5])
    return res


def parse_review(review_text: Optional[str]) -> Tuple[Optional[int], List[str]]:
    """Extract score and top reasons from review text string."""
    if not review_text or not isinstance(review_text, str):
        return None, []

    # Look for 'Checker: 82/100' or 'score 82/100' or '82/100'
    # The checklist and the checker can have different scores. Prefer the checker.
    m = re.search(r"Checker:\s*(\d{1,3})\s*/\s*100", review_text, re.IGNORECASE)
    if not m:
        m = re.search(r"(?:score\s*)?(\d{1,3})\s*/\s*100", review_text, re.IGNORECASE)
    score = int(m.group(1)) if m else None

    # Look for top reasons after '| top:' or '| issues:'
    reasons = []
    top_m = re.search(r"\|\s*(?:top|issues):\s*([^|]+)", review_text, re.IGNORECASE)
    if top_m:
        raw_reasons = top_m.group(1).split(";")
        reasons = [r.strip() for r in raw_reasons if r.strip()]

    return score, reasons


def review_passes(review_text: Optional[str]) -> bool:
    """A high checklist score never overrides a rejected or unavailable checker."""
    score, _ = parse_review(review_text)
    verdict = re.search(r"CHECKER:\s*(PASS|APPROVE|REVISE|REJECT|UNAVAILABLE)\b",
                        review_text or "", re.IGNORECASE)
    return bool(score is not None and 70 <= score <= 100 and verdict
                and verdict[1].upper() in ("PASS", "APPROVE"))
