"""CCE-2: one test per root cause the day simulator (tools/robot_day_sim.py) found."""
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import guardrails


class FreshDatabaseMarketingTests(unittest.TestCase):
    """Sim: 'Marketing agency skipped: OperationalError' at marketing_agency.py:92 on the first run."""

    def test_fresh_database_has_no_ready_products_and_no_crash(self):
        import marketing_agency
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "agent_data.db")
            guardrails.connect(db).close()
            self.assertEqual(marketing_agency._get_ready_products(db_path=db), [])
            conn = sqlite3.connect(db)
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            conn.close()
            self.assertNotIn("product_builds", tables)   # reading never creates another module's table

    def test_built_products_are_still_found(self):
        import marketing_agency
        import product_builder
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "agent_data.db")
            aid = guardrails.request_approval("public_draft", "Draft: [Budget Sheets Agent] Sheet", "body",
                                              db_path=db)
            guardrails.decide(aid, True, db)
            product_builder._record(db, aid, "built", f"{aid}-sheet", "ready")
            self.assertEqual([p["id"] for p in marketing_agency._get_ready_products(db_path=db)], [aid])


class RebuildSpecTargetTests(unittest.TestCase):
    """Sim: specs/rebuild/1..3-v2.json built ADHD planners into a new database's draft #1-#3 (a Budget sheet the
    owner approved later, and two PENDING drafts), and the owner's real draft #1 was never built."""

    def _spec_dir(self, tmp):
        import shutil
        from pathlib import Path
        specs = os.path.join(tmp, "specs")
        os.makedirs(specs)
        shutil.copy(str(Path(__file__).resolve().parents[1] / "specs" / "rebuild" / "1-v2.json"), specs)
        return specs

    def _draft(self, db, title, approve):
        aid = guardrails.request_approval("public_draft", title, "body", db_path=db)
        if approve:
            guardrails.decide(aid, True, db)
        return aid

    def test_other_niche_or_pending_draft_is_never_overwritten(self):
        import product_builder as pb
        for title, approve in (("Draft: [Budget Sheets Agent] Couple budget", True),
                               ("Draft: [ADHD Planners Agent] Energy planner", False)):
            with self.subTest(title=title, approve=approve), tempfile.TemporaryDirectory() as tmp:
                db, out = os.path.join(tmp, "a.db"), os.path.join(tmp, "products")
                self.assertEqual(self._draft(db, title, approve), 1)
                with mock.patch.object(pb, "build_product") as build:
                    self.assertEqual(pb.rebuild_versions(db, out, self._spec_dir(tmp), notify_fn=lambda m: True), [])
                build.assert_not_called()
                self.assertEqual(pb.builds(db), [])

    def test_missing_draft_without_an_earlier_build_is_skipped(self):
        import product_builder as pb
        with tempfile.TemporaryDirectory() as tmp:
            db, out = os.path.join(tmp, "a.db"), os.path.join(tmp, "products")
            with mock.patch.object(pb, "build_product") as build:
                self.assertEqual(pb.rebuild_versions(db, out, self._spec_dir(tmp), notify_fn=lambda m: True), [])
            build.assert_not_called()

    def test_the_real_approved_adhd_draft_still_gets_its_new_version(self):
        import product_builder as pb
        with tempfile.TemporaryDirectory() as tmp:
            db, out = os.path.join(tmp, "a.db"), os.path.join(tmp, "products")
            self._draft(db, "Draft: [ADHD Planners Agent] printable energy-based daily planner", True)
            with mock.patch.object(pb, "build_product") as build, \
                    mock.patch.object(pb, "truth_check_folder", return_value={"verdict": "ready"}):
                done = pb.rebuild_versions(db, out, self._spec_dir(tmp), notify_fn=lambda m: True)
            self.assertEqual(done, [(1, "1-v2-energy-daily-planner", "ready")])
            build.assert_called_once()


class DatedOwnerHoldTests(unittest.TestCase):
    """Sim: the owner approved a new draft #4 (ADHD planner); OWNER_HOLDS = {4, 6} from 2026-10-16 made the
    pipeline skip it forever without a word."""

    def _db_with_draft_4(self, tmp, made):
        db = os.path.join(tmp, "a.db")
        for n in range(1, 5):
            aid = guardrails.request_approval("public_draft", f"Draft: [ADHD Planners Agent] Planner {n}", "body",
                                              db_path=db)
            guardrails.decide(aid, True, db)
        conn = sqlite3.connect(db)
        conn.execute("UPDATE approvals SET ts = ? WHERE id = 4", (made,))
        conn.commit()
        conn.close()
        return db

    def test_new_draft_with_a_held_number_is_built_and_announced(self):
        import product_builder as pb
        import product_pipeline
        with tempfile.TemporaryDirectory() as tmp:
            db = self._db_with_draft_4(tmp, "2026-11-15T14:05:00")
            self.assertFalse(pb.is_held(4, db))
            self.assertIn(4, [item["id"] for item in product_pipeline.pending(db)])

    def test_the_original_held_draft_stays_not_for_sale(self):
        import product_builder as pb
        import product_pipeline
        for made in ("2026-10-12T09:00:00", "", "not a date"):
            with self.subTest(made=made), tempfile.TemporaryDirectory() as tmp:
                db = self._db_with_draft_4(tmp, made)
                self.assertTrue(pb.is_held(4, db))
                self.assertNotIn(4, [item["id"] for item in product_pipeline.pending(db)])
                self.assertIn(3, [item["id"] for item in product_pipeline.pending(db)])

    def test_holds_without_a_date_always_apply(self):
        import product_builder as pb
        with tempfile.TemporaryDirectory() as tmp:
            db = self._db_with_draft_4(tmp, "2026-11-15T14:05:00")
            with mock.patch.dict(pb.OWNER_HOLDS, {3: "Owner hold"}):
                self.assertTrue(pb.is_held(3, db))
            self.assertFalse(pb.is_held(2, db))


if __name__ == "__main__":
    unittest.main()
