"""Product Council: Multi-stage debate pipeline between draft creation and approval queue.

Stages:
  1. Writer: Existing draft payload.
  2. Critic: Identifies concrete weaknesses with evidence from the draft.
  3. Buyer's Voice: Evaluates why a customer would pick this over a free template and rates conviction (0-10).
  4. Fixer: Rewrites only the weak parts of the draft.
  5. Judge: Compares before/after quality against checker score and learned rules (improver_loop) to decide PASS or RETRY (max 3 rounds).

Each stage uses llm_router with a rule-based fallback so execution never blocks.
All debate decisions are logged in SQLite database table `council_debates`.
Formats a 3-line summary for Telegram approval cards.
"""

import datetime
import json
import re
from typing import Dict, List, Any, Optional, Tuple

import cubes
import guardrails
import improver_loop
import llm_router
import qa_checklist
import rest_window


def connect(db_path=None):
    conn = guardrails.connect(db_path)
    conn.execute("""CREATE TABLE IF NOT EXISTS council_debates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        approval_id INTEGER,
        round_num INTEGER,
        stage TEXT,
        verdict TEXT,
        reasons TEXT,
        conviction INTEGER,
        top_fix TEXT,
        ts TEXT
    )""")
    conn.commit()
    return conn


def log_debate(approval_id: Optional[int], round_num: int, stage: str, verdict: str,
               reasons: str, conviction: Optional[int] = None,
               top_fix: Optional[str] = None, db_path=None):
    if approval_id is None:
        return
    conn = connect(db_path)
    now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO council_debates (approval_id, round_num, stage, verdict, reasons, conviction, top_fix, ts) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (approval_id, round_num, stage, verdict, str(reasons or ""), conviction, top_fix, now_str)
    )
    conn.commit()
    conn.close()


def _ask_stage(prompt: str, ctx=None, db_path=None) -> Optional[str]:
    if ctx and callable(ctx.get("llm")):
        try:
            return ctx["llm"](prompt, size="large")
        except Exception:
            try:
                return ctx["llm"](prompt)
            except Exception:
                return None
    try:
        return llm_router.chat(prompt, db_path=db_path, size="large")
    except Exception:
        return None


def _parse_json(text: str) -> Optional[Dict[str, Any]]:
    if not text or not isinstance(text, str):
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return None


# --- Stage 2: Critic ---
CRITIC_PROMPT = """You are the Product Council Critic agent.
Identify concrete weaknesses in the given draft with direct evidence or quotes from the text.
Look for vague claims, missing features, missing target buyer, or unclear structure.

Topic: {topic}

Draft:
{draft}

Reply with ONLY this JSON object:
{{"weaknesses": ["weakness 1 with evidence", "weakness 2 with evidence"], "verdict": "FLAGGED" | "OK"}}"""


def critic_fallback(draft_text: str, topic: str = "") -> Dict[str, Any]:
    text = draft_text or ""
    lower = text.lower()
    weaknesses = []

    if len(text.strip()) < 300:
        weaknesses.append("Draft is too short and lacks detailed content")
    if "buyer" not in lower:
        weaknesses.append("Target buyer persona is not clearly defined")
    if "what's inside" not in lower and "whats inside" not in lower and "features" not in lower:
        weaknesses.append("Missing explicit product features or structure section")
    if "search tags" not in lower:
        weaknesses.append("Missing search tags section")

    verdict = "FLAGGED" if weaknesses else "OK"
    if not weaknesses:
        weaknesses = ["No critical weaknesses identified"]
    return {"weaknesses": weaknesses, "verdict": verdict, "fallback": True}


def run_critic(draft_text: str, topic: str = "", ctx=None, db_path=None, allow_ai: bool = True) -> Tuple[Dict[str, Any], int]:
    if not allow_ai:
        return critic_fallback(draft_text, topic), 0

    prompt = CRITIC_PROMPT.format(topic=topic or "Digital Product", draft=(draft_text or "")[:5000])
    raw = _ask_stage(prompt, ctx=ctx, db_path=db_path)
    parsed = _parse_json(raw)
    if parsed and "weaknesses" in parsed:
        weaknesses = parsed.get("weaknesses") or []
        if not isinstance(weaknesses, list):
            weaknesses = [str(weaknesses)]
        verdict = str(parsed.get("verdict", "OK")).upper()
        if verdict not in ("FLAGGED", "OK"):
            verdict = "FLAGGED" if weaknesses else "OK"
        return {"weaknesses": [str(w).strip()[:150] for w in weaknesses], "verdict": verdict, "fallback": False}, 1

    return critic_fallback(draft_text, topic), (1 if raw is not None else 0)


# --- Stage 3: Buyer's Voice ---
BUYER_PROMPT = """You are the Buyer's Voice agent on the Product Council.
Answer: 'Why would a customer pick this over a free template?'
Rate your purchase conviction on an integer scale from 0 to 10.

Topic: {topic}

Draft:
{draft}

Reply with ONLY this JSON object:
{{"answer": "short statement explaining value over free template", "conviction": <0-10>}}"""


def buyer_fallback(draft_text: str, topic: str = "") -> Dict[str, Any]:
    text = draft_text or ""
    lower = text.lower()
    value_terms = ["automated", "dashboard", "formulas", "step-by-step", "ready-made", "tool", "workflow", "custom"]
    matches = sum(1 for term in value_terms if term in lower)
    conviction = min(10, max(1, 4 + matches))

    if matches >= 2:
        answer = "Provides structured workflows and automated tools beyond basic free templates."
    else:
        answer = "Offers a basic template layout; needs clearer unique value proposition."

    return {"answer": answer, "conviction": conviction, "fallback": True}


def run_buyer(draft_text: str, topic: str = "", ctx=None, db_path=None, allow_ai: bool = True) -> Tuple[Dict[str, Any], int]:
    if not allow_ai:
        return buyer_fallback(draft_text, topic), 0

    prompt = BUYER_PROMPT.format(topic=topic or "Digital Product", draft=(draft_text or "")[:5000])
    raw = _ask_stage(prompt, ctx=ctx, db_path=db_path)
    parsed = _parse_json(raw)
    if parsed and "conviction" in parsed:
        conv = parsed.get("conviction")
        if isinstance(conv, (int, float)) and not isinstance(conv, bool):
            conv_int = max(0, min(10, int(round(conv))))
            ans = str(parsed.get("answer", "")).strip()[:200] or "Clear value proposition provided."
            return {"answer": ans, "conviction": conv_int, "fallback": False}, 1

    return buyer_fallback(draft_text, topic), (1 if raw is not None else 0)


# --- Stage 4: Fixer ---
FIXER_PROMPT = """You are the Fixer agent on the Product Council.
Rewrite ONLY the weak parts of the draft below based on Critic weaknesses and Buyer feedback.
Keep existing strong content intact and preserve labelled sections (PRODUCT NAME, BUYER, WHAT'S INSIDE, HOW TO USE, LISTING TITLE, LISTING DESCRIPTION, SEARCH TAGS).

Topic: {topic}
Weaknesses:
{weaknesses}

Buyer Feedback:
{buyer_feedback}

Draft:
{draft}"""


def run_fixer(draft_text: str, topic: str, weaknesses: List[str], buyer_feedback: str,
              ctx=None, db_path=None, allow_ai: bool = True) -> Tuple[str, int]:
    if not allow_ai:
        return draft_text, 0

    weak_str = "\n".join(f"- {w}" for w in weaknesses) if weaknesses else "- Improve clarity and completeness"
    prompt = FIXER_PROMPT.format(topic=topic or "Digital Product", weaknesses=weak_str,
                                  buyer_feedback=buyer_feedback or "N/A", draft=(draft_text or "")[:5000])
    raw = _ask_stage(prompt, ctx=ctx, db_path=db_path)
    if raw and len(raw.strip()) >= 200:
        return raw.strip(), 1

    return draft_text, (1 if raw is not None else 0)


# --- Stage 5: Judge ---
JUDGE_PROMPT = """You are the Judge agent on the Product Council.
Compare the draft quality against QA score ({score}/100), Buyer conviction ({conviction}/10), and learned rules:
{learned_rules}

Topic: {topic}
Critic Weaknesses: {weaknesses}
Buyer Answer: {buyer_answer}

Current Draft:
{draft}

Decide PASS (ready for human review) or RETRY (needs another round of fixes).
Reply with ONLY this JSON object:
{{"verdict": "PASS" | "RETRY", "reasons": ["short reason 1"], "top_fix": "short description of top fix made"}}"""


def judge_fallback(draft_text: str, topic: str, qa_score: int, conviction: int,
                   weaknesses: List[str], db_path=None) -> Dict[str, Any]:
    reasons = []
    if qa_score >= 70 and conviction >= 6:
        verdict = "PASS"
        reasons.append(f"QA score {qa_score}/100 and buyer conviction {conviction}/10 meet quality threshold")
        top_fix = "Improved structure and buyer value proposition"
    else:
        verdict = "RETRY"
        if qa_score < 70:
            reasons.append(f"QA score {qa_score}/100 below 70 threshold")
        if conviction < 6:
            reasons.append(f"Buyer conviction {conviction}/10 below 6 threshold")
        if weaknesses:
            reasons.extend(weaknesses[:2])
        top_fix = "Refined product details and target buyer alignment"

    return {"verdict": verdict, "reasons": reasons[:3], "top_fix": top_fix, "fallback": True}


def run_judge(draft_text: str, topic: str, qa_score: int, conviction: int,
              weaknesses: List[str], buyer_answer: str, ctx=None, db_path=None,
              allow_ai: bool = True) -> Tuple[Dict[str, Any], int]:
    learned_rules = improver_loop.get_active_rules_block("checker", db_path=db_path) or "Follow quality guidelines."
    if not allow_ai:
        return judge_fallback(draft_text, topic, qa_score, conviction, weaknesses, db_path=db_path), 0

    weak_str = "; ".join(weaknesses[:3]) if weaknesses else "None"
    prompt = JUDGE_PROMPT.format(score=qa_score, conviction=conviction, learned_rules=learned_rules,
                                  topic=topic or "Digital Product", weaknesses=weak_str,
                                  buyer_answer=buyer_answer or "N/A", draft=(draft_text or "")[:5000])
    raw = _ask_stage(prompt, ctx=ctx, db_path=db_path)
    parsed = _parse_json(raw)
    if parsed and "verdict" in parsed:
        verdict = str(parsed.get("verdict", "")).strip().upper()
        if verdict in ("PASS", "RETRY"):
            reasons = parsed.get("reasons") or []
            if not isinstance(reasons, list):
                reasons = [str(reasons)]
            top_fix = str(parsed.get("top_fix", "")).strip()[:100] or "Updated product details"
            return {"verdict": verdict, "reasons": [str(r).strip()[:150] for r in reasons if str(r).strip()][:3],
                    "top_fix": top_fix, "fallback": False}, 1

    return judge_fallback(draft_text, topic, qa_score, conviction, weaknesses, db_path=db_path), (1 if raw is not None else 0)


# --- Council Pipeline Execution ---
def run_council(approval_id: Optional[int], draft_text: str, topic: str = "",
                ctx: Optional[Dict[str, Any]] = None, db_path: Optional[str] = None,
                max_rounds: int = 3, max_ai_calls: int = 8) -> Dict[str, Any]:
    if guardrails.is_killed() or rest_window.in_rest_window() or not cubes.is_kind_enabled("review", db_path=db_path) or not guardrails.can_call_llm(db_path):
        return {
            "status": "skipped",
            "reason": "daily_cap_or_killed_or_paused",
            "verdict": "SKIPPED",
            "draft_text": draft_text
        }

    current_draft = draft_text or ""
    ai_calls = 0
    passed = False
    final_round = 1
    last_conviction = 5
    last_top_fix = "Initial draft review"
    last_reasons = []

    for r in range(1, max_rounds + 1):
        final_round = r

        # 1. Critic
        critic_res, c_calls = run_critic(current_draft, topic, ctx=ctx, db_path=db_path,
                                         allow_ai=(ai_calls < max_ai_calls))
        ai_calls += c_calls
        weaknesses = critic_res.get("weaknesses") or []
        log_debate(approval_id, r, "critic", critic_res.get("verdict", "OK"),
                   "; ".join(weaknesses[:3]), db_path=db_path)

        # 2. Buyer's Voice
        buyer_res, b_calls = run_buyer(current_draft, topic, ctx=ctx, db_path=db_path,
                                       allow_ai=(ai_calls < max_ai_calls))
        ai_calls += b_calls
        last_conviction = buyer_res.get("conviction", 5)
        buyer_answer = buyer_res.get("answer", "")
        log_debate(approval_id, r, "buyer_voice", f"RATED_{last_conviction}",
                   buyer_answer, conviction=last_conviction, db_path=db_path)

        # 3. Fixer
        needs_fix = (critic_res.get("verdict") == "FLAGGED" or last_conviction < 7 or len(weaknesses) > 0)
        if needs_fix:
            fixed_draft, f_calls = run_fixer(current_draft, topic, weaknesses, buyer_answer,
                                             ctx=ctx, db_path=db_path, allow_ai=(ai_calls < max_ai_calls))
            ai_calls += f_calls
            if fixed_draft and fixed_draft.strip() != current_draft.strip():
                current_draft = fixed_draft
                if approval_id:
                    conn = guardrails.connect(db_path)
                    conn.execute("UPDATE approvals SET payload = ? WHERE id = ? AND status = 'pending'",
                                 (current_draft, approval_id))
                    conn.commit()
                    conn.close()
                log_debate(approval_id, r, "fixer", "REWRITTEN", "Rewrote weak parts of draft", db_path=db_path)
            else:
                log_debate(approval_id, r, "fixer", "UNCHANGED", "Draft unchanged", db_path=db_path)
        else:
            log_debate(approval_id, r, "fixer", "SKIPPED", "No major fixes required", db_path=db_path)

        # 4. Judge
        qa_res = qa_checklist.score(current_draft)
        qa_score = qa_res.get("score", 70)

        judge_res, j_calls = run_judge(current_draft, topic, qa_score, last_conviction,
                                       weaknesses, buyer_answer, ctx=ctx, db_path=db_path,
                                       allow_ai=(ai_calls < max_ai_calls))
        ai_calls += j_calls

        j_verdict = judge_res.get("verdict", "RETRY").upper()
        last_reasons = judge_res.get("reasons") or ["Quality evaluation complete"]
        if judge_res.get("top_fix"):
            last_top_fix = judge_res["top_fix"]

        log_debate(approval_id, r, "judge", j_verdict, "; ".join(last_reasons[:3]),
                   conviction=last_conviction, top_fix=last_top_fix, db_path=db_path)

        if j_verdict == "PASS":
            passed = True
            break

    final_verdict = "PASS" if passed else "NEEDS YOU"
    log_debate(approval_id, final_round, "council_summary", final_verdict,
               "; ".join(last_reasons[:3]), conviction=last_conviction,
               top_fix=last_top_fix, db_path=db_path)

    return {
        "status": "completed",
        "verdict": final_verdict,
        "rounds": final_round,
        "conviction": last_conviction,
        "top_fix": last_top_fix,
        "reasons": last_reasons,
        "draft_text": current_draft,
        "ai_calls": ai_calls
    }


def get_council_summary(approval_id: int, db_path=None) -> Optional[Dict[str, Any]]:
    if not approval_id:
        return None
    conn = connect(db_path)
    row = conn.execute(
        "SELECT verdict, round_num, conviction, top_fix, reasons FROM council_debates "
        "WHERE approval_id = ? AND stage = 'council_summary' ORDER BY id DESC LIMIT 1",
        (approval_id,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {
        "verdict": row[0],
        "rounds": row[1],
        "conviction": row[2] if row[2] is not None else 0,
        "top_fix": row[3] or "none",
        "reasons": row[4] or ""
    }


def get_council_card_lines(approval_id: int, db_path=None) -> List[str]:
    summary = get_council_summary(approval_id, db_path=db_path)
    if not summary:
        return []

    verdict = summary["verdict"]
    rounds = summary["rounds"]
    conviction = summary["conviction"]
    top_fix = summary["top_fix"]
    reasons = summary["reasons"]

    line1 = f"Council: {verdict} after {rounds} round(s); buyer conviction {conviction}/10; top fix: {top_fix}"
    line2 = f"Buyer conviction: {conviction}/10 | Top fix: {top_fix}"
    line3 = f"Reasons: {reasons[:120]}" if reasons else "Reasons: checked against quality guidelines"

    return [line1, line2, line3]
