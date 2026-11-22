"""CXJ-1: generated Pin images, strict posting readiness and planner integration."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    Image = None

import pin_ready


@unittest.skipUnless(Image is not None,'Pin image checks need Pillow')
class PinGateTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name);self.path=self.root/'pin.png'
        canvas=pin_ready.Canvas(1000,1500,(250,250,245))
        canvas.text('title','Weekly Planner',100,100,800,'bold',(48,),2,(25,40,30),(250,250,245))
        canvas.image.save(self.path)
        self.pin={'file':'pin.png','title':'Weekly Planner Printable',
                  'description':'A weekly planner for daily focus. Download the pages from the shop link.',
                  'alt':'Weekly planner page with priorities and notes.',
                  'board':'Weekly Planning','link':'https://example.com/planner',
                  'main_keyword':'weekly planner','layout':canvas.blocks}

    def test_real_pin_passes(self):
        self.assertEqual(pin_ready.check_ready_pin(self.pin,self.path),[])

    def test_every_public_field_limit_board_keyword_and_cta(self):
        cases=[('title','x'*101,'title'),('description','x'*501,'description'),('alt','x'*501,'alt'),
               ('board','','board'),('description','Download a different workbook.','main keyword'),
               ('description','A weekly planner for calm days.','call to action')]
        for field,value,expected in cases:
            with self.subTest(field=field,expected=expected):
                pin=dict(self.pin);pin[field]=value
                self.assertIn(expected,'\n'.join(pin_ready.check_ready_pin(pin,self.path)))

    def test_placeholder_blank_and_credential_links_are_not_post_ready(self):
        for value in ('','[STORE LINK]','file:///private/pack','https://name:private@example.com/planner'):
            with self.subTest(link=value):
                pin=dict(self.pin,link=value)
                self.assertIn('nijamaina Store link','\n'.join(pin_ready.check_ready_pin(pin,self.path)))

    def test_wrong_dimensions_corruption_and_unreadable_font_fail(self):
        Image.new('RGB',(500,500),'white').save(self.path)
        self.assertIn('1000x1500','\n'.join(pin_ready.check_ready_pin(self.pin,self.path)))
        self.path.write_bytes(b'broken')
        self.assertIn('teriche Pin image','\n'.join(pin_ready.check_ready_pin(self.pin,self.path)))
        Image.new('RGB',(1000,1500),'white').save(self.path)
        pin=copy.deepcopy(self.pin);pin['layout'][0]['size']=12
        self.assertIn('font size','\n'.join(pin_ready.check_ready_pin(pin,self.path)))

    def test_brand_and_money_fail_without_echoing_private_text(self):
        for value in ('Canva weekly planner', 'Weekly planner '+chr(36)+str(12)):
            pin=dict(self.pin,title=value)
            self.assertTrue(pin_ready.check_ready_pin(pin,self.path))

    def test_currency_variants_are_rejected_in_metadata_and_visible_text(self):
        for amount in ('CAD'+str(12),str(12)+'EUR','AED .'+str(99),chr(8361)+str(12)):
            with self.subTest(amount=amount):
                pin=dict(self.pin,title='Weekly Planner '+amount)
                problems=pin_ready.check_ready_pin(pin,self.path)
                self.assertTrue(any('money' in problem for problem in problems),problems)
                pin=copy.deepcopy(self.pin)
                pin['layout'][0]['text']=amount
                pin['layout'][0]['lines']=[amount]
                problems=pin_ready.check_ready_pin(pin,self.path)
                self.assertTrue(any('money' in problem for problem in problems),problems)

    def test_folder_checker_reaches_singular_gate_and_refuses_external_images(self):
        data={'pins':[dict(self.pin) for _ in range(5)],'main_keyword':'weekly planner'}
        (self.root/'pins.json').write_text(json.dumps(data))
        self.assertEqual(pin_ready.check_pins(self.root),[])
        data['pins'][0]['link']='[STORE LINK]'
        (self.root/'pins.json').write_text(json.dumps(data))
        self.assertIn('Store link','\n'.join(pin_ready.check_pins(self.root)))
        data['pins'][0]['file']='../private.png'
        (self.root/'pins.json').write_text(json.dumps(data))
        self.assertIn('ee Pin folder lone','\n'.join(pin_ready.check_pins(self.root)))

    def test_real_generator_makes_a_draft_until_owner_supplies_a_link(self):
        pack=self.root/'pack';pack.mkdir()
        (pack/'listing.txt').write_text('Title: Weekly Planner Printable\nDescription: A weekly planner for daily priorities and notes.\nTags: weekly planner, printable, routine\nWhat you get:\n- 4 PDF planner pages\n')
        Image.new('RGB',(1200,1600),'#e8e5d8').save(pack/'cover.png')
        data=pin_ready.make_pins(pack,self.root/'draft')
        self.assertFalse(data['check']['ok'])
        self.assertTrue(all('Store link' in value for value in data['check']['problems']),data['check'])
        data=pin_ready.make_pins(pack,self.root/'ready',store_link='https://example.com/planner')
        self.assertTrue(data['check']['ok'],data['check'])


if __name__=='__main__':unittest.main()
