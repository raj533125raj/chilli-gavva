"""Visual audit must detect actual defective pages, not only count files."""
from pathlib import Path
import shutil
import tempfile
import unittest
from tools import visual_qa as qa


@unittest.skipUnless(qa.available(),'Pillow and Poppler required')
class VisualDetectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def pdf(self,name='fixture_A4.pdf'):
        import pdf_design as pd
        pdf=pd.Pdf('A4')
        pdf.new_page()
        pdf.new_page();pdf.text(100,450,'Overlapping text',size=24);pdf.text(100,450,'Second text',size=24)
        pdf.new_page();pdf.text(0,450,'Text cut at edge',size=24)
        pdf.new_page();pdf.text(50,750,'Writing page',size=22)
        for y in range(70,720,24):pdf.line(50,y,540,y)
        path=self.root/name;pdf.save(path)
        return path

    def test_every_page_is_rendered_and_bad_pages_are_flagged(self):
        report=qa.check_pdf(self.pdf(),self.root/'renders')
        self.assertEqual(len(report['pages']),4)
        issues={r['page']:' '.join(f['issue'] for f in report['flags'] if f['page']==r['page']) for r in report['pages']}
        self.assertIn('blank or near-blank',issues[1])
        self.assertIn('empty bottom exceeds 35%',issues[1])
        self.assertIn('overlapping text',issues[2])
        self.assertIn('text cut at edge',issues[3])
        self.assertEqual(issues[4],'')
        self.assertTrue(all(Path(p['raster']).is_file() for p in report['pages']))

    def test_declared_paper_size_is_checked(self):
        report=qa.check_pdf(self.pdf('fixture_Letter.pdf'),self.root/'renders')
        self.assertEqual(sum(f['issue']=='wrong page size' for f in report['flags']),4)

    def test_identical_cover_and_preview_are_detected(self):
        from PIL import Image,ImageDraw
        image=Image.new('RGB',(1000,1000),'white');ImageDraw.Draw(image).rectangle((50,50,950,950),outline='black',width=12)
        image.save(self.root/'cover.png');image.save(self.root/'preview_1.png')
        report=qa.check_pack(self.root,self.root/'renders','sheets')
        self.assertTrue(any('same as preview' in f['issue'] for f in report['flags']))
        self.assertEqual(report['pdf_pages'],0)
        self.assertEqual(report['previews'],1)

    def test_report_must_not_be_inside_repository(self):
        with self.assertRaises(ValueError):qa.audit(qa.ROOT/'visual-report',[])

@unittest.skipUnless(qa.available(),'Pillow and Poppler required')
class AllFormatsVisualTests(unittest.TestCase):
    def test_every_page_and_preview_of_every_registered_format_has_zero_flags(self):
        import format_registry
        with tempfile.TemporaryDirectory() as temp:
            rows=qa.audit(Path(temp)/'audit')
            self.assertEqual({r['format'] for r in rows},set(format_registry.list_registered_formats()))
            self.assertEqual(len(rows),23)   # 19 + CC-BIG-1 money sheets (4)
            self.assertGreater(sum(r['pdf_pages'] for r in rows),600)
            self.assertTrue(all(r['previews']>=3 for r in rows))
            self.assertEqual({r['format']:r['flags'] for r in rows if r['flags']},{})
