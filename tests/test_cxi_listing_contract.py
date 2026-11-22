"""CXI-1: listing gate interfaces used by the existing sell flow."""
import tempfile
import unittest
from pathlib import Path

import listing_lint
import listing_fields
import sell_ready
import seo_score


class ListingContractTests(unittest.TestCase):
    def test_sell_ready_uses_folder_gate_and_its_measured_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            pack=Path(tmp)
            (pack/'listing.txt').write_text('Title: Planner\nDescription:\nA planner.\nTags: planner\n')
            rows,ok=listing_lint.lint_folder(pack)
            result=sell_ready.inspect_listing_lint(pack)
            fails=sum(level=='FAIL' for _,level,_ in rows)
            warns=sum(level=='WARN' for _,level,_ in rows)
            self.assertFalse(ok)
            self.assertEqual(result['score'],max(0,100-fails*20-warns*5))
            self.assertTrue(result['issues'])

    def test_pack_scorer_and_owner_fields_keep_their_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            pack=Path(tmp)
            (pack/'listing.txt').write_text('Title: Weekly Planner Printable\nDescription:\nPlan a week.\nTags: weekly planner, printable, organizer\n')
            result=seo_score.score_pack_folder(pack)
            self.assertTrue(0<=result['score']<=100)
            self.assertIn('tenglish_summary',result)
            output=listing_fields.build_blocks('Weekly Planner','Plan a week.','planner, printable, organizer','gumroad')
            self.assertIn('Name: Weekly Planner',output)
            self.assertIn('Price: SET BY OWNER',output)


if __name__=='__main__':unittest.main()
