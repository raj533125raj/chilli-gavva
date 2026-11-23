"""Three real offline robot days from the live-shaped approval queue.

Only the outside world and clock are simulated by the existing day simulator.
The three-hour fallback schedule runs all daily slots. Approval policy, quotas,
drafts, builders, ZIPs and readiness gates remain real.
No owner buttons are pressed, and the temporary simulation code is removed.
"""
import argparse
from collections import Counter
import datetime as dt
import json
from pathlib import Path
import sys
from unittest import mock
import zipfile

ROOT=Path(__file__).resolve().parents[1]
for folder in (ROOT,ROOT/'tools'):
    if str(folder) not in sys.path:sys.path.insert(0,str(folder))


def simulate(now=None,log=lambda text:None):
    import approval_probe
    import robot_day_sim
    import product_counts
    import gumroad_handoff
    import store_kits
    import selltest
    now=now or dt.datetime.now(dt.timezone.utc)
    # An ordinary afternoon cycle on the owner's current calendar day.
    import post_planner
    day=post_planner.owner_today(now)
    start=dt.datetime.combine(day,dt.time(12,5),dt.timezone.utc)
    snapshots=[]

    def capture(paths,when):
        code=paths['code'];products=code/'exported_content/products';db=code/'agent_data.db'
        view=product_counts.snapshot(str(db),products,now=when)
        ready=[];blocked=[]
        for aid,title,pack,evaluation in (view or {}).get('packs',[]):
            pack=Path(pack);kit=pack/gumroad_handoff.FOLDER
            missing=[name for name in gumroad_handoff.FILES if not (kit/name).is_file()]
            if missing:
                blocked.append({'id':aid,'reason':'Upload kit missing: '+', '.join(missing)});continue
            with zipfile.ZipFile(kit/'product.zip') as archive:
                if archive.testzip() is not None:raise ValueError('Damaged buyer ZIP')
                files=len(archive.namelist())
            kind=store_kits.gumroad_product_type(pack,store_kits.parse_listing_file(pack/'listing.txt'))
            ready.append({'id':aid,'format':kind,'files':files,'kit_files':len(gumroad_handoff.FILES)})
        import auto_approve
        waiting={row['id']:row['why_text'] for row in auto_approve.waiting_report(str(db),when)['drafts']}
        conn=__import__('sqlite3').connect(db)
        try:
            for aid,status,reason in conn.execute("SELECT a.id,a.status,COALESCE(d.reason,'') FROM approvals a LEFT JOIN product_deliveries d ON d.approval_id=a.id WHERE a.kind='public_draft' AND a.status IN ('pending','approved')"):
                if aid not in {p['id'] for p in ready}:blocked.append({'id':aid,'reason':reason or (waiting.get(aid,'Owner/checker approval waiting') if status=='pending' else 'Approved build waiting')})
        finally:conn.close()
        row={'day':len(snapshots)+1,'date':post_planner.owner_today(when).isoformat(),'ready':len(ready),
             'preferred_ready':sum(p['format'] in selltest.PREFERRED for p in ready),
             'formats':dict(Counter(p['format'] for p in ready)),'packs':ready,'blocked':blocked}
        snapshots.append(row);log(f"Day {row['day']}: {row['ready']}/8 ready; preferred niches {row['preferred_ready']}")

    def seed(paths,env):
        approval_probe.seed_queue(paths['code']/'agent_data.db',start,live=True)
        (paths['sim']/'model_profile.json').write_text(json.dumps({'distinct_subjects':True}))
    def next_day(index):
        def event(paths,env):capture(paths,start+dt.timedelta(days=index,hours=-3))
        return event
    original=robot_day_sim.build_report
    def finish(paths,steps,timeline,owner,runs,failed):
        capture(paths,start+dt.timedelta(hours=69))
        return original(paths,steps,timeline,owner,runs,failed)
    with mock.patch.object(robot_day_sim.Owner,'act',return_value=[]),mock.patch.object(robot_day_sim,'build_report',side_effect=finish):
        report=robot_day_sim.simulate(hours=72,every=180,start=start.strftime('%Y-%m-%dT%H:%M:%SZ'),
            events={0:seed,8:next_day(1),16:next_day(2)},python=sys.executable,log=log)
    return {'days':snapshots,'failed_runs':report['failed_runs'],'errors':report.get('errors',[]),
            'build_logs':report.get('builds',[]),'timeline':report.get('timeline',[]),
            'owner_commands':[], 'sandbox_removed':report.get('sandbox')=='(deleted)'}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',required=True)
    args=parser.parse_args(argv)
    out=Path(args.out).resolve()
    if out==ROOT or ROOT in out.parents:raise SystemExit('Capacity evidence must stay outside the repository')
    result=simulate(log=lambda text:print(text,flush=True))
    out.write_text(json.dumps(result,indent=2,default=str))
    final=result['days'][-1]
    ok=final['ready']>=8 and final['preferred_ready']>=6 and result['failed_runs']==0
    print('CAPACITY: '+('PASS' if ok else 'FAIL'))
    return 0 if ok else 1


if __name__=='__main__':sys.exit(main())
