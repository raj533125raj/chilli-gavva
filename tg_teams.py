"""CCH-1: /teams — each team's real output of the last 7 days (Pinterest, Instagram, Marketing, Product,
Research), what waits for the owner and one next step. Numbers come only from real files and DB rows
(teams_report.py). Read-only: nothing is posted, built or changed.

  /teams            all teams
  /teams pinterest  one team (pinterest | instagram | marketing | product | research)
  bare words ("teams", "team report") -> short help
"""
from __future__ import annotations

import re
from typing import Optional

COMMANDS = [
    {"cmd": "/teams", "words": ["teams", "team report", "teams report", "teams chupinchu"],
     "help_en": "/teams - what each team (Pinterest, Instagram, Marketing, Product, Research) made in 7 days",
     "help_te": "/teams - prathi team 7 rojullo emi chesindi, meekosam emi waiting, next step"},
]

HELP_TEXT = ("Teams report kosam /teams pampandi.\n"
             "Okka team matrame: /teams pinterest | /teams instagram | /teams marketing | /teams product | "
             "/teams research\n"
             "Anni numbers real files / DB nunchi; edi lekapothe 0 chupistundi.")

_SLASH = re.compile(r"^/teams(?:@\w+)?(?:\s+(\S+))?\s*$", re.IGNORECASE)


def handle(text: str, ctx: dict) -> Optional[dict]:
    if not text or not isinstance(text, str):
        return None
    raw = text.strip()
    norm = re.sub(r"\s+", " ", raw.lower())
    match = _SLASH.match(raw)
    if match:
        import teams_report
        word = match.group(1)
        team = teams_report.team_of(word) if word else None
        if word and team is None:
            return {"text": f"'{word}' ane team ledu.\n" + HELP_TEXT}
        try:
            stats = teams_report.team_stats(db_path=ctx.get("db_path"), now=ctx.get("now"))
            return {"text": teams_report.report_text(stats, team=team)}
        except Exception as exc:     # one short line; details stay out of the chat
            return {"text": f"Teams report ippudu chupinchalekapoyanu ({type(exc).__name__}). /doctor chudandi."}
    if norm in {w for c in COMMANDS for w in c["words"]} or norm in ("team", "teams?", "team?"):
        return {"text": HELP_TEXT}
    return None
