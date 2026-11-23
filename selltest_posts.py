"""The selected packs' private hand-post plan: three Pins and one carousel per day."""
import datetime as dt
import json
from pathlib import Path
import post_planner as planner
import selltest

HOOKS = ('Plan a calmer week','Try a simple daily reset','Keep important details together',
         'Make room for your priorities','Start your next week prepared','Review your progress',
         'Build a routine that fits','Organize one small task today',
         'Keep your next step visible','Turn notes into a clear plan','Make space for focus',
         'Track what matters this week','Prepare for a fresh start','Choose one useful routine')


def _assets(products, packs, links, now, sample_ids=()):
    import pin_ready
    import carousel_maker
    for pack in packs:
        link=links.get(pack['id'])
        if not link:
            continue
        from selltest_site import tracked_link
        link=tracked_link(link,'pinterest')
        source=products/('samples' if pack['id'] in sample_ids else 'upload')/pack['folder']
        target=products/'marketing'/pack['folder']
        if not source.is_dir() or source.is_symlink():
            continue
        try:
            pin_dir=target/'pinterest'
            meta=pin_dir/pin_ready.META_FILE
            valid=meta.is_file() and not pin_ready.check_pins(pin_dir)
            data=json.loads(meta.read_text()) if valid else {}
            if not valid or any(p.get('link')!=link for p in data.get('pins',[])):
                pin_ready.make_pins(source,pin_dir,now=now,store_link=link)
            car_dir=target/'carousel'
            if not (car_dir/carousel_maker.READY_FILE).is_file() or carousel_maker.check_ready_carousel(car_dir):
                carousel_maker.make_ready_carousel(source,car_dir,now=now)
        except (OSError,ValueError,RuntimeError):
            # A row below explains the missing asset; no unchecked image becomes a post.
            continue


def build(products_dir, run, old_plan, db_path=None, now=None):
    import tg_listed
    products=Path(products_dir)
    links={p['id']:p['url'] for p in tg_listed.all_listed(db_path)}
    import free_sample
    sample_links=free_sample.links(db_path)
    links.update(sample_links)
    from selltest_site import tracked_link
    packs=run['packs']; _assets(products,packs,links,now,sample_links)
    assets=planner.ready_items(products)
    groups={}
    for item in assets:
        groups.setdefault((item['pack_id'],item['channel']),[]).append(item)
    previous={r.get('post_id'):r for r in old_plan if r.get('post_id')}
    today=planner.owner_today(now).isoformat()
    start=dt.date.fromisoformat(run['start_day'])
    plan=[]; seen=set(); occurrences={}
    for day_index in range(14):
        day=(start+dt.timedelta(days=day_index)).isoformat()
        selections=[('pinterest',(day_index*3+slot)%len(packs)) for slot in range(3)]
        selections.append(('carousel',day_index%len(packs)))
        for slot,(channel,pack_index) in enumerate(selections):
            pack=packs[pack_index];aid=pack['id'];folder=pack['folder']
            occurrence=occurrences.get((aid,channel),0);occurrences[aid,channel]=occurrence+1
            pid=f"selltest:{run['start_day']}:{day}:{slot}:{aid}"
            seen.add(pid)
            old=previous.get(pid)
            if old and old.get('status')=='done':
                plan.append(old);continue
            available=groups.get((folder,channel),[])
            link=links.get(aid)
            if not link:
                notice=f"first /listed {aid} <link> pampandi; Store link save ayyaka Post ready avuthundi."
            elif not available:
                notice=f"Pack #{aid} Post assets ready kaaledu; /marketing {aid} chudandi."
            else:
                notice=''
            row={'post_id':pid,'pack_id':folder,'day':day_index+1,'date':day,
                 'status':'missed' if day<today else 'todo','selltest':True}
            if notice:
                row.update(channel='action',caption_short=notice,notice=notice,ready=False,asset='')
            else:
                item=dict(available[occurrence%len(available)])
                base=planner.clean_caption(pack['title'],45 if aid in sample_links else 58)
                if aid in sample_links:
                    base='Free sample: '+base
                title=planner.clean_caption(base+': '+HOOKS[occurrence%len(HOOKS)],100)
                item.update(row);row=item
                row.update(title=title,caption_short=title,link=tracked_link(link,'pinterest') if channel=='pinterest' else link)
                if channel=='carousel':
                    row['caption']=title+'\nLink: '+link
            plan.append(row)
    # Preserve actual posting history, including posts from before the experiment.
    history=[r for r in old_plan if r.get('status')=='done' and r.get('post_id') not in seen]
    import selltest_learning
    return selltest_learning.stop_pins(history+plan,selltest_learning.rules(db_path,products,now),now)
