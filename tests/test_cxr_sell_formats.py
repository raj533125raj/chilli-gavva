"""Registered formats remain covered by proof, buyer profiles and private marketing."""
import tempfile
import unittest
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    Image = None

import format_registry
import listing_lint
import pin_ready
import carousel_maker
from tools import sell_proof

NEW_FORMATS = ('teacher_planner', 'fitness_planner', 'baby_tracker',
               'small_business_log', 'kids_chore_reward')


class RegistryCoverageTests(unittest.TestCase):
    def test_proof_specs_cover_every_registered_format(self):
        self.assertEqual(set(sell_proof.FORMAT_ORDER), set(format_registry.list_registered_formats()))
        for kind in format_registry.list_registered_formats():
            self.assertEqual(sell_proof.spec_for(kind)['kind'], kind)


@unittest.skipUnless(Image is not None, 'needs Pillow')
class NewFormatsSellTests(unittest.TestCase):
    def test_upload_pack_uses_its_recorded_buyer_profile_after_title_sanitization(self):
        import store_kits
        with tempfile.TemporaryDirectory() as tmp:
            row = sell_proof.prove('kids_chore_reward', Path(tmp))
            self.assertIn(row['status'], ('SELLABLE','ALMOST'), row)
            pack = Path(row['pack'])
            info = store_kits.parse_listing_file(pack/'listing.txt')
            self.assertEqual(store_kits.gumroad_product_type(pack,info),'kids_chore_reward')
            self.assertEqual(store_kits.gumroad_copy(pack,info)['who_for'],
                             store_kits.GUMROAD_TYPES['kids_chore_reward']['audience'])

    def check_format(self, kind):
        with tempfile.TemporaryDirectory() as tmp:
            from tests.test_och_niche_sweep import build_variant
            built = build_variant(kind, 'student', Path(tmp))
            pack = built['pack']
            import truth_check
            truth = truth_check.check(str(pack))
            self.assertEqual(truth["verdict"], "ready", truth)
            quality = listing_lint.quality_report(pack)
            self.assertTrue(quality['ok'], quality)
            pins = pin_ready.make_pins(pack, Path(tmp)/'pins', store_link='https://example.com/pack')
            self.assertTrue(pins['check']['ok'], pins['check'])
            carousel = carousel_maker.make_ready_carousel(pack, Path(tmp)/'carousel')
            self.assertTrue(carousel['check']['ok'], carousel['check'])


def attach(kind):
    def test(self):
        self.check_format(kind)
    setattr(NewFormatsSellTests, 'test_'+kind, test)


for kind in NEW_FORMATS:
    attach(kind)
