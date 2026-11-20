"""CCH-4: the 30-day post plan the owner follows by hand uses ONLY ready posts.

Ready = Pins / carousel made for a SELLABLE pack whose checker passes. "/posted N" (the existing tg_posts
command) marks a post done with the time; the next refresh keeps it; /teams counts planned and posted posts.
Nothing posts by itself.
"""
import datetime
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

import post_planner

NOW = datetime.datetime(2026, 11, 20, 4, 0, tzinfo=datetime.timezone.utc)      # 09:30 in India
CHAT = int("1234" "5678")
LISTING = """Title: {title}

Description:
A calm planner that keeps your week on one page with clear boxes for priorities and notes.

Tags: weekly planner, printable, routine

What you get:
{slug}-A4.pdf - PDF, page count: 4, A4
"""


def make_ready(products: Path, slug: str, colour, pins=True, carousel=True) -> str:
    """A pack in products/upload/<folder> plus its ready Pins / carousel in products/marketing/<folder>."""
    import carousel_maker
    import pin_ready
    folder = f"90{len(list((products / 'upload').glob('*'))) if (products / 'upload').is_dir() else 0}-{slug}"
    pack = products / "upload" / folder
    pack.mkdir(parents=True)
    (pack / "listing.txt").write_text(LISTING.format(title=slug.replace("-", " ").title(), slug=slug))
    Image.new("RGB", (1200, 1600), colour).save(pack / "cover.png")
    Image.new("RGB", (1600, 1200), (250, 250, 245)).save(pack / "preview-1.png")
    out = products / "marketing" / folder
    if pins:
        pin_ready.make_pins(pack, out / "pinterest", now=NOW)
    if carousel:
        carousel_maker.make_ready_carousel(pack, out / "carousel", now=NOW)
    return folder


@unittest.skipUnless(HAS_PIL, "ready posts need Pillow")
class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(os.path.realpath(self.tmp.name))
        self.products = self.root / "exported_content" / "products"
        (self.products / "upload").mkdir(parents=True)
        self.plan_path = self.products / "post_plan.json"

    def refresh(self, now=NOW):
        return post_planner.refresh_ready_plan(self.products, now=now)

    def plan(self):
        return json.loads(self.plan_path.read_text())


class ReadyOnlyTests(Base):
    def test_plan_holds_only_ready_posts(self):
        good = make_ready(self.products, "calm-week", (40, 90, 150))
        # a pack with old pins/ images only (not ready) and a pack whose Pins fail the checker
        old = self.products / "marketing" / "9100-old" / "pins"
        old.mkdir(parents=True)
        Image.new("RGB", (1000, 1500)).save(old / "pin_1_a.png")
        (old / "pins.json").write_text(json.dumps([{"file": "pin_1_a.png", "title": "Old"}]))
        bad = make_ready(self.products, "broken-pack", (150, 60, 40), carousel=False)
        Image.new("RGB", (500, 500)).save(self.products / "marketing" / bad / "pinterest" / "pin-2.png")

        result = self.refresh()
        self.assertEqual(result, {"changed": True, "posts": 6, "ready": 6})
        plan = self.plan()
        self.assertEqual({row["pack_id"] for row in plan}, {good})
        self.assertEqual(sorted(row["channel"] for row in plan), ["carousel"] + ["pinterest"] * 5)
        self.assertTrue(all(row["ready"] and row["status"] == "todo" for row in plan))
        days = {}
        for row in plan:
            days.setdefault((row["date"], row["channel"]), []).append(row)
            self.assertTrue("2026-11-20" <= row["date"] <= "2026-12-19", row["date"])
            self.assertTrue(Path(row["asset"]).is_file(), row["asset"])
        for (day, channel), rows in days.items():
            self.assertLessEqual(len(rows), 3 if channel == "pinterest" else 1)
            self.assertEqual(len({r["pack_id"] for r in rows}), len(rows))     # one post per pack a day
        pins = [r for r in plan if r["channel"] == "pinterest"]
        self.assertEqual(len({r["date"] for r in pins}), 5)                    # spread over the month
        self.assertTrue(all(r["board"] and r["link"] == "[STORE LINK]" for r in pins))
        car = [r for r in plan if r["channel"] == "carousel"][0]
        self.assertEqual(len(car["slides"]), len(json.loads(
            (self.products / "marketing" / good / "carousel" / "carousel.json").read_text())["slides"]))

    def test_nothing_ready_writes_nothing(self):
        self.assertEqual(self.refresh(), {"changed": False, "posts": 0, "ready": 0})
        self.assertFalse(self.plan_path.exists())

    def test_second_refresh_changes_nothing(self):
        make_ready(self.products, "calm-week", (40, 90, 150))
        self.refresh()
        stamp = self.plan_path.read_text()
        self.assertFalse(self.refresh()["changed"])
        self.assertEqual(self.plan_path.read_text(), stamp)

    def test_today_numbers_stay_when_a_new_pack_arrives(self):
        make_ready(self.products, "calm-week", (40, 90, 150))
        self.refresh()
        today_before = [r["post_id"] for r in self.plan() if r["date"] == "2026-11-20"]
        later = NOW + datetime.timedelta(hours=3)
        new = make_ready(self.products, "budget-log", (150, 60, 40))
        self.assertTrue(self.refresh(later)["changed"])
        plan = self.plan()
        self.assertEqual([r["post_id"] for r in plan if r["date"] == "2026-11-20"], today_before)
        new_rows = [r for r in plan if r["pack_id"] == new]
        self.assertEqual(len(new_rows), 6)
        self.assertTrue(all(r["date"] >= "2026-11-21" for r in new_rows))

    def test_old_rows_from_pack_images_are_dropped_but_history_stays(self):
        make_ready(self.products, "calm-week", (40, 90, 150))
        legacy = [{"day": 1, "date": "2026-11-20", "channel": "pinterest", "pack_id": "x", "asset": "cover.png",
                   "status": "todo"},
                  {"day": 1, "date": "2026-11-18", "channel": "pinterest", "pack_id": "x", "asset": "cover.png",
                   "status": "done", "posted_at": "2026-11-18T05:00:00"}]
        self.plan_path.write_text(json.dumps(legacy))
        self.refresh()
        plan = self.plan()
        self.assertNotIn("cover.png", [r["asset"] for r in plan if r["status"] == "todo"])
        self.assertIn("2026-11-18T05:00:00", [r.get("posted_at") for r in plan])


class PostedTests(Base):
    """/posted N through the real Telegram plugin path; refresh keeps the mark; /teams counts it."""

    def setUp(self):
        super().setUp()
        import guardrails
        import telegram_inbox
        import teams_report
        self.inbox = telegram_inbox
        self.db = str(self.root / "agent_data.db")
        guardrails.connect(self.db).close()
        cwd = os.getcwd()
        os.chdir(self.root)                # tg_posts reads the plan relative to the robot folder
        self.addCleanup(os.chdir, cwd)
        self.texts = []
        for patch in (
            mock.patch.object(telegram_inbox, "send_text",
                              side_effect=lambda token, chat, text, buttons=None, keyboard=None:
                              self.texts.append(text) or True),
            mock.patch.object(telegram_inbox, "_send_text_kw",
                              side_effect=lambda token, chat, text, keyboard=None: self.texts.append(text) or True),
            mock.patch.object(teams_report, "PRODUCTS_DIR", self.products),
            mock.patch.object(teams_report, "PLAN_PATH", self.plan_path),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def call(self, text, now=NOW):
        handled, reply = self.inbox.call_plugins(text, "test-token", CHAT, db_path=self.db, now=now)
        self.assertTrue(handled, text)
        return (reply or {}).get("text") or (self.texts[-1] if self.texts else "")

    def test_posted_marks_ready_post_and_teams_counts_it(self):
        self.assertEqual(Path(post_planner.PLAN_PATH), Path("exported_content/products/post_plan.json"))
        folder = make_ready(self.products, "calm-week", (40, 90, 150))
        self.refresh()
        today = [r for r in self.plan() if r["date"] == "2026-11-20"]
        self.assertTrue(today)
        reply = self.call("/posted 1")
        self.assertIn("Post 1 done", reply)
        plan = self.plan()
        row = [r for r in plan if r["date"] == "2026-11-20"][0]
        self.assertEqual(row["status"], "done")
        self.assertTrue(row["posted_at"].startswith("2026-11-"))
        # the next robot cycle keeps the mark and does not plan that post again
        self.refresh(NOW + datetime.timedelta(days=1))
        plan = self.plan()
        self.assertEqual([r["status"] for r in plan if r["post_id"] == row["post_id"]], ["done"])
        import teams_report
        stats = teams_report.team_stats(db_path=self.db, now=NOW + datetime.timedelta(days=1))
        self.assertEqual(stats["pinterest"]["posted"], 1)
        self.assertEqual(stats["pinterest"]["planned"], 4)
        self.assertEqual(stats["marketing"]["made"]["posts_posted"], 1)
        self.assertIn("1 posted", teams_report.report_text(stats, team="pinterest"))
        self.assertTrue((self.products / "marketing" / folder / "pinterest" / "pin-1.png").is_file())

    def test_missed_posts_are_planned_again_and_count_as_due(self):
        make_ready(self.products, "calm-week", (40, 90, 150))
        self.refresh()
        first = [r for r in self.plan() if r["date"] == "2026-11-20"][0]
        self.refresh(NOW + datetime.timedelta(days=2))
        plan = self.plan()
        rows = [r for r in plan if r["post_id"] == first["post_id"]]
        self.assertEqual(sorted(r["status"] for r in rows), ["missed", "todo"])
        import teams_report
        stats = teams_report.team_stats(db_path=self.db, now=NOW + datetime.timedelta(days=2))
        self.assertEqual(stats["marketing"]["waiting"]["overdue"], 0)          # it was moved, not lost
        self.assertEqual(stats["marketing"]["percent"], 0)                       # due and not posted

    def test_today_text_shows_ready_posts_only(self):
        make_ready(self.products, "calm-week", (40, 90, 150))
        text = self.inbox.today_posts_text(plan_path=str(self.plan_path), products_dir=str(self.products),
                                           date="2026-11-20")
        self.assertIn("Today post (1 pins, 1 Instagram):", text)
        self.assertIn("pin-1.png [board:", text)
        self.assertIn("slide-1.png", text)
        self.assertNotIn(str(self.root), text)


@unittest.skipUnless(HAS_PIL, "ready posts need Pillow")
class CycleHookTests(Base):
    def test_run_ready_refreshes_the_plan_in_the_products_folder(self):
        import guardrails
        import marketing_auto
        make_ready(self.products, "calm-week", (40, 90, 150))
        db = str(self.root / "agent_data.db")
        guardrails.connect(db).close()
        marketing_auto.run_ready(db, products_dir=self.products, now=NOW)
        self.assertEqual(len(self.plan()), 6)

    def test_mark_posted_keeps_first_time(self):
        plan = [{"date": "2026-11-20", "status": "todo"}]
        self.assertTrue(post_planner.mark_posted(plan, "2026-11-20", 1, now=NOW))
        self.assertEqual(plan[0]["posted_at"], "2026-11-20T04:00:00")
        post_planner.mark_posted(plan, "2026-11-20", 1, now=NOW + datetime.timedelta(hours=5))
        self.assertEqual(plan[0]["posted_at"], "2026-11-20T04:00:00")


if __name__ == "__main__":
    unittest.main()
