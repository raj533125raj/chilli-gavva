"""Ready-plan assets must reach /post today through the real plugin dispatcher."""
import datetime
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import post_planner
import telegram_inbox
import tg_posts
from tests.test_cch4_post_plan import make_ready, NOW, CHAT

class ReadyPostDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.products = Path(self.tmp.name) / "products"
        self.products.mkdir()
        self.pack = make_ready(self.products,"calm-week",(40,90,150))
        self.plan = post_planner.build_ready_plan(self.products,now=NOW)
        self.plan_path = self.products / "post_plan.json"
        post_planner.save_plan(self.plan,self.plan_path)
        self.texts=[];self.photos=[];self.groups=[]
        self.ctx={"db_path":str(Path(self.tmp.name)/"state.db"),"chat_id":CHAT,"now":NOW,
            "products_dir":str(self.products),"state_get":lambda key,default=None:default,
            "state_set":lambda key,value:None,
            "send_text":lambda text,keyboard=None:self.texts.append(text) or True,
            "send_photo":lambda path,caption="":self.photos.append((str(path),caption)) or True,
            "send_media_group":lambda paths,caption="":self.groups.append(([str(p) for p in paths],caption)) or True}
        self.carousel=next(row for row in self.plan if row["channel"]=="carousel")

    def test_dispatch_sends_every_slide_caption_and_pin_board_link(self):
        with mock.patch.object(post_planner,"PLAN_PATH",str(self.plan_path)),mock.patch.object(telegram_inbox,"build_plugin_ctx",return_value=self.ctx):
            handled,result=telegram_inbox.call_plugins("/post today","test-bot",CHAT,db_path=self.ctx["db_path"],now=NOW,plugins=[tg_posts])
        self.assertTrue(handled)
        self.assertEqual(result,{"handled":True})
        self.assertEqual(len(self.groups),1)
        self.assertEqual(self.groups[0][0],[str(Path(p).resolve()) for p in self.carousel["slides"]])
        self.assertGreaterEqual(len(self.groups[0][0]),5)
        caption=Path(self.carousel["caption_file"]).read_text()
        delivered="\n".join(self.texts)
        for line in caption.splitlines():
            if line.strip(): self.assertIn(line.strip(),delivered)
        pin=next(row for row in self.plan if row["channel"]=="pinterest" and row["date"]==self.carousel["date"])
        self.assertEqual(len(self.photos),1)
        self.assertIn("Board: "+pin["board"],self.photos[0][1])
        self.assertIn("Link: "+pin["link"],self.photos[0][1])
        self.assertTrue(all(row["status"]=="todo" for row in post_planner.load_plan(self.plan_path)))

    def test_photo_transport_fallback_still_sends_every_carousel_slide(self):
        self.ctx.pop("send_media_group")
        with mock.patch.object(post_planner,"PLAN_PATH",str(self.plan_path)):
            tg_posts.handle("/post today",self.ctx)
        expected=[str(Path(p).resolve()) for p in self.carousel["slides"]]
        self.assertEqual([path for path,_ in self.photos if path in expected],expected)
        self.assertEqual(self.groups,[])
        self.assertIn("Instagram Carousel Caption","\n".join(self.texts))

    def test_canonical_folder_supplies_all_slides_without_plan_slide_list(self):
        row=dict(self.carousel);row.pop("slides")
        folder=self.products/"marketing"/self.pack
        slides,caption=tg_posts._collect_carousel_assets(self.pack,row,folder,None)
        self.assertEqual(slides,[str(Path(p).resolve()) for p in self.carousel["slides"]])
        self.assertEqual(caption,Path(row["caption_file"]).read_text().strip())

    def test_explicit_slide_order_and_caption_file_work_without_marketing_lookup(self):
        row=dict(self.carousel);row["slides"]=list(reversed(row["slides"]))
        slides,caption=tg_posts._collect_carousel_assets(self.pack,row,None,None)
        self.assertEqual(slides,[str(Path(p).resolve()) for p in row["slides"]])
        self.assertTrue(caption)

    def test_missing_planned_slide_does_not_send_a_partial_carousel(self):
        row=dict(self.carousel);row["slides"]=list(row["slides"])+[str(self.products/"missing.png")]
        slides,caption=tg_posts._collect_carousel_assets(self.pack,row,None,None)
        self.assertEqual(slides,[])
        self.assertIn("file ledu",caption)
