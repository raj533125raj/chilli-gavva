"""Run real owner flows at 19:45, 01:15 and 07:00 UTC, without changing the host clock."""
import datetime as dt
import tempfile
import types
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest import mock

try:
    from PIL import Image
except ImportError:
    Image=None

MOMENTS = [dt.datetime(2026,11,23,h,m,tzinfo=dt.timezone.utc) for h,m in ((19,45),(1,15),(7,0))]


@contextmanager
def frozen(moment):
    import telegram_inbox, post_planner, daily_report, guardrails, product_builder, product_pipeline, marketing_auto
    class ClockType(type):
        def __instancecheck__(cls,value):return isinstance(value,dt.datetime)
    class DayType(type):
        def __instancecheck__(cls,value):return isinstance(value,dt.date)
    class Clock(dt.datetime,metaclass=ClockType):
        @classmethod
        def now(cls,tz=None):
            value=cls.fromtimestamp(moment.timestamp(),dt.timezone.utc)
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)
        @classmethod
        def utcnow(cls):return cls.now(dt.timezone.utc).replace(tzinfo=None)
    class Day(dt.date,metaclass=DayType):
        @classmethod
        def today(cls):return cls.fromisoformat(moment.date().isoformat())
    clock=types.SimpleNamespace(datetime=Clock,date=Day,timedelta=dt.timedelta,timezone=dt.timezone,time=dt.time)
    with ExitStack() as stack:
        for module in (telegram_inbox,post_planner,daily_report,guardrails,product_builder,product_pipeline,marketing_auto):
            stack.enter_context(mock.patch.object(module,'datetime',clock))
        stack.enter_context(mock.patch('time.time',return_value=moment.timestamp()))
        yield


@unittest.skipUnless(Image is not None,'Pillow required for real owner assets')
class ClockSafetyTests(unittest.TestCase):
    def journey(self,moment):
        from tests.test_ocf_owner_journey import OwnerJourneyTests
        with frozen(moment):
            case=OwnerJourneyTests('test_list_approve_build_sell_listed_today')
            result=unittest.TestResult();case.run(result)
        self.assertEqual(result.testsRun,1)
        self.assertEqual(result.failures+result.errors,[])
        self.assertEqual(result.skipped,[])

    def week(self,moment):
        from tests.test_cxk_owner_week import OwnerWeekTests
        import post_planner
        with frozen(moment):
            fixture=OwnerWeekTests('test_seven_days_preserve_decisions_posts_and_owner_control')
            try:
                fixture.setUp()
                fixture.now=moment;fixture.ctx['now']=moment
                day=post_planner.owner_today(moment).isoformat()
                items=post_planner.ready_items(fixture.products)
                self.assertTrue(items)
                row=dict(items[0],day=1,date=day,status='todo')
                post_planner.save_plan([row],post_planner.PLAN_PATH)
                fixture.exchange('/today')
                fixture.exchange('/post week')
                fixture.exchange('/posted 1')
                plan=post_planner.load_plan(post_planner.PLAN_PATH)
                done=next(p for p in plan if p.get('post_id')==row['post_id'])
                self.assertEqual(done['status'],'done')
                self.assertEqual(done['date'],day)
                self.assertEqual(done['posted_at'],moment.replace(tzinfo=None).isoformat(timespec='seconds'))
            finally:fixture.doCleanups()

    def posts(self,moment):
        import post_planner
        import tg_posts
        from tests.test_cch4_post_plan import make_ready
        from tests.test_cxx_selltest import CHAT
        with tempfile.TemporaryDirectory() as temp, frozen(moment):
            products=Path(temp)/'products'
            make_ready(products,'clock-focus',(40,90,150))
            plan=post_planner.build_ready_plan(products,now=moment)
            path=post_planner.plan_path_for(products);post_planner.save_plan(plan,path)
            expected=moment.astimezone(dt.timezone(dt.timedelta(hours=5,minutes=30))).date().isoformat()
            self.assertEqual(plan[0]['date'],expected)
            texts=[];photos=[]
            ctx={'products_dir':str(products),'chat_id':CHAT,'now':moment,
                 'send_text':lambda t,*a,**k:texts.append(t),'send_photo':lambda p,**k:photos.append(p),
                 'send_media_group':lambda p,**k:None,
                 'state_get':lambda k,d=None:{'quiet_hours':{'enabled':True,'start':'22:00','end':'08:00'}} if k=='owner_settings' else d}
            result=tg_posts.handle_post_today(ctx,plan_path=path,products_dir=str(products))
            if moment.hour in (19,1):
                self.assertIn('Quiet hours',result['text']);self.assertEqual(photos,[])
            else:
                self.assertTrue(photos)
                self.assertTrue(any(expected in text for text in texts))

    def daily_selltest(self,moment):
        import daily_report, post_planner, selltest, tg_selltest
        from tests.test_cxx_selltest import choices, CHAT
        base=dt.datetime(2026,11,23,7,tzinfo=dt.timezone.utc)
        with tempfile.TemporaryDirectory() as temp, frozen(moment):
            db=str(Path(temp)/'test.db')
            with mock.patch.object(selltest,'candidates',return_value=choices()):selltest.start(db,temp,base)
            day=post_planner.owner_today(moment).isoformat()
            self.assertEqual(day,'2026-11-24' if moment.hour==19 else '2026-11-23')
            result=tg_selltest.handle('/selltest stats 1 views 120 sales 1',{'db_path':db,'products_dir':temp,'now':moment,'chat_id':CHAT})
            self.assertIn(day,result['text'])
            self.assertEqual(selltest.stats(db)[0]['day'],day)
            report=daily_report.daily_text(db,moment,status_fn=lambda:{},products_dir=temp)
            self.assertIn('Sell test day '+('2' if moment.hour==19 else '1')+'/14',report)
            self.assertEqual(daily_report.delivery_day(moment),day)
            self.assertEqual(daily_report.due(None,moment),moment.hour==7)

    def urgent(self,moment):
        from tests import test_urgent_telegram as urgent
        from tests.test_cxx_selltest import CHAT
        import notify, guardrails
        with frozen(moment):
            fixture=urgent.TelegramIntegrationTests('test_button_approves_real_draft_and_sends_reply_through_http')
            try:
                fixture.setUp()
                fixture.enterContext(mock.patch.object(notify,'_telegram_configured',return_value=(True,'unused',CHAT)))
                aid=guardrails.request_approval('public_draft','Clock test draft','Owner preview',db_path=fixture.db)
                server=urgent.Telegram([{'update_id':10,'callback_query':{'id':'clock-button','data':f'a:{aid}',
                    'message':{'chat':{'id':int(CHAT)}}}}])
                self.assertEqual(fixture.run_server(server,recovery=False)['commands'],1)
                self.assertEqual(guardrails.get_approval(aid,fixture.db)['status'],'approved')
                self.assertTrue(server.sent)
                self.assertTrue(all(str(p['chat_id'])==CHAT for p in server.sent))
                self.assertEqual(fixture.run_server(server,recovery=False)['commands'],0)
            finally:fixture.doCleanups()


for family in ('journey','week','posts','daily_selltest','urgent'):
    for moment in MOMENTS:
        def test(self,family=family,moment=moment):getattr(self,family)(moment)
        setattr(ClockSafetyTests,'test_'+family+'_'+moment.strftime('%H%M')+'_UTC',test)
