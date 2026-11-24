"""Measured day-7/day-14 rules for private drafts and owner-controlled traffic.

Any measured sale is a winner. Zero entered views creates a traffic-fix task;
positive views with no sales creates a listing-fix task. Only day 14 with at
least seven entered days and 200 views permits Drop. Missing data is unknown.
Rules persist after the milestone; future entries never leak into an earlier review.
"""
from contextlib import closing
import datetime as dt
import json
import re
from pathlib import Path
import guardrails
import post_planner
import selltest
import selltest_site


def rules(db_path=None, products_dir=None, now=None):
    run = selltest.active(db_path)
    if not run:
        return []
    rows = selltest.stats(db_path)
    result = []
    for pack in run['packs']:
        day = selltest.day_number(run,now,pack)
        if day < 7: continue
        checkpoint = 14 if day >= 14 else 7
        start = pack.get('start_day', run['start_day'])
        end = (dt.date.fromisoformat(start)+dt.timedelta(days=checkpoint-1)).isoformat()
        entries = [r for r in rows if r['id'] == pack['id'] and start <= r['day'] <= end]
        views = sum(r['views'] for r in entries); sales = sum(r['sales'] for r in entries)
        if not entries:
            action, task = 'unknown', f"Numbers levu; /selltest stats {pack['id']} views <views> sales <sales> pampandi."
        elif sales > 0 and views > 0:
            action, task = 'winner', 'Ade niche / format / style Drafts ki preference; traffic konasaginchandi.'
        elif views == 0:
            action, task = 'traffic fix', f"3 kotha Pins, kotha keyword hooks prepare chesi /marketing {pack['id']}; /post today chudandi."
        elif checkpoint == 14 and views >= 200 and len(entries) >= 7:
            action, task = 'Drop', 'Listing fix: Title B, Cover, first Preview review cheyandi; ee Pack ki kotha Pins aapamu.'
        else:
            action, task = 'listing fix', f"/selltest swap-title {pack['id']}; /marketing {pack['id']} lo Cover, first Preview review chesi Store lo meere marchandi."
        item = guardrails.get_approval(pack['id'], db_path) or {}
        text = (item.get('title') or pack['title'])+'\n'+(item.get('payload') or '')
        import scout
        niche_match = re.search(r'\bniche_[a-z0-9_]+\b', text)
        niche = niche_match.group() if niche_match else scout.resolve_niche(pack['title'], 'Templates')
        style_match = re.search(r'(?im)^(?:Design theme|Style):\s*([^\n]+)', text)
        style = style_match.group(1).strip().rstrip('.') if style_match else ''
        source = selltest_site.safe_path(selltest.products_root(products_dir), pack['folder'])
        if source.is_dir():
            import product_builder
            selltest_site.safe_path(source, 'listing.md')
            style = product_builder.recorded_theme(source) or style
        result.append(dict(pack, checkpoint=checkpoint, start_day=start, action=action, task=task,
                           views=views, sales=sales, entered_days=len(entries), missing_days=checkpoint-len(entries),
                           niche=niche, style=style))
    return result


def record(db_path=None, products_dir=None, now=None):
    learned = rules(db_path, products_dir, now)
    if not learned: return []
    with closing(guardrails.connect(db_path)) as conn, conn:
        conn.execute('CREATE TABLE IF NOT EXISTS sell_test_learning (start_day TEXT, checkpoint INTEGER, approval_id INTEGER, '
                     'payload TEXT NOT NULL, PRIMARY KEY(start_day,checkpoint,approval_id))')
        for item in learned:
            conn.execute('INSERT INTO sell_test_learning VALUES(?,?,?,?) ON CONFLICT(start_day,checkpoint,approval_id) '
                         'DO UPDATE SET payload=excluded.payload',
                         (item['start_day'], item['checkpoint'], item['id'], json.dumps(item, sort_keys=True)))
    return learned


def text(db_path=None, products_dir=None, now=None):
    learned = record(db_path, products_dir, now)
    if not learned:
        return 'Day 7 nunchi entered numbers tho nerchukuntamu.\nNext: /selltest numbers pampandi.'
    lines = [f"Sell test day {learned[0]['checkpoint']} learning; entered numbers matrame."]
    for item in learned:
        lines.append(f"#{item['id']} {item['action']}: Views {item['views']}, Sales {item['sales']}, missing days {item['missing_days']}; "+item['task'])
    lines.append('Next: paina unna fix command pampandi; Store changes, posts meere cheyandi.')
    return '\n'.join(lines)


def tune(candidate, learned):
    """Bounded preference; niche/audience/quality caps in Scout remain unchanged."""
    import format_registry
    kind = candidate.get('format')
    if not format_registry.get_format_info(kind):
        kind = format_registry.classify(candidate.get('idea', ''))
    best, boost = None, 0
    for item in learned:
        if item['action'] != 'winner': continue
        same_niche = candidate.get('niche') == item['niche']
        same_format = kind == item['format']
        value = 6*same_niche + 6*same_format
        if same_niche and same_format and item['style']:
            value += 4
        if value > boost:
            best, boost = item, value
    result = dict(candidate, selltest_boost=boost)
    if best and best['style'] and kind == best['format'] and candidate.get('niche') == best['niche']:
        result['style'] = best['style']
    if boost:
        result['selltest_why'] = f"Sell test #{best['id']}: entered Views {best['views']}, Sales {best['sales']}; ade niche/format/style preference."
    return result


def prompt(learned):
    winners = [item for item in learned if item['action'] == 'winner']
    if not winners: return ''
    lines = ['Owner-entered sell-test evidence: prefer new drafts in these proven combinations; keep all safety, diversity and quality rules.']
    lines += [f"{item['niche']} / {item['format']} / {item['style'] or 'style not recorded'}: {item['views']} views, {item['sales']} sales."
              for item in winners]
    return '\n'+'\n'.join(lines)


def stop_pins(plan, learned, now=None):
    """Keep numbered today slots and confirmed history; refuse new loser Pin delivery."""
    dropped = {item['folder']: item for item in learned if item['action'] == 'Drop'}
    today = post_planner.owner_today(now).isoformat()
    result = []
    for row in plan:
        if (row.get('pack_id') in dropped and row.get('channel') == 'pinterest' and
                row.get('status') != 'done' and row.get('date', '') >= today):
            item = dropped[row['pack_id']]
            notice = f"Pack #{item['id']} Drop: kotha Pins aapamu. Next: /selltest learn tho listing fix chudandi."
            row = dict(row, channel='action', ready=False, asset='', notice=notice, caption_short=notice, selltest_stopped=True)
            for key in ('assets', 'image', 'images', 'link'):
                row.pop(key, None)
        result.append(row)
    return result
