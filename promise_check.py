"""Daily checks against saved operational rows and actual upload files."""
import datetime as dt
from pathlib import Path
import sqlite3
import guardrails

CYCLE_HOURS = 3


def utc(value=None):
    value = value or dt.datetime.now(dt.timezone.utc)
    if isinstance(value, str):
        value = dt.datetime.fromisoformat(value.replace('Z','+00:00'))
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value.astimezone(dt.timezone.utc)


def rows(conn, sql, args=()):
    try:
        return conn.execute(sql,args).fetchall()
    except sqlite3.OperationalError as exc:
        if 'no such table' in str(exc): return []
        raise


def record_build_cycle(db_path=None, now=None):
    """Count completed build passes, including faster alarm/door-bell cycles."""
    with guardrails.connect(db_path) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS promise_cycles(stamp TEXT PRIMARY KEY)')
        conn.execute('INSERT OR IGNORE INTO promise_cycles VALUES(?)',(utc(now).isoformat(),))
        conn.execute('DELETE FROM promise_cycles WHERE stamp<?',((utc(now)-dt.timedelta(days=30)).isoformat(),))


def button_received(update_id, db_path=None, now=None):
    with guardrails.connect(db_path) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS button_receipts (update_id INTEGER PRIMARY KEY, received_ts TEXT, answered_ts TEXT)')
        conn.execute('INSERT OR IGNORE INTO button_receipts(update_id,received_ts) VALUES(?,?)', (update_id,utc(now).isoformat()))


def button_answered(update_id, db_path=None, now=None):
    with guardrails.connect(db_path) as conn:
        return bool(conn.execute('UPDATE button_receipts SET answered_ts=? WHERE update_id=? AND answered_ts IS NULL',(utc(now).isoformat(),update_id)).rowcount)


def check(db_path=None, products_dir=None, now=None):
    import product_builder
    import product_pipeline
    import selltest
    import selltest_doctor
    import post_planner
    now=utc(now); products=Path(products_dir or product_builder.PRODUCTS_DIR)
    conn=guardrails.connect(db_path)
    problems=[]
    try:
        approved=rows(conn,"SELECT a.id,a.title,a.payload,a.decided_ts,b.status FROM approvals a LEFT JOIN product_builds b ON b.approval_id=a.id WHERE a.status='approved' AND a.kind='public_draft'") if rows(conn,"SELECT name FROM sqlite_master WHERE name='product_builds'") else [(*r,None) for r in rows(conn,"SELECT id,title,payload,decided_ts FROM approvals WHERE status='approved' AND kind='public_draft'")]
        stuck=[]
        for aid,title,payload,decided,status in approved:
            if status == 'built' or not product_pipeline.buildable(title,payload): continue
            try:
                stamp=utc(decided) if decided else None
                completed=sum(1 for (cycle,) in rows(conn,'SELECT stamp FROM promise_cycles')
                              if stamp is not None and utc(cycle)>stamp and utc(cycle)<=now)
                # Legacy databases have no cycle ledger yet: retain a six-hour
                # wall-clock backstop so their existing stranded work is caught.
                old=completed>=2 or (now-stamp).total_seconds()>2*CYCLE_HOURS*3600 if stamp else True
            except ValueError: old=True
            if old: stuck.append(aid)
        if stuck:
            causes=rows(conn,"SELECT approval_id,reason FROM product_deliveries WHERE reason != ''")
            reason=next((str(reason)[:100] for aid,reason in causes if aid in stuck),'build step complete kaaledu')
            problems.append(('build',f"Promise build: {len(stuck)} approved Drafts, 2 cycles leda 6h daatina files levu; {reason}. Fix: /doctor promises; /view {stuck[0]}"))
        missing=[]
        for aid,folder in rows(conn,"SELECT approval_id,folder FROM product_builds WHERE status='built'"):
            if not folder or Path(folder).name != folder or not selltest_doctor.kit_ok(products/'upload'/folder): missing.append(aid)
        if missing:
            problems.append(('kit',f'Promise kit: {len(missing)} built Packs ki complete Upload kit ledu. Fix: /upload {missing[0]}; /doctor selltest'))
        waiting=[]
        for aid,stamp in rows(conn,"SELECT id,ts FROM approvals WHERE status='pending' AND kind='public_draft'"):
            try: old=(now-utc(stamp)).total_seconds()>86400 if stamp else True
            except ValueError: old=True
            if old: waiting.append(aid)
        if waiting:
            problems.append(('decision',f'Promise decision: {len(waiting)} Drafts 24h daatina waiting lo unnayi; owner/checker decision ledu. Fix: /autoapprove status; /view {waiting[0]}'))
        unanswered=rows(conn,'SELECT update_id FROM button_receipts WHERE answered_ts IS NULL')
        if unanswered:
            problems.append(('button',f'Promise button: {len(unanswered)} received presses ki result delivery confirm kaaledu. Fix: /buttontest; /list'))
        run=selltest.active(db_path)
        if selltest.running(run,now):
            today=post_planner.owner_today(now).isoformat()
            measured={r['id'] for r in selltest.stats(db_path) if r['day']==today}
            missing=[p['id'] for p in run['packs'] if 1 <= selltest.day_number(run,now,p) <= 14 and p['id'] not in measured]
            if missing:
                problems.append(('stats',f'Promise stats: ee Sell test day lo {len(missing)} Packs numbers enter cheyaledu. Fix: /selltest stats {missing[0]} views <views> sales <sales>'))
    finally:
        conn.close()
    return problems


def report(db_path=None, products_dir=None, now=None):
    return '\n'.join(text for _,text in check(db_path,products_dir,now)) or 'Anni promises OK'


def send_daily(send, db_path=None, products_dir=None, now=None):
    """Reserve each promise before delivery; ambiguous sends do not cause duplicate alerts."""
    import daily_report
    day=daily_report.delivery_day(utc(now))
    problems=check(db_path,products_dir,now) or [('ok','Anni promises OK')]
    count=0
    for key,text in problems:
        with guardrails.connect(db_path) as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS promise_alerts(day TEXT, promise TEXT, PRIMARY KEY(day,promise))')
            won=conn.execute('INSERT OR IGNORE INTO promise_alerts VALUES(?,?)',(day,key)).rowcount
        if won:
            send(text); count+=1
    return count
