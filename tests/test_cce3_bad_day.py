"""CCE-3: bad-day failures the simulator injects never crash the robot, give one owner line, and recover."""
import errno
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import guardrails


class DiskFullTests(unittest.TestCase):
    """Sim: disk full while writing product files -> OSError traceback in product_builder.write_index (red
    build step) and the draft's daily build slot was used up, so it waited a whole day."""

    def test_builder_main_does_not_crash_and_tells_the_owner(self):
        import product_builder as pb
        sent = []
        full = OSError(errno.ENOSPC, "No space left on device")
        with mock.patch.object(pb, "_build_all", side_effect=full), \
                mock.patch.object(pb.guardrails, "is_killed", return_value=False), \
                mock.patch.object(pb.guardrails, "automation_allowed", return_value=True), \
                mock.patch("cubes.get_cube_state", return_value="on"), \
                mock.patch("rest_window.in_rest_window", return_value=False), \
                mock.patch("notify.send", side_effect=lambda text, db_path=None: sent.append(text) or True):
            pb.main([])
        self.assertEqual(sent, [pb.DISK_FULL_MESSAGE])
        self.assertIn("disk full ayyindi", sent[0])

    def test_other_os_errors_still_fail_loudly(self):
        import product_builder as pb
        with mock.patch.object(pb, "_build_all", side_effect=PermissionError(errno.EACCES, "denied")), \
                mock.patch.object(pb.guardrails, "is_killed", return_value=False), \
                mock.patch.object(pb.guardrails, "automation_allowed", return_value=True), \
                mock.patch("cubes.get_cube_state", return_value="on"), \
                mock.patch("rest_window.in_rest_window", return_value=False):
            with self.assertRaises(PermissionError):
                pb.main([])

    def test_pipeline_gives_back_the_daily_slot_on_disk_full(self):
        import product_pipeline
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "a.db")
            aid = guardrails.request_approval("public_draft", "Draft: [Budget Sheets Agent] Sheet", "body",
                                              db_path=db)
            guardrails.decide(aid, True, db)
            full = OSError(errno.ENOSPC, "No space left on device")
            with mock.patch.object(product_pipeline, "_allowed", return_value=True), \
                    mock.patch("workforce.default_llm", return_value=lambda *a, **k: ""), \
                    mock.patch("format_registry.can_build", side_effect=full):
                with self.assertRaises(OSError):
                    product_pipeline.run_pending(db_path=db, out_dir=os.path.join(tmp, "p"), day="2026-11-16")
            conn = sqlite3.connect(db)
            attempts = conn.execute("SELECT COUNT(*) FROM product_delivery_attempts").fetchone()[0]
            status = conn.execute("SELECT status, lease_until FROM product_deliveries WHERE approval_id=?",
                                  (aid,)).fetchone()
            conn.close()
            self.assertEqual(attempts, 0)                 # the next run may try again today
            self.assertEqual(status, ("retry", 0))
            self.assertIn(aid, [item["id"] for item in product_pipeline.pending(db)])


if __name__ == "__main__":
    unittest.main()
