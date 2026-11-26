"""Exact owner-first daily card for the reported November queue shape."""
import datetime as dt
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import daily_report
import guardrails
import product_builder

NOW=dt.datetime(2026,11,26,7,tzinfo=dt.timezone.utc)

class DailyCard(unittest.TestCase):
    def test_exact_live_shape_and_order(self):
        with tempfile.TemporaryDirectory() as temp:
            db=str(Path(temp)/'robot.db');products=Path(temp)/'products'
            with guardrails.connect(db) as conn:
                for i in range(16):
                    conn.execute("INSERT INTO approvals(ts,kind,title,status) VALUES(?,'scout_idea','Research proposal','pending')",(NOW.isoformat(),))
            for i in range(32):
                product_builder._record(db,1000+i,'needs_fix',note='truth_or_build: Failed to parse spec')
            with guardrails.connect(db) as conn:
                conn.execute('UPDATE product_builds SET ts=?',(NOW.isoformat(),))
            measured={'built':0,'upload_ready':9,'packs':[]}
            with patch('product_counts.snapshot',return_value=measured), \
                 patch('product_counts.sell_test_line',return_value='Sell test ready: 8/8 packs'), \
                 patch('llm_router.provider_reason_lines',return_value=['Cloudflare: key ledu; owner setup chudandi.']):
                text=daily_report.daily_text(db,NOW,status_fn=lambda:{},products_dir=products)
            expected='''Meeru cheyali: /listnext — ready Pack ni Gumroad lo pettandi.
Chilli Gavva daily report (2026-11-26)
Drafts: 0 made, 0 waiting, 0 approved (last 24h); Auto-decided (last 24h): 0 approved, 0 rejected
Products built: 0; Built products (last 24h): none
Product drafts waiting: 0; Proposals waiting: 16 + 0 Self-Improvement (product drafts kaadu, meere decide); Approved today: 0 (owner 0, auto 0); Needs fix today: 32 (top: truth_or_build: Failed to parse spec) — robot fix chestondi 32, owner decision kavali 0
Upload packs ready: 9 (send /packs to see them); Listed: 0
Sell test ready: 8/8 packs
GitHub minutes this month: unknown (alarm clock not reachable); Door bell: unknown; Last robot run: unknown
/buttontest median: unknown (measured presses levu); Button presses received in the last 24h: 0
Errors: none
Next step for owner: check upload packs; Next: /listnext — ready Pack ni Gumroad lo pettandi.
AI: not used in the last day
Cloudflare: key ledu; owner setup chudandi.'''
            self.assertEqual(text,expected)
            self.assertLessEqual(len(text.splitlines()),20)
    def test_max_twenty_lines_on_sunday_with_real_provider_diagnostics(self):
        with tempfile.TemporaryDirectory() as temp:
            db=str(Path(temp)/'robot.db')
            for day in (NOW,NOW+dt.timedelta(days=3)):
                text=daily_report.report_text(db,day,status_fn=lambda:{},products_dir=Path(temp)/'products')
                self.assertTrue(text.startswith('Meeru cheyali:'))
                self.assertLessEqual(len(text.splitlines()),20,text)
                self.assertGreater(text.index('AI:'),text.index('Products built:'))
