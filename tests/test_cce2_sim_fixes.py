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


if __name__ == "__main__":
    unittest.main()
