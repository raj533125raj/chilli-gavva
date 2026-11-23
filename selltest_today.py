"""One compact owner checklist from durable test, listing and post records."""
import post_planner
import selltest


def checklist(db_path=None, products_dir=None, now=None):
    import free_sample
    import tg_listed
    run = selltest.active(db_path)
    if not run or not 1 <= selltest.day_number(run, now) <= selltest.DAYS:
        return ''
    root = selltest.products_root(products_dir)
    day = post_planner.owner_today(now).isoformat()
    paid = {p['id'] for p in tg_listed.all_listed(db_path)}
    samples = free_sample.links(db_path)
    measured = {r['id'] for r in selltest.stats(db_path) if r['day'] == day}
    posts = [r for r in post_planner.load_plan(post_planner.plan_path_for(root)) if r.get('date') == day]
    lines = [f"Ee roju 10 nimishala pani | day {selltest.day_number(run, now)}/14"]
    for pack in run['packs']:
        aid = pack['id']
        tasks = []
        if aid not in paid:
            tasks.append(f'/upload {aid} → Gumroad lo meere pettandi; /listed {aid} <link>')
        if aid not in samples:
            tasks.append(f'/upload {aid} sample → meere pettandi; /selltest sample {aid} <link>')
        pending = [str(i) for i, r in enumerate(posts, 1)
                   if r.get('pack_id') == pack['folder'] and r.get('status') != 'done']
        if pending:
            tasks.append('/post today → meere post chesi ' + ', '.join('/posted '+i for i in pending))
        if aid not in measured:
            tasks.append(f'/selltest stats {aid} views <views> sales <sales>')
        lines.append(f"Pack #{aid}: " + ' → '.join(tasks or ['Ee roju pani poorthi.']))
    lines.append('Next: paina unna modati command pampandi; upload, posts meere cheyandi.')
    return '\n'.join(lines)
