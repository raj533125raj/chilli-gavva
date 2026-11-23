"""Real checked assets, saved links, rotation and owner-confirmed posting history."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import post_planner
import selltest
import tg_listed
from tests.test_cxx_selltest import NOW, CHAT
from tests.test_cch4_post_plan import make_ready, HAS_PIL


@unittest.skipUnless(HAS_PIL,'Pillow required')
class SellPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.products=self.root/'products';self.db=str(self.root/'test.db')
        self.packs=[]
        for i in range(8):
            folder=make_ready(self.products,'focus-'+str(i),(40,90,150))
            aid=int(folder.split('-')[0]);self.packs.append({'id':aid,'title':'Focus Planner '+str(i),'folder':folder,'format':'planner','listing_score':100,'ready_score':95})
            with tg_listed._connect(self.db) as conn:
                conn.execute("INSERT INTO approvals(id,kind,title,payload,status) VALUES(?,'public_draft',?,'{}','approved')",(aid,'Focus Planner '+str(i)))
            self.assertTrue(tg_listed.set_listed(aid,'https://example.gumroad.com/l/focus-'+str(i),self.db,NOW)['ok'])
        with mock.patch.object(selltest,'candidates',return_value=self.packs):
            selltest.start(self.db,self.products,NOW)

    def refresh(self,now=NOW):
        post_planner.refresh_ready_plan(self.products,now=now,db_path=self.db)
        return post_planner.load_plan(post_planner.plan_path_for(self.products))

    def test_every_day_has_three_pins_and_one_carousel_for_only_selected_packs(self):
        plan=self.refresh();self.assertEqual(len(plan),56)
        for day in range(1,15):
            rows=[p for p in plan if p['day']==day]
            self.assertEqual([r['channel'] for r in rows],['pinterest']*3+['carousel'])
            self.assertEqual(len({r['pack_id'] for r in rows[:3]}),3)
        pins=[p for p in plan if p['channel']=='pinterest']
        self.assertEqual(len({p['title'] for p in pins}),42)
        self.assertLessEqual(max(sum(p['pack_id']==f for p in pins) for f in {p['pack_id'] for p in pins}),6)
        for row in plan:
            index=next(i for i,p in enumerate(self.packs) if p['folder']==row['pack_id'])
            self.assertEqual(row['link'],'https://example.gumroad.com/l/focus-'+str(index)+('?utm_source=pinterest&utm_campaign=selltest' if row['channel']=='pinterest' else ''))
            self.assertTrue(Path(row['asset']).is_file())
        self.assertTrue(post_planner.mark_posted(plan,date=NOW.date(),n=1,now=NOW))
        post_planner.save_plan(plan,post_planner.plan_path_for(self.products))
        refreshed=self.refresh();self.assertEqual(refreshed[0]['status'],'done')
        self.assertEqual(refreshed[0]['posted_at'],plan[0]['posted_at'])

    def test_missing_link_becomes_action_and_loader_sends_no_pin_for_it(self):
        import telegram_inbox
        import tg_posts
        aid=self.packs[0]['id'];tg_listed.remove_listed(aid,self.db)
        plan=self.refresh();row=plan[0]
        self.assertEqual(row['channel'],'action');self.assertIn(f'first /listed {aid} <link>',row['notice'])
        self.assertFalse(post_planner.mark_posted(plan,date=NOW.date(),n=1,now=NOW))
        texts=[];photos=[]
        ctx={'db_path':self.db,'products_dir':str(self.products),'chat_id':CHAT,'now':NOW,
             'send_text':lambda t,*a,**k:texts.append(t),'send_photo':lambda p,**k:photos.append(p),
             'send_media_group':lambda p,**k:None,'state_get':lambda k,d=None:d}
        with mock.patch.object(telegram_inbox,'build_plugin_ctx',return_value=ctx),mock.patch.dict('os.environ',{'TELEGRAM_CHAT_ID':CHAT}), \
             mock.patch.object(post_planner,'PLAN_PATH',str(post_planner.plan_path_for(self.products))):
            handled,_=telegram_inbox.call_plugins('/post today','unused',CHAT,db_path=self.db,now=NOW,plugins=[tg_posts])
        self.assertTrue(handled);self.assertTrue(any('first /listed' in t for t in texts))
        self.assertEqual(len(photos),2)
        tg_listed.set_listed(aid,'https://example.gumroad.com/l/relinked',self.db,NOW)
        self.assertEqual(self.refresh()[0]['link'],'https://example.gumroad.com/l/relinked?utm_source=pinterest&utm_campaign=selltest')
