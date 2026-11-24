"""Restore an accidentally timed-out Scout product only on the owner's command."""
import os
import re
import guardrails

COMMANDS = [{'cmd': '/restore', 'words': ['restore'], 'help_en': 'Restore timed-out Scout draft: /restore N',
             'help_te': 'Timeout ayina Scout Draft kosam /restore N pampandi.'}]


def handle(text, ctx):
    match = re.fullmatch(r'/?restore(?:@\w+)?(?:\s+(\d+))?', str(text).strip(), re.I)
    if not match:
        return None
    owner = os.environ.get('TELEGRAM_CHAT_ID', '')
    if owner and str(ctx.get('chat_id')) != owner:
        return None
    if not match.group(1):
        return {'text':'Timeout ayina Scout product Draft malli waiting lo pettadaniki /restore N pampandi.\nNext: /list'}
    aid = int(match.group(1))
    conn = guardrails.connect(ctx.get('db_path'))
    try:
        with conn:
            changed = conn.execute("UPDATE approvals SET status='pending', decided_ts=NULL, reason=NULL, ts=? "
                                   "WHERE id=? AND kind='public_draft' AND title LIKE '[Scout Idea] %' "
                                   "AND status='rejected' AND reason='owner time ayipoyindi'",
                                   (ctx['now'].isoformat(), aid)).rowcount
            if changed:
                # Scout sync may have copied the timeout decision. Reopen only this row.
                exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='scout_ideas'").fetchone()
                if exists:
                    conn.execute("UPDATE scout_ideas SET status='pending', synced=0 WHERE approval_id=?", (aid,))
    finally:
        conn.close()
    return {'text': (f'Draft #{aid} malli waiting lo undi.\nNext: /view {aid}' if changed else
                     'Timeout ayina Scout product Draft maatrame restore cheyagalanu.\nNext: /list')}
