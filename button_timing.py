"""Measure test-button delivery from the clock's first observation, never message creation time."""
import datetime as dt
import math
import statistics
import guardrails
import promise_check


def record(query_id,update_id,db_path=None,now=None,result=None):
    if result is None:
        import always_on
        result=always_on.call('/button-result',{'query_id':query_id})
    minutes=(result or {}).get('minutes')
    if type(minutes) not in (int,float) or not math.isfinite(minutes) or not 0<=minutes<=10080:return False
    stamp=promise_check.utc(now).isoformat()
    with guardrails.connect(db_path) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS button_timings(update_id INTEGER PRIMARY KEY, result_ts TEXT, minutes REAL)')
        conn.execute('INSERT OR IGNORE INTO button_timings VALUES(?,?,?)',(update_id,stamp,minutes))
        conn.execute('DELETE FROM button_timings WHERE result_ts<?',((promise_check.utc(now)-dt.timedelta(days=30)).isoformat(),))
    return True


def line(db_path=None,now=None):
    since=(promise_check.utc(now)-dt.timedelta(days=1)).isoformat()
    with guardrails.connect(db_path) as conn:
        values=[r[0] for r in promise_check.rows(conn,'SELECT minutes FROM button_timings WHERE result_ts>=?',(since,))]
    if not values:return '/buttontest median: unknown (measured presses levu)'
    return f'/buttontest median: {statistics.median(values):.1f} min ({len(values)} tests; door bell press -> result)'
