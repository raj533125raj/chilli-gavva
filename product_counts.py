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


def sell_test_progress(db_path=None, products_dir=None, now=None):
    """Count complete, database-approved handoff kits; explain the shortfall."""
    from collections import Counter
    import struct
    import zipfile
    import guardrails
    import gumroad_handoff
    db_path=db_path or guardrails.DB_PATH
    products=Path(products_dir) if products_dir is not None else Path(__file__).resolve().parent/'exported_content/products'
    view=snapshot(db_path,products,now=now)
    ready=set();blocked=Counter();seen=set()
    for aid,title,pack,evaluation in (view or {}).get('packs',[]):
        seen.add(aid)
        kit=Path(pack)/gumroad_handoff.FOLDER
        try:
            if kit.is_symlink():raise ValueError('Upload kit symlink')
            missing=[name for name in gumroad_handoff.FILES if not (kit/name).is_file() or (kit/name).is_symlink()]
            if missing:
                blocked['Upload kit files kavali: '+', '.join(missing[:2])]+=1
                continue
            for name,size in gumroad_handoff.SIZES.items():
                with (kit/name).open('rb') as handle:header=handle.read(24)
                if header[:8]!=b'\x89PNG\r\n\x1a\n' or len(header)<24 or struct.unpack('>II',header[16:24])!=size:
                    raise ValueError('Upload image size fix kavali')
            with zipfile.ZipFile(kit/'product.zip') as archive:
                if not archive.namelist() or archive.testzip() is not None:raise ValueError('Buyer ZIP fix kavali')
            ready.add(aid)
        except (OSError,ValueError,zipfile.BadZipFile):
            blocked['Upload kit fix kavali: /ready']+=1
    if Path(db_path).is_file():
        with sqlite3.connect(db_path) as conn:
            tables={row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if {'product_builds','approvals'}<=tables:
                for aid,status in conn.execute("SELECT b.approval_id,b.status FROM product_builds b JOIN approvals a ON a.id=b.approval_id WHERE a.status='approved' AND a.kind='public_draft'"):
                    if aid not in seen and status not in ('rejected',):blocked['Pack fixes leda Hold: /ready']+=1
        if 'approvals' in tables:
            import auto_approve
            labels={'under_24h':'24h wait migilindi','daily_limit':'Auto daily limit aipoyindi',
                    'similar':'Duplicate: owner review','checker_revise':'Checker REVISE: owner review',
                    'checker_signal':'Checker signal check kavali','off':'Auto-approve OFF lo undi',
                    'paused':'Robot pause lo undi','no_time':'Draft time check kavali',
                    'low_score':'Low score: next run review','ready':'Next run approval migilindi',
                    'checker_problem':'Checker fix kavali'}
            for row in auto_approve.waiting_report(db_path,now)['drafts']:
                blocked[labels.get(row['why'],'Owner review migilindi')]+=1
    count=min(8,len(ready))
    if count<8 and not blocked:blocked['Kotha Drafts kavali: /list']=8-count
    return {'ready':count,'target':8,'ready_ids':sorted(ready),'blockers':dict(blocked)}


def sell_test_line(db_path=None,products_dir=None,now=None):
    progress=sell_test_progress(db_path,products_dir,now)
    text=f"Sell test ready: {progress['ready']}/8 packs"
    if progress['ready']<8:
        reasons=sorted(progress['blockers'].items(),key=lambda row:(-row[1],row[0]))[:2]
        text+='\nBlocks: '+'; '.join(f'{reason} ({count})' for reason,count in reasons)
    return text
