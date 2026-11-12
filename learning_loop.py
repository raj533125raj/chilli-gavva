"""learning_loop.py — BINNY-W3B: Learn from owner decisions, truth-checks, and sales.

Learns preferences per niche, format, and audience based on:
1. Approve / reject history from approvals and scout_ideas
2. Reject reasons (e.g., 'too generic', 'duplicate', 'weak niche', etc.)
3. Truth-check results from truth_checks table (verdict pass/ready vs needs_fix/fail)
4. Sales counts from sales_ledger (if present, safely guarded)

Key properties:
- Weights are bounded in [0.5, 1.5].
- Time decay over 60 days (linear decay: weight = 1 - age_days / 60).
- Minimum samples threshold (MIN_SAMPLES = 3 by default) before adjusting away from neutral 1.0.
- Bayesian / Laplace smoothing towards 1.0 so small sample sizes do not cause erratic swings.
- Tenglish "why" explanations ("Budget sheets approve rate ekkuva", "Students audience reject rate ekkuva").
- learning_summary(db) generates a concise operational summary without money amounts or secrets.
"""

import datetime
import math
import os
import re
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

import guardrails

# Constants
LOOKBACK_DAYS = 60
MIN_SAMPLES = 3
MIN_WEIGHT = 0.5
MAX_WEIGHT = 1.5
BASE_WEIGHT = 1.0
WEIGHT_RANGE = (0.5, 1.5)

# Standard dimensions recognized
AUDIENCES = (
    "teachers",
    "students",
    "exam takers",
    "small shop owners",
    "home bakers",
    "fitness beginners",
    "new parents",
    "freelancers",
    "job seekers",
    "wedding planners",
    "Telugu-speaking families",
    "retirees",
)

FORMATS = (
    "printable planner",
    "tracker sheet",
    "workbook",
    "checklist",
    "worksheet set",
    "budget sheet",
    "resume pack",
    "wall art",
    "slides",
    "email signature",
    "event planner",
)

DEFAULT_NICHES = {
    "budget": "niche_budget_sheets",
    "sheet": "niche_budget_sheets",
    "money": "niche_budget_sheets",
    "freelance": "niche_freelancer_trackers",
    "invoice": "niche_freelancer_trackers",
    "tracker": "niche_freelancer_trackers",
    "adhd": "niche_adhd_planners",
    "focus": "niche_adhd_planners",
    "planner": "niche_adhd_planners",
    "2027": "niche_planners_2027",
    "academic": "niche_planners_2027",
    "teacher": "niche_planners_2027",
    "resume": "niche_resume_bundle",
    "job": "niche_resume_bundle",
    "notion": "niche_notion_templates",
}


def _parse_iso(ts_str: Optional[str]) -> Optional[datetime.datetime]:
    if not ts_str:
        return None
    try:
        clean = ts_str.strip().replace("Z", "+00:00")
        dt = datetime.datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt
    except Exception:
        return None


def calculate_decay_weight(event_dt: Optional[datetime.datetime],
                           now: Optional[datetime.datetime] = None,
                           lookback_days: float = LOOKBACK_DAYS) -> float:
    """Calculate time decay weight in [0.0, 1.0] over lookback_days (default 60).

    Recent events have weight close to 1.0; events older than lookback_days have weight 0.0.
    Uses smooth linear decay: max(0.0, 1.0 - (age_days / lookback_days)).
    """
    if not event_dt:
        return 0.5  # default if timestamp is unknown
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if event_dt.tzinfo is None:
        event_dt = event_dt.replace(tzinfo=datetime.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)

    age_seconds = (now - event_dt).total_seconds()
    if age_seconds < 0:
        # Event is in the future or clock skew -> treat as current
        age_seconds = 0
    age_days = age_seconds / 86400.0
    if age_days >= lookback_days:
        return 0.0
    return max(0.0, min(1.0, 1.0 - (age_days / lookback_days)))


def detect_niche(text: str) -> Optional[str]:
    """Detect niche identifier from title/payload."""
    if not text:
        return None
    m = re.search(r"\b(niche_[a-z0-9_]+)\b", text)
    if m:
        return m.group(1)
    lower = text.lower()
    for kw, n_name in DEFAULT_NICHES.items():
        if kw in lower:
            return n_name
    return None


def detect_format(text: str) -> Optional[str]:
    """Detect product format from text."""
    if not text:
        return None
    lower = text.lower()
    for fmt in FORMATS:
        if fmt in lower:
            return fmt
    # Keyword fallbacks
    kw_map = {
        "budget sheet": "budget sheet",
        "budget": "budget sheet",
        "planner": "printable planner",
        "tracker sheet": "tracker sheet",
        "tracker": "tracker sheet",
        "workbook": "workbook",
        "checklist": "checklist",
        "worksheet set": "worksheet set",
        "worksheet": "worksheet set",
        "resume pack": "resume pack",
        "resume": "resume pack",
        "wall art": "wall art",
        "poster": "wall art",
        "email signature": "email signature",
        "signature": "email signature",
        "event planner": "event planner",
        "wedding planner": "event planner",
    }
    for kw, fmt in kw_map.items():
        if re.search(r"\b" + re.escape(kw) + r"\b", lower):
            return fmt
    return None


def detect_audience(text: str) -> Optional[str]:
    """Detect audience persona from text."""
    if not text:
        return None
    lower = text.lower()
    for aud in AUDIENCES:
        if aud.lower() in lower:
            return aud
    aliases = {
        "teacher": "teachers",
        "educator": "teachers",
        "student": "students",
        "college": "students",
        "exam": "exam takers",
        "test": "exam takers",
        "shop": "small shop owners",
        "store": "small shop owners",
        "retail": "small shop owners",
        "baker": "home bakers",
        "bakery": "home bakers",
        "fitness": "fitness beginners",
        "gym": "fitness beginners",
        "workout": "fitness beginners",
        "parent": "new parents",
        "baby": "new parents",
        "infant": "new parents",
        "freelance": "freelancers",
        "client": "freelancers",
        "job": "job seekers",
        "career": "job seekers",
        "resume": "job seekers",
        "wedding": "wedding planners",
        "ceremony": "wedding planners",
        "telugu": "Telugu-speaking families",
        "retire": "retirees",
        "senior": "retirees",
    }
    for kw, aud in aliases.items():
        if re.search(r"\b" + re.escape(kw) + r"\b", lower):
            return aud
    return None


def _get_sales_counts(db_path: Optional[str] = None) -> Dict[str, int]:
    """Query sales counts if sales_ledger module exists (safe guarded import).

    Returns a dict mapping pack or niche/identifier -> sales count.
    """
    try:
        import sales_ledger  # type: ignore
        if hasattr(sales_ledger, "get_sales_summary"):
            return sales_ledger.get_sales_summary(db_path=db_path)
        if hasattr(sales_ledger, "get_pack_sales"):
            return sales_ledger.get_pack_sales(db_path=db_path)
    except (ImportError, Exception):
        pass

    # Also check if a sales_records or sales table exists in SQLite
    sales_map: Dict[str, int] = {}
    try:
        conn = guardrails.connect(db_path)
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        if "sales_ledger" in tables:
            rows = conn.execute("SELECT item_name, count FROM sales_ledger").fetchall()
            for item, cnt in rows:
                if item:
                    sales_map[str(item).lower()] = sales_map.get(str(item).lower(), 0) + int(cnt or 1)
        elif "sales" in tables:
            rows = conn.execute("SELECT item, COUNT(*) FROM sales GROUP BY item").fetchall()
            for item, cnt in rows:
                if item:
                    sales_map[str(item).lower()] = sales_map.get(str(item).lower(), 0) + int(cnt or 1)
        conn.close()
    except Exception:
        pass
    return sales_map


def extract_events(db_path: Optional[str] = None,
                   now: Optional[datetime.datetime] = None,
                   lookback_days: int = LOOKBACK_DAYS) -> List[Dict[str, Any]]:
    """Extract historical decision, truth check, and sales events within lookback_days."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    cutoff = (now - datetime.timedelta(days=lookback_days)).isoformat()
    events: List[Dict[str, Any]] = []

    try:
        conn = guardrails.connect(db_path)
    except Exception:
        return events

    try:
        # 1. Approvals table
        cols = [r[1] for r in conn.execute("PRAGMA table_info(approvals)").fetchall()]
        has_decided_ts = "decided_ts" in cols
        has_reason = "reason" in cols

        select_cols = ["id", "kind", "title", "payload", "status", "ts"]
        if has_decided_ts:
            select_cols.append("decided_ts")
        if has_reason:
            select_cols.append("reason")

        query = f"SELECT {', '.join(select_cols)} FROM approvals WHERE status IN ('approved', 'rejected')"
        rows = conn.execute(query).fetchall()

        for row in rows:
            rec = dict(zip(select_cols, row))
            event_ts_str = rec.get("decided_ts") or rec.get("ts")
            dt = _parse_iso(event_ts_str)
            if not dt or dt < (now - datetime.timedelta(days=lookback_days)):
                continue

            text_full = f"{rec.get('title', '')} {rec.get('payload', '')}"
            niche = detect_niche(text_full)
            fmt = detect_format(text_full)
            aud = detect_audience(text_full)
            reason = rec.get("reason") if has_reason else None

            events.append({
                "type": "decision",
                "source": "approvals",
                "id": rec.get("id"),
                "status": rec.get("status"),
                "reason": reason,
                "niche": niche,
                "format": fmt,
                "audience": aud,
                "dt": dt,
                "text": text_full,
            })

        # 2. Scout ideas table (synced or resolved ones)
        has_scout = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='scout_ideas'").fetchone()
        if has_scout:
            s_cols = [r[1] for r in conn.execute("PRAGMA table_info(scout_ideas)").fetchall()]
            s_select = ["id", "ts", "idea", "status", "niche"]
            if "audience" in s_cols:
                s_select.append("audience")
            if "format" in s_cols:
                s_select.append("format")

            s_query = f"SELECT {', '.join(s_select)} FROM scout_ideas WHERE status IN ('approved', 'rejected') AND ts >= ?"
            s_rows = conn.execute(s_query, (cutoff,)).fetchall()

            for s_row in s_rows:
                s_rec = dict(zip(s_select, s_row))
                dt = _parse_iso(s_rec.get("ts"))
                if not dt or dt < (now - datetime.timedelta(days=lookback_days)):
                    continue

                idea_text = s_rec.get("idea", "")
                niche = s_rec.get("niche") or detect_niche(idea_text)
                aud = s_rec.get("audience") or detect_audience(idea_text)
                fmt = s_rec.get("format") or detect_format(idea_text)

                events.append({
                    "type": "decision",
                    "source": "scout_ideas",
                    "id": s_rec.get("id"),
                    "status": s_rec.get("status"),
                    "reason": None,
                    "niche": niche,
                    "format": fmt,
                    "audience": aud,
                    "dt": dt,
                    "text": idea_text,
                })

        # 3. Truth checks table
        has_tc = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='truth_checks'").fetchone()
        if has_tc:
            tc_cols = [r[1] for r in conn.execute("PRAGMA table_info(truth_checks)").fetchall()]
            has_score = "score" in tc_cols
            tc_select = ["approval_id", "verdict"]
            if has_score:
                tc_select.append("score")
            tc_rows = conn.execute(f"SELECT {', '.join(tc_select)} FROM truth_checks").fetchall()

            for tc_row in tc_rows:
                app_id = tc_row[0]
                verdict = str(tc_row[1] or "").lower()
                # Lookup approval record
                app = conn.execute("SELECT title, payload, ts FROM approvals WHERE id = ?", (app_id,)).fetchone()
                if not app:
                    continue
                dt = _parse_iso(app[2])
                if not dt or dt < (now - datetime.timedelta(days=lookback_days)):
                    continue

                text_full = f"{app[0] or ''} {app[1] or ''}"
                events.append({
                    "type": "truth_check",
                    "verdict": verdict,  # ready / pass vs needs_fix / fail
                    "niche": detect_niche(text_full),
                    "format": detect_format(text_full),
                    "audience": detect_audience(text_full),
                    "dt": dt,
                    "text": text_full,
                })

        conn.close()
    except Exception:
        try:
            conn.close()
        except Exception:
            pass

    # 4. Sales events from sales_ledger (if available)
    sales = _get_sales_counts(db_path)
    for key, count in sales.items():
        if count <= 0:
            continue
        niche = detect_niche(key)
        fmt = detect_format(key)
        aud = detect_audience(key)
        events.append({
            "type": "sales",
            "count": count,
            "niche": niche,
            "format": fmt,
            "audience": aud,
            "dt": now,  # current window
            "text": key,
        })

    return events


def calculate_dimension_stats(dimension: str,
                              events: List[Dict[str, Any]],
                              now: Optional[datetime.datetime] = None) -> Dict[str, Dict[str, Any]]:
    """Aggregate weighted signals for a dimension ('niche', 'format', 'audience').

    Applies time decay:
    - Approve: +1.0 * decay
    - Reject: -1.0 * decay
      If reject reason is severe ('too generic', 'weak niche', 'duplicate', 'promises too much'), adds extra penalty -0.25 * decay
    - Truth check pass/ready: +0.75 * decay
    - Truth check fail/needs_fix: -0.75 * decay
    - Sales: +0.5 per sale
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    stats: Dict[str, Dict[str, Any]] = {}

    for ev in events:
        val = ev.get(dimension)
        if not val:
            continue
        val = str(val).strip()
        if not val:
            continue

        if val not in stats:
            stats[val] = {
                "approves": 0,
                "rejects": 0,
                "tc_passes": 0,
                "tc_fails": 0,
                "sales": 0,
                "sample_count": 0,
                "decayed_positives": 0.0,
                "decayed_negatives": 0.0,
                "reasons": {},
            }

        s = stats[val]
        decay = calculate_decay_weight(ev.get("dt"), now=now)
        ev_type = ev.get("type")

        if ev_type == "decision":
            s["sample_count"] += 1
            st = ev.get("status")
            if st == "approved":
                s["approves"] += 1
                s["decayed_positives"] += 1.0 * decay
            elif st == "rejected":
                s["rejects"] += 1
                s["decayed_negatives"] += 1.0 * decay
                reason = ev.get("reason")
                if reason:
                    r_str = str(reason).lower()
                    s["reasons"][r_str] = s["reasons"].get(r_str, 0) + 1
                    # Extra penalty for severe structural faults
                    if any(w in r_str for w in ("generic", "weak", "duplicate", "promise", "quality")):
                        s["decayed_negatives"] += 0.25 * decay

        elif ev_type == "truth_check":
            s["sample_count"] += 1
            vd = ev.get("verdict", "")
            if vd in ("ready", "pass", "READY", "PASS"):
                s["tc_passes"] += 1
                s["decayed_positives"] += 0.75 * decay
            else:
                s["tc_fails"] += 1
                s["decayed_negatives"] += 0.75 * decay

        elif ev_type == "sales":
            cnt = ev.get("count", 1)
            s["sales"] += cnt
            s["sample_count"] += cnt
            s["decayed_positives"] += 0.5 * cnt * decay

    return stats


def compute_smoothed_weight(positives: float,
                            negatives: float,
                            sample_count: int,
                            min_samples: int = MIN_SAMPLES,
                            smoothing_prior: float = 3.0) -> float:
    """Calculate Bayesian/Laplace smoothed weight bounded in [0.5, 1.5].

    If sample_count < min_samples:
      Returns 1.0 (neutral, insufficient history).
    """
    if sample_count < min_samples:
        return 1.0

    total = positives + negatives
    if total <= 0:
        return 1.0

    # Smoothed positive ratio with prior
    smoothed_ratio = (positives + smoothing_prior) / (total + 2.0 * smoothing_prior)
    # Map ratio [0, 1] -> weight [0.5, 1.5]
    weight = 0.5 + smoothed_ratio
    return max(MIN_WEIGHT, min(MAX_WEIGHT, round(weight, 3)))


def get_dimension_weights(dimension: str,
                          db_path: Optional[str] = None,
                          now: Optional[datetime.datetime] = None,
                          min_samples: int = MIN_SAMPLES) -> Dict[str, float]:
    """Return map of identifier -> weight in [0.5, 1.5] for given dimension."""
    events = extract_events(db_path=db_path, now=now)
    stats_map = calculate_dimension_stats(dimension, events, now=now)
    weights: Dict[str, float] = {}

    for name, st in stats_map.items():
        w = compute_smoothed_weight(
            positives=st["decayed_positives"],
            negatives=st["decayed_negatives"],
            sample_count=st["sample_count"],
            min_samples=min_samples,
        )
        weights[name] = w

    return weights


def get_weights(db_path: Optional[str] = None,
                now: Optional[datetime.datetime] = None,
                min_samples: int = MIN_SAMPLES) -> Dict[str, Dict[str, float]]:
    """Return dictionary of all learned weights by dimension."""
    events = extract_events(db_path=db_path, now=now)
    return {
        "niche": {k: compute_smoothed_weight(v["decayed_positives"], v["decayed_negatives"], v["sample_count"], min_samples)
                  for k, v in calculate_dimension_stats("niche", events, now=now).items()},
        "format": {k: compute_smoothed_weight(v["decayed_positives"], v["decayed_negatives"], v["sample_count"], min_samples)
                   for k, v in calculate_dimension_stats("format", events, now=now).items()},
        "audience": {k: compute_smoothed_weight(v["decayed_positives"], v["decayed_negatives"], v["sample_count"], min_samples)
                     for k, v in calculate_dimension_stats("audience", events, now=now).items()},
    }


def get_candidate_weight(candidate: Dict[str, Any],
                         weights: Optional[Dict[str, Dict[str, float]]] = None,
                         db_path: Optional[str] = None,
                         now: Optional[datetime.datetime] = None) -> float:
    """Calculate combined learning weight for a candidate idea in [0.5, 1.5].

    Multiplies dimension deviations or takes geometric mean of niche, format, and audience.
    """
    if weights is None:
        weights = get_weights(db_path=db_path, now=now)

    niche = candidate.get("niche")
    fmt = candidate.get("format")
    aud = candidate.get("audience")

    wn = weights.get("niche", {}).get(niche, 1.0) if niche else 1.0
    wf = weights.get("format", {}).get(fmt, 1.0) if fmt else 1.0
    wa = weights.get("audience", {}).get(aud, 1.0) if aud else 1.0

    # Geometric mean or combined factor clamped to [0.5, 1.5]
    combined = (wn * wf * wa) ** (1.0 / 3.0)
    return max(MIN_WEIGHT, min(MAX_WEIGHT, round(combined, 3)))


def _friendly_name(val: str) -> str:
    """Turn internal identifiers like niche_budget_sheets into friendly text."""
    v = val.replace("niche_", "").replace("_", " ").strip()
    return v.capitalize() if v else ""


def generate_learning_why(candidate: Dict[str, Any],
                          db_path: Optional[str] = None,
                          now: Optional[datetime.datetime] = None,
                          weights: Optional[Dict[str, Dict[str, float]]] = None) -> Optional[str]:
    """Generate a short Tenglish 'why' explanation based on learned signals.

    Examples:
    - "Budget sheets approve rate ekkuva"
    - "Students audience reject rate ekkuva"
    - "Printable planner truth-check pass rate bagundi"
    - "Sales ekkuva unnayi"

    Returns None if there is insufficient history or neutral weight (1.0).
    """
    events = extract_events(db_path=db_path, now=now)
    if not events:
        return None

    if weights is None:
        weights = get_weights(db_path=db_path, now=now)

    niche = candidate.get("niche")
    fmt = candidate.get("format")
    aud = candidate.get("audience")

    wn = weights.get("niche", {}).get(niche, 1.0) if niche else 1.0
    wf = weights.get("format", {}).get(fmt, 1.0) if fmt else 1.0
    wa = weights.get("audience", {}).get(aud, 1.0) if aud else 1.0

    # Find the dimension with the strongest deviation from 1.0
    deviations = [
        ("niche", niche, wn, abs(wn - 1.0)),
        ("format", fmt, wf, abs(wf - 1.0)),
        ("audience", aud, wa, abs(wa - 1.0)),
    ]
    deviations.sort(key=lambda x: x[3], reverse=True)
    best_dim, best_val, best_w, best_dev = deviations[0]

    if best_dev < 0.05 or not best_val:
        # Check sales directly
        n_stats = calculate_dimension_stats("niche", events, now=now).get(niche or "", {})
        if n_stats.get("sales", 0) > 0:
            return f"{_friendly_name(niche or '')} ki sales ekkuva unnayi"
        return None

    stats_for_dim = calculate_dimension_stats(best_dim, events, now=now).get(best_val, {})
    label = _friendly_name(best_val)

    # Positive signal (weight > 1.0)
    if best_w > 1.0:
        if stats_for_dim.get("sales", 0) > 0:
            return f"{label} sales ekkuva unnayi"
        if stats_for_dim.get("approves", 0) > stats_for_dim.get("rejects", 0):
            return f"{label} approve rate ekkuva"
        if stats_for_dim.get("tc_passes", 0) > stats_for_dim.get("tc_fails", 0):
            return f"{label} truth-check pass rate bagundi"
        return f"{label} history lo positive track record undi"

    # Negative signal (weight < 1.0)
    if best_w < 1.0:
        if stats_for_dim.get("rejects", 0) > stats_for_dim.get("approves", 0):
            # Check top reject reason
            reasons = stats_for_dim.get("reasons", {})
            if reasons:
                top_reason = max(reasons.items(), key=lambda r: r[1])[0]
                return f"{label} reject rate ekkuva ({top_reason})"
            return f"{label} reject rate ekkuva"
        if stats_for_dim.get("tc_fails", 0) > stats_for_dim.get("tc_passes", 0):
            return f"{label} truth-check fail rate ekkuva"
        return f"{label} history lo low score undi"

    return None


def learning_summary(db_path: Optional[str] = None,
                     now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """Return a short operational summary used by daily reports.

    Produces concise metrics and a Tenglish summary text without money amounts.
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    events = extract_events(db_path=db_path, now=now)
    weights = get_weights(db_path=db_path, now=now)

    total_events = len(events)
    decisions = [e for e in events if e.get("type") == "decision"]
    tc_events = [e for e in events if e.get("type") == "truth_check"]
    sales_events = [e for e in events if e.get("type") == "sales"]

    approves = sum(1 for e in decisions if e.get("status") == "approved")
    rejects = sum(1 for e in decisions if e.get("status") == "rejected")
    tc_ready = sum(1 for e in tc_events if e.get("verdict") in ("ready", "pass", "READY", "PASS"))
    total_sales = sum(e.get("count", 1) for e in sales_events)

    # Find highest and lowest weighted niches
    niche_weights = weights.get("niche", {})
    sorted_niches = sorted(niche_weights.items(), key=lambda x: x[1], reverse=True)
    top_niches = [f"{_friendly_name(n)} ({w:.2f})" for n, w in sorted_niches if w > 1.05][:2]
    low_niches = [f"{_friendly_name(n)} ({w:.2f})" for n, w in reversed(sorted_niches) if w < 0.95][:2]

    # Build Tenglish summary line
    lines: List[str] = []
    lines.append(f"Learning loop: {len(decisions)} decisions ({approves} approved, {rejects} rejected), "
                 f"{len(tc_events)} truth-checks ({tc_ready} ready), {total_sales} sales tracked (60-day window).")
    if top_niches:
        lines.append(f"High performance niches: {', '.join(top_niches)} - approve & build rate bagundi.")
    if low_niches:
        lines.append(f"Low performance niches: {', '.join(low_niches)} - weight reduced.")
    if not top_niches and not low_niches:
        lines.append("Learning loop neutral ga undi (baseline weights 1.00).")

    summary_text = "\n".join(lines)

    return {
        "total_events": total_events,
        "decisions_count": len(decisions),
        "approves": approves,
        "rejects": rejects,
        "truth_checks_count": len(tc_events),
        "truth_checks_ready": tc_ready,
        "sales_count": total_sales,
        "weights": weights,
        "top_niches": top_niches,
        "low_niches": low_niches,
        "summary_text": summary_text,
    }
