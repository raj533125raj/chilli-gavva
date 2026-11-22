"""CXJ-3: existing planner admits good assets and excludes broken ones."""
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
import post_planner


def sample_assets(products):
    """Create actual rendered images plus their checked text layout model."""
    pack=Path(products)/'marketing'/'1-sample'
    pins=pack/'pinterest';carousel=pack/'carousel'
    pins.mkdir(parents=True);carousel.mkdir()
    pin_rows=[];slides=[]
    for i in range(5):
        cv=pin_ready.Canvas(1000,1500,(250,250,245))
        cv.text('title','Weekly Planner',100,150,800,'bold',(44,),2,(20,40,30),(250,250,245))
        name=f'pin-{i+1}.png';cv.image.save(pins/name)
        pin_rows.append({'file':name,'title':'Weekly Planner Printable',
                         'description':'A weekly planner for calm days. Download the pages from the shop link.',
                         'alt':'Weekly planner page with priorities and notes.','board':'Weekly Planning',
                         'link':'https://example.com/planner','layout':cv.blocks})
        cv=pin_ready.Canvas(1080,1350,(250,250,245))
        cv.text('cta' if i==4 else 'title','Download the planner' if i==4 else 'Weekly Planner',
                100,150,880,'bold',(44,),2,(20,40,30),(250,250,245))
        name=f'slide-{i+1}.png';cv.image.save(carousel/name)
        slides.append({'file':name,'alt':f'Planner preview page {i+1}.','layout':cv.blocks})
    pin_data={'main_keyword':'weekly planner','pins':pin_rows}
    car_data={'slides':slides,'caption':'A weekly planner for calm days. #weeklyplanner','hashtags':['weeklyplanner']}
    (pins/pin_ready.META_FILE).write_text(json.dumps(pin_data))
    (carousel/carousel_maker.READY_FILE).write_text(json.dumps(car_data))
    (carousel/'caption.txt').write_text(car_data['caption'])
    (carousel/'alt.txt').write_text('\n'.join(f"Slide {i}: {s['alt']}" for i,s in enumerate(slides,1)))
    return pins,carousel,pin_data,car_data


@unittest.skipUnless(Image is not None,'Planner media checks need Pillow')
class PlannerGateTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.products=Path(tmp.name)/'products'
        self.pins,self.carousel,self.pin_data,self.car_data=sample_assets(self.products)

    def test_both_good_formats_reach_the_real_planner(self):
        rows=post_planner.ready_items(self.products)
        self.assertEqual([row['channel'] for row in rows],['pinterest']*5+['carousel'])
        self.assertEqual(len(rows[-1]['slides']),5)
        self.assertTrue(all(row['ready'] for row in rows))

    def test_bad_pin_link_stops_pin_posts_while_good_carousel_remains(self):
        self.pin_data['pins'][0]['link']='[STORE LINK]'
        (self.pins/pin_ready.META_FILE).write_text(json.dumps(self.pin_data))
        self.assertEqual([row['channel'] for row in post_planner.ready_items(self.products)],['carousel'])

    def test_broken_carousel_image_stops_carousel_while_good_pins_remain(self):
        Image.new('RGB',(500,500),'white').save(self.carousel/'slide-1.png')
        self.assertEqual([row['channel'] for row in post_planner.ready_items(self.products)],['pinterest']*5)


if __name__=='__main__':unittest.main()
