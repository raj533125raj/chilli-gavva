"""CXH-2: bounded waiting without bypassing score, owner or checker safety."""
import datetime
from unittest import mock
import unittest

import auto_approve
import guardrails
import improver_loop
import workforce
from tests.test_cxh_queue_probe import QueueFixture, NOW


class AutoDecisionTests(QueueFixture):
    def reason(self, aid):
        conn = guardrails.connect(self.db)
        try:
            return conn.execute("SELECT reason FROM approvals WHERE id=?", (aid,)).fetchone()[0]
        finally:
            conn.close()

    def rule(self, hours=24, status="proposed"):
        conn = improver_loop.connect(self.db)
        try:
            row = conn.execute("INSERT INTO learned_rules(rule_text,status,created_ts) VALUES (?,?,?)",
                               ("Improve page contrast", status, (NOW-datetime.timedelta(hours=hours)).isoformat()))
            conn.commit()
            return row.lastrowid
        finally:
            conn.close()

    def test_exact_24_hour_boundary(self):
        young = self.add("Young printable", 91, 24-1/3600)
        due = self.add("Due workbook", 89, 24)
        decisions = auto_approve.auto_decide_drafts(self.db, NOW)
        self.assertEqual([(d["id"], d["action"]) for d in decisions], [(due,"approved")])
        self.assertEqual(guardrails.get_approval(young,self.db)["status"], "pending")

    def test_low_score_ignores_daily_limit_and_keeps_checker_reason(self):
        auto_approve.save_settings({"enabled":True,"threshold":80,"daily_limit":0},self.db)
        aid=self.add("Unsupported builder format",40,24,verdict="reject")
        self.assertEqual(auto_approve.auto_decide_drafts(self.db,NOW)[0]["action"],"rejected")
        self.assertIn("Format check chudandi",self.reason(aid))

    def test_daily_limit_has_a_72_hour_escape(self):
        auto_approve.save_settings({"enabled":True,"threshold":70,"daily_limit":0},self.db)
        young=self.add("Waiting quota planner",87,71)
        due=self.add("Overdue quota workbook",91,72)
        low=self.add("Below owner threshold",40,100)
        actions={d["id"]:d["action"] for d in auto_approve.auto_decide_drafts(self.db,NOW)}
        self.assertEqual(actions,{due:"approved",low:"rejected"})
        self.assertEqual(guardrails.get_approval(young,self.db)["status"],"pending")

    def test_quota_escape_does_not_bypass_revise_or_similarity(self):
        auto_approve.save_settings({"enabled":True,"threshold":70,"daily_limit":0},self.db)
        original=self.add("Exact duplicate worksheet",91,100)
        guardrails.decide(original,True,self.db)
        duplicate=self.add("Exact duplicate worksheet",88,80)
        revise=self.add("Needs extra checking",85,90,verdict="revise")
        self.assertEqual(auto_approve.auto_decide_drafts(self.db,NOW),[])
        reasons={r["id"]:r["why"] for r in auto_approve._plan(self.db,NOW)}
        self.assertEqual(reasons,{duplicate:"similar",revise:"checker_revise"})

    def test_broken_checker_rejects_after_three_continuous_days(self):
        ids=[self.add(f"Independent subject {i}",88,30+i) for i in range(12)]
        self.assertEqual(auto_approve.auto_decide_drafts(self.db,NOW),[])
        self.assertEqual(auto_approve.auto_decide_drafts(self.db,NOW+datetime.timedelta(hours=71)),[])
        decisions=auto_approve.auto_decide_drafts(self.db,NOW+datetime.timedelta(hours=72))
        self.assertEqual({d["id"] for d in decisions},set(ids))
        self.assertEqual({d["action"] for d in decisions},{"rejected"})
        self.assertEqual({self.reason(aid) for aid in ids},{"checker problem"})
        self.assertEqual(auto_approve.auto_decide_drafts(self.db,NOW+datetime.timedelta(hours=73)),[])

    def test_recovered_checker_resets_the_three_day_timer(self):
        for i in range(5): self.add(f"Identical check {i}",88,30+i)
        auto_approve.auto_decide_drafts(self.db,NOW)
        with mock.patch.object(auto_approve,"has_broken_checker_signal",return_value=False):
            auto_approve._checker_since(self.db,NOW+datetime.timedelta(days=1),track=True)
        self.assertIsNone(auto_approve._checker_since(self.db))
        auto_approve._checker_since(self.db,NOW+datetime.timedelta(days=2),track=True)
        self.assertEqual(auto_approve._checker_since(self.db),NOW+datetime.timedelta(days=2))

    def test_self_improvement_expires_without_being_applied(self):
        old=self.rule(24); young=self.rule(23); approved=self.rule(90,"approved")
        with mock.patch.object(improver_loop,"decide_proposal",side_effect=AssertionError("never apply")):
            decisions=auto_approve.auto_decide_drafts(self.db,NOW)
        self.assertEqual([(d["id"],d["action"]) for d in decisions],[(f"imp:{old}","rejected")])
        conn=improver_loop.connect(self.db)
        try:
            rows={r[0]:r[1:] for r in conn.execute("SELECT id,status,reason FROM learned_rules")}
            self.assertEqual(rows[old],("rejected","owner time ayipoyindi"))
            self.assertEqual(rows[young][0],"proposed");self.assertEqual(rows[approved][0],"approved")
        finally: conn.close()

    def test_owner_decision_pause_and_disabled_setting_win(self):
        aid=self.add("Owner declined workbook",90,100)
        guardrails.decide(aid,False,self.db,reason="Other")
        rule=self.rule(80)
        guardrails.set_paused(True,self.db)
        self.assertEqual(auto_approve.auto_decide_drafts(self.db,NOW),[])
        self.assertEqual(auto_approve.waiting_rule_proposals(self.db),1)
        guardrails.set_paused(False,self.db)
        other=self.add("Disabled workbook",89,100)
        auto_approve.save_settings({"enabled":False,"threshold":70,"daily_limit":2},self.db)
        result=auto_approve.auto_decide_drafts(self.db,NOW)
        self.assertEqual([d["id"] for d in result],[f"imp:{rule}"])
        self.assertEqual(guardrails.get_approval(other,self.db)["status"],"pending")
        self.assertEqual(self.reason(aid),"Other")

    def test_fake_clock_is_saved_and_limits_the_next_run(self):
        for i,score in enumerate((81,83,86,89)): self.add(f"Unique print subject {i}",score,30+i)
        first=auto_approve.auto_decide_drafts(self.db,NOW)
        self.assertEqual(len(first),2)
        self.assertEqual(auto_approve.count_auto_approvals_today(self.db,NOW),2)
        self.assertEqual(auto_approve.auto_decide_drafts(self.db,NOW),[])
        for d in first: self.assertEqual(guardrails.get_approval(d["id"],self.db)["decided_ts"],NOW.isoformat())

    def test_queued_draft_creation_stops_before_calling_ai(self):
        for i in range(11): self.add(f"Waiting item {i}",80+i,3)
        with mock.patch.object(workforce,"_ask",side_effect=AssertionError("AI must not run")):
            result=workforce.step_draft({"payload":{"topic":"Weekly planner"}},{"db_path":self.db})
        self.assertEqual(result["next"],[])
        self.assertIn("Waiting 11",result["summary"])
        self.assertEqual(len(guardrails.pending(self.db)),11)


if __name__=="__main__": unittest.main()
