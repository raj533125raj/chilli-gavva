"""CXJ-2: real slide files and visible hook/CTA, with temp-only metadata."""
import json
import tempfile
import unittest
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    Image=None

import carousel_maker
import pin_ready


@unittest.skipUnless(Image is not None,'Carousel checks need Pillow')
class CarouselGateTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        slides=[]
        for i in range(5):
            canvas=pin_ready.Canvas(1080,1350,(250,250,245))
            role='title' if i==0 else ('cta' if i==4 else 'quote')
            text='Weekly Planner' if i<4 else 'Download the planner from the shop link'
            canvas.text(role,text,100,150,880,'bold',(44,),3,(25,40,30),(250,250,245))
            name=f'slide-{i+1}.png';canvas.image.save(self.root/name)
            slides.append({'file':name,'alt':f'Planner preview page {i+1}.','layout':canvas.blocks})
        self.data={'slides':slides,'caption':'A weekly planner for calm days. #weeklyplanner'}
        self.save()

    def save(self):
        (self.root/carousel_maker.READY_FILE).write_text(json.dumps(self.data))
        (self.root/'caption.txt').write_text(self.data['caption'])
        (self.root/'alt.txt').write_text('\n'.join(f"Slide {i}: {s['alt']}" for i,s in enumerate(self.data['slides'],1)))

    def errors(self):
        self.save();return '\n'.join(carousel_maker.check_ready_carousel(self.root))

    def test_good_carousel_passes(self):
        self.assertEqual(carousel_maker.check_ready_carousel(self.root),[])

    def test_slide_count_is_five_to_eight(self):
        self.data['slides']=self.data['slides'][:4]
        self.assertIn('5-8 slides',self.errors())

    def test_dimensions_corruption_and_alt_text(self):
        Image.new('RGB',(1080,1080),'white').save(self.root/'slide-2.png')
        self.assertIn('1080x1350',self.errors())
        (self.root/'slide-3.png').write_bytes(b'broken')
        self.assertIn('teriche slide image',self.errors())
        self.data['slides'][1]['alt']=''
        self.assertIn('Alt text',self.errors())

    def test_visible_hook_and_last_slide_cta_are_required(self):
        self.data['slides'][0]['layout'][0]['role']='quote'
        self.assertIn('Slide 1',self.errors())
        self.data['slides'][-1]['layout'][0].update(text='A quiet weekly planner',lines=['A quiet weekly planner'])
        self.assertIn('Last Slide',self.errors())

    def test_caption_length_and_maximum_twenty_hashtags(self):
        self.data['caption']='x'*2201
        self.assertIn('2200 characters',self.errors())
        self.data['caption']='Planner '+ ' '.join('#tag'+str(i) for i in range(21))
        self.assertIn('20 kanna ekkuva hashtags',self.errors())
        self.data['caption']='Planner '+ ' '.join('#tag'+str(i) for i in range(20))
        self.assertEqual(self.errors(),'')

    def test_buyer_text_has_no_brands_money_or_unreadable_font(self):
        self.data['caption']='Canva planner '+chr(36)+str(12)
        self.assertIn('brand/money',self.errors())
        self.data['slides'][1]['layout'][0]['size']=12
        self.assertIn('font size',self.errors())

    def test_saved_caption_and_alt_must_match_the_checked_values(self):
        (self.root/'caption.txt').write_text('Different caption')
        self.assertIn('caption.txt','\n'.join(carousel_maker.check_ready_carousel(self.root)))
        self.save();(self.root/'alt.txt').write_text('Wrong alt\n'*5)
        self.assertIn('alt.txt','\n'.join(carousel_maker.check_ready_carousel(self.root)))

    def test_real_generator_makes_a_passing_carousel(self):
        pack=self.root/'pack';pack.mkdir()
        (pack/'listing.txt').write_text('Title: Weekly Planner Printable\nDescription: A weekly planner for priorities and notes.\nTags: weekly planner, printable, routine\nWhat you get:\n- 4 PDF planner pages\n')
        Image.new('RGB',(1200,1600),'#e8e5d8').save(pack/'cover.png')
        result=carousel_maker.make_ready_carousel(pack,self.root/'generated')
        self.assertTrue(result['check']['ok'],result['check'])


if __name__=='__main__':unittest.main()
