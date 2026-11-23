"""Private, owner-controlled fourteen-day sales experiment. No store API calls."""
import datetime as dt
from contextlib import contextmanager, closing
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PREFERRED = ('sheets', 'biz_sheets', 'planner', 'resume', 'notion', 'digital_planner')
DAYS = 14


def products_root(products_dir=None):
    return Path(products_dir) if products_dir is not None else ROOT/'exported_content'/'products'


def _db(db_path=None):
    import guardrails
    return str(db_path or guardrails.DB_PATH)


@contextmanager
def _connect(db_path=None):
    import guardrails
    conn = guardrails.connect(_db(db_path))
    conn.execute('CREATE TABLE IF NOT EXISTS sell_test (id INTEGER PRIMARY KEY CHECK(id=1), start_day TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS sell_test_packs (slot INTEGER PRIMARY KEY, approval_id INTEGER UNIQUE NOT NULL, '
                 'title TEXT NOT NULL, folder TEXT NOT NULL, format TEXT NOT NULL, listing_score INTEGER NOT NULL)')
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def active(db_path=None):
    """Read existing membership without creating a database or starting another experiment."""
    path = Path(_db(db_path))
    if not path.is_file():
        return None
    with closing(sqlite3.connect(path)) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='sell_test'").fetchone():
            return None
        row = conn.execute('SELECT start_day FROM sell_test WHERE id=1').fetchone()
        if not row:
            return None
        packs = conn.execute('SELECT slot,approval_id,title,folder,format,listing_score FROM sell_test_packs ORDER BY slot').fetchall()
    keys = ('slot','id','title','folder','format','listing_score')
    return {'start_day': row[0], 'packs': [dict(zip(keys,p)) for p in packs]}


def day_number(run, now=None):
    import post_planner
    return (post_planner.owner_today(now)-dt.date.fromisoformat(run['start_day'])).days+1


def candidates(db_path=None, products_dir=None, now=None):
    """Only database-approved, built, held-free packs that pass the real readiness and listing gates."""
    import product_counts
    import listing_lint
    import store_kits
    view = product_counts.snapshot(_db(db_path), products_root(products_dir), now=now)
    choices = []
    for aid, title, pack, evaluation in (view or {}).get('packs', []):
        if Path(pack).is_symlink():
            continue
        quality = listing_lint.quality_report(pack)
        if not quality['ok']:
            continue
        kind = store_kits.gumroad_product_type(Path(pack), store_kits.parse_listing_file(Path(pack)/'listing.txt'))
        choices.append({'id':aid, 'title':title, 'folder':Path(pack).name, 'format':kind,
                        'listing_score':quality['score'], 'ready_score':evaluation['score']})
    rank = lambda p: (-p['listing_score'], PREFERRED.index(p['format']) if p['format'] in PREFERRED else len(PREFERRED),
                      -p['ready_score'], p['id'])
    return sorted(choices,key=rank)


def start(db_path=None, products_dir=None, now=None):
    import post_planner
    existing = active(db_path)
    if existing:
        return existing
    packs = candidates(db_path,products_dir,now)[:8]
    if len(packs) < 5:
        raise ValueError('Kanisam 5 READY Packs kavali; /ready lo fixes chudandi.')
    with _connect(db_path) as conn:
        conn.execute('BEGIN IMMEDIATE')
        if not conn.execute('SELECT 1 FROM sell_test WHERE id=1').fetchone():
            conn.execute('INSERT INTO sell_test VALUES(1,?)',(post_planner.owner_today(now).isoformat(),))
            conn.executemany('INSERT INTO sell_test_packs VALUES(?,?,?,?,?,?)',
                [(i,p['id'],p['title'],p['folder'],p['format'],p['listing_score']) for i,p in enumerate(packs,1)])
    return active(db_path)


def swap(old_id, new_id, db_path=None, products_dir=None, now=None):
    run = active(db_path)
    if not run or not 1 <= day_number(run,now) <= DAYS:
        raise ValueError('Active 14-day Sell test kavali; /selltest list chudandi.')
    old = next((p for p in run['packs'] if p['id']==old_id),None)
    if old is None or any(p['id']==new_id for p in run['packs']):
        raise ValueError('A test lo unna Pack number; B vere READY Pack number ivvandi.')
    new = next((p for p in candidates(db_path,products_dir,now) if p['id']==new_id),None)
    if new is None:
        raise ValueError('Kotha Pack READY kaadu; /ready lo fixes chudandi.')
    with _connect(db_path) as conn:
        changed = conn.execute('UPDATE sell_test_packs SET approval_id=?,title=?,folder=?,format=?,listing_score=? '
                              'WHERE slot=? AND approval_id=?',
            (new['id'],new['title'],new['folder'],new['format'],new['listing_score'],old['slot'],old_id)).rowcount
        if not changed:
            raise ValueError('Pack selection maarindi; /selltest list malli chudandi.')
    return active(db_path)


def plan_text(run, now=None):
    day = day_number(run,now)
    lines = [f"14-day Sell test: start {run['start_day']} | day {min(max(day,1),14)}/14",
             'Packs ni meere upload cheyandi; 14 rojulu numbers record cheyandi.']
    lines += [f"{p['slot']}. {p['title'][:70]} | /upload {p['id']}" for p in run['packs']]
    if day > DAYS:
        lines.append('14 rojulu poorthi; saved numbers tho results chudandi.')
    lines.append('Next: /selltest list; Pack marchadaniki /selltest swap A B (Pack numbers).')
    return '\n'.join(lines)


def save_stats(aid, views, sales, db_path=None, now=None):
    """Replace this owner's daily totals, never add a resubmission twice."""
    import post_planner
    run = active(db_path)
    if not run or not 1 <= day_number(run,now) <= DAYS or aid not in {p['id'] for p in run['packs']}:
        raise ValueError('Ee Pack active Sell test lo ledu leda 14 rojulu poorthi ayyayi.')
    if any(type(n) is not int or not 0 <= n <= 999999999 for n in (views,sales)) or sales > views:
        raise ValueError('Views, Sales positive whole numbers leda zero ivvandi; Sales Views ni minchakudadhu.')
    day = post_planner.owner_today(now).isoformat()
    with _connect(db_path) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS sell_test_stats (approval_id INTEGER NOT NULL, day TEXT NOT NULL, '
                     'views INTEGER NOT NULL CHECK(views>=0), sales INTEGER NOT NULL CHECK(sales>=0 AND sales<=views), '
                     'PRIMARY KEY(approval_id,day))')
        conn.execute('INSERT INTO sell_test_stats VALUES(?,?,?,?) ON CONFLICT(approval_id,day) '
                     'DO UPDATE SET views=excluded.views,sales=excluded.sales',(aid,day,views,sales))
    return day


def stats(db_path=None):
    path = Path(_db(db_path))
    if not path.is_file():
        return []
    with closing(sqlite3.connect(path)) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='sell_test_stats'").fetchone():
            return []
        return [dict(zip(('id','day','views','sales'),row)) for row in
                conn.execute('SELECT approval_id,day,views,sales FROM sell_test_stats ORDER BY day,approval_id')]


def pins_posted(aid, day, products_dir=None):
    """Count only owner-confirmed Pins on the actual India posting date."""
    import post_planner
    plan = post_planner.load_plan(post_planner.plan_path_for(products_root(products_dir)))
    seen = set()
    for i,row in enumerate(plan):
        if row.get('status') != 'done' or row.get('channel') != 'pinterest':
            continue
        if str(row.get('pack_id','')).split('-')[0] != str(aid):
            continue
        posted_day = row.get('date')
        if row.get('posted_at'):
            try:
                stamp = dt.datetime.fromisoformat(row['posted_at'].replace('Z','+00:00'))
                posted_day = post_planner.owner_today(stamp).isoformat()
            except (ValueError,TypeError):
                continue
        if posted_day == day:
            seen.add(row.get('post_id') or str(i))
    return len(seen)


def report_block(db_path=None, now=None, products_dir=None):
    import post_planner
    run = active(db_path)
    if not run or not 1 <= day_number(run,now) <= DAYS:
        return ''
    day = post_planner.owner_today(now).isoformat()
    rows = {r['id']:r for r in stats(db_path) if r['day']==day}
    lines = [f"Sell test day {day_number(run,now)}/14 ({day})"]
    for pack in run['packs']:
        row = rows.get(pack['id'])
        pins = pins_posted(pack['id'],day,products_dir)
        if row is None:
            numbers = 'Views - | Sales - | Conversion - (numbers inka ivvaledu)'
        else:
            conversion = 100*row['sales']/row['views'] if row['views'] else 0
            numbers = f"Views {row['views']} | Sales {row['sales']} | Conversion {conversion:.1f}%"
        lines.append(f"#{pack['id']}: {numbers} | Pins posted {pins}")
    lines.append('Next: /selltest stats N views 120 sales 1 (ee roju totals) pampandi.')
    return '\n'.join(lines)


def review(db_path=None, now=None):
    """Conservative day-7/day-14 decisions from entered daily totals, with missing data explicit.

    Any sale -> keep; zero measured traffic -> improve traffic; traffic without sales -> change
    listing/title/cover/price positioning. Only day 14 with at least 200 measured views and no
    sales recommends dropping from the next rotation. No product is deleted or published.
    """
    import post_planner
    run = active(db_path)
    if not run or day_number(run,now) not in (7,14):
        return []
    today = post_planner.owner_today(now).isoformat()
    rows = [r for r in stats(db_path) if run['start_day'] <= r['day'] <= today]
    decisions=[]
    for pack in run['packs']:
        entries=[r for r in rows if r['id']==pack['id']]
        views=sum(r['views'] for r in entries);sales=sum(r['sales'] for r in entries)
        if not entries:
            action,reason='Change','Numbers mundu record cheyandi; result inka teliyadu.'
        elif sales:
            action,reason='Keep','Sales unnayi; ade Pack ki traffic konasaginchandi.'
        elif views==0:
            action,reason='Change','Traffic problem: keyword Pins, boards, link check cheyandi.'
        elif day_number(run,now)==14 and views>=200 and len(entries)>=7:
            action,reason='Drop','Views unna Sales levu; next rotation nunchi pakkana pettandi.'
        else:
            action,reason='Change','Listing problem: Title, Cover, benefits, Price positioning review cheyandi.'
        decisions.append(dict(pack,action=action,reason=reason,views=views,sales=sales,
                              entered_days=len(entries),missing_days=day_number(run,now)-len(entries)))
    return decisions


def review_block(db_path=None, now=None):
    decisions=review(db_path,now)
    if not decisions:
        return ''
    run=active(db_path)
    lines=[f"Sell test day {day_number(run,now)} review (entered numbers matrame)"]
    for p in decisions:
        lines.append(f"#{p['id']} {p['action']}: {p['views']} Views, {p['sales']} Sales; {p['reason']} Missing days {p['missing_days']}.")
    lines.append('Next: Meeru decide cheyandi; /selltest list lo Packs chudandi.')
    return '\n'.join(lines)
