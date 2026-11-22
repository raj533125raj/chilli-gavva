"""One database-backed view of built products and upload packs offered to the owner."""
import datetime
import sqlite3
from pathlib import Path


def snapshot(db_path, products_dir, marketing_dir=None, now=None):
    """Return current ready pack evaluations and the last-day built count.

    A ZIP on disk cannot override a failed or held database build. A missing database
    build table returns None for legacy file-only tools; a real empty table returns zero.
    """
    if not db_path or not Path(db_path).is_file():
        return None
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if now.tzinfo is not None:
        now = now.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    since = (now-datetime.timedelta(days=1)).isoformat()
    with sqlite3.connect(db_path) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='product_builds'").fetchone():
            return None
        built = conn.execute("SELECT COUNT(*) FROM product_builds WHERE status='built' AND ts>=?", (since,)).fetchone()[0]
        rows = conn.execute("SELECT b.approval_id,b.folder FROM product_builds b JOIN approvals a ON a.id=b.approval_id "
                            "WHERE b.status='built' AND a.status='approved'").fetchall()
    import product_builder
    import sell_ready
    allowed = {aid: folder for aid, folder in rows if not product_builder.is_held(aid, db_path)}
    packs=[]
    seen=set()
    for aid, title, path in sell_ready.find_all_ready_packs(products_dir):
        if aid in seen or allowed.get(aid) != path.name:
            continue
        evaluation=sell_ready.evaluate_pack(path, marketing_dir or Path(products_dir)/'marketing')
        if evaluation['status'] in (sell_ready.STATUS_SELLABLE, sell_ready.STATUS_ALMOST):
            packs.append((aid,title,path,evaluation))
            seen.add(aid)
    return {'built':built,'upload_ready':len(packs),'packs':packs}
