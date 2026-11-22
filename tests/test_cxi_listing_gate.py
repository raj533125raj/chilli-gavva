"""CXI-2/3: actual temporary listing packs, no real products needed."""
import base64
import tempfile
import unittest
from pathlib import Path

import listing_fields
import listing_lint
import sell_ready
import seo_score

TITLE='Weekly Planner Printable Workbook for Calm Daily Focus'
DESC=('Use this weekly planner to organize priorities and give every task a clear place. '
      'The uncluttered pages leave room for notes, appointments, and small habits. '
      'Choose one goal each morning and review the week when you finish. '
      'Keep the original workbook and print another copy whenever you want a fresh start. '
      'The guide explains each section so you can begin without special software.')
GOOD=(f'Title: {TITLE}\nMain keyword: weekly planner\nDescription: {DESC}\n'
      'What you get:\n- 24 PDF planner pages\n- A4 guide\n- Blank notes sheet\n'
      'How to use: Download, open the PDF, and print the pages.\nLicense: Personal use only.\n'
      'Tags: weekly planner, printable workbook, daily focus, organization\n')
PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2n1cAAAAASUVORK5CYII=')


class ListingGateTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.pack=Path(tmp.name)
        self.write(GOOD)
        for name in ('cover.png','preview-1.png','preview-2.png','preview-3.png'):
            (self.pack/name).write_bytes(PNG)

    def write(self,text): (self.pack/'listing.txt').write_text(text)

    def failures(self,text=None):
        if text is not None:self.write(text)
        return {name:fix for name,level,fix in listing_lint.quality_report(self.pack)['checks'] if level=='FAIL'}

    def test_good_pack_passes_and_matches_sell_ready_score(self):
        report=listing_lint.quality_report(self.pack)
        self.assertTrue(report['ok'],report['fixes'])
        self.assertEqual(report['score'],100)
        self.assertEqual(sell_ready.inspect_listing_lint(self.pack)['score'],report['score'])
        self.assertEqual(seo_score.score_pack_folder(self.pack)['score'],report['score'])
        self.assertIn(report['next'],listing_fields.owner_quality_summary(self.pack))

    def test_weak_pack_has_real_fixes_and_at_most_three_next_actions(self):
        self.write('Title: Pack\nDescription: A file.\nTags: one, ONE\n')
        report=listing_lint.quality_report(self.pack)
        self.assertEqual(report['score'],0)
        self.assertFalse(report['ok'])
        self.assertEqual(len(report['top_fixes']),3)
        self.assertEqual(report['next'],'Next: '+'; '.join(report['top_fixes']))
        self.assertTrue(all(fix.endswith('.') for fix in report['fixes']))
        ready=sell_ready.inspect_listing_lint(self.pack)
        self.assertEqual(ready['score'],report['score'])
        self.assertTrue(any(report['next'] in line for line in ready['issues']))
        scored=seo_score.score_pack_folder(self.pack)
        self.assertEqual(scored['score'],report['score'])
        self.assertEqual(scored['next'],report['next'])

    def test_title_length_and_keyword_are_independent(self):
        for title in ('Short title','x'*81):
            with self.subTest(title=title):self.assertIn('title',self.failures(GOOD.replace(TITLE,title)))
        late='A Calm Daily Focus Workbook With Weekly Planner Pages'
        self.assertIn('keyword',self.failures(GOOD.replace(TITLE,late)))

    def test_each_description_component_is_required(self):
        cases={
            'what you get':GOOD.replace('What you get:', 'More details:'),
            'format':GOOD.replace('PDF','digital').replace('A4','printed'),
            'page count/size':GOOD.replace('24 PDF planner pages','PDF planner workbook').replace('A4','Simple'),
            'how to use':GOOD.replace('How to use: Download, open the PDF, and print the pages.','').replace(
                'Keep the original workbook and print another copy whenever you want a fresh start.',
                'Keep the original workbook for another fresh start.'),
            'license':GOOD.replace('License: Personal use only.',''),
        }
        for check,text in cases.items():
            with self.subTest(check=check):self.assertIn(check,self.failures(text))

    def test_tags_range_duplicates_and_brand_names(self):
        start=GOOD.split('Tags:')[0]
        for tags in ('one, two',', '.join(f'topic{i}' for i in range(11))):
            self.assertIn('tags',self.failures(start+'Tags: '+tags))
        self.assertIn('tag duplicates',self.failures(start+'Tags: planner, PLANNER, focus'))
        self.assertIn('brands',self.failures(start+'Tags: weekly planner, Canva, focus'))

    def test_all_common_currency_forms_are_refused(self):
        for amount in (chr(36)+str(12),str(12)+' EUR','GBP '+str(12),str(12)+' '+chr(165),
                       str(12)+'EUR','CAD'+str(12),'AED .'+str(99),
                       str(12)+' dollars', 'rupees '+str(12), str(12)+' cents'):
            with self.subTest(form=amount):self.assertIn('money',self.failures(GOOD+'\n'+amount))

    def test_cover_and_three_actual_previews_are_required(self):
        (self.pack/'cover.png').unlink()
        (self.pack/'preview-3.png').unlink()
        failures=self.failures()
        self.assertIn('cover',failures);self.assertIn('previews',failures)

    def test_external_symlink_is_not_counted_as_an_upload_image(self):
        (self.pack/'preview-3.png').unlink()
        with tempfile.TemporaryDirectory() as outside:
            path=Path(outside)/'private.png';path.write_bytes(PNG)
            (self.pack/'preview-3.png').symlink_to(path)
            self.assertIn('previews',self.failures())

    def test_sections_do_not_bleed_into_the_next_field(self):
        fields=listing_fields.parse_fields(GOOD)
        self.assertEqual(fields['title'],TITLE)
        self.assertEqual(fields['description'],DESC)
        self.assertNotIn('Tags:',fields['what_you_get'])
        self.assertEqual(listing_fields._section(GOOD,'Title'),TITLE)

    def test_markdown_and_colonless_headers_work(self):
        text=GOOD.replace('Title: '+TITLE,'# '+TITLE).replace('Description: '+DESC,'## Description\n'+DESC)
        text=text.replace('What you get:', '## What you get').replace('Tags:', 'Tags\n')
        self.assertEqual(self.failures(text),{})

    def test_complete_markdown_pack_has_the_same_canonical_score(self):
        text=GOOD.replace('Title: '+TITLE,'# '+TITLE).replace('Description: '+DESC,'## Description\n'+DESC)
        (self.pack/'listing.txt').unlink()
        (self.pack/'listing.md').write_text(text)
        self.assertEqual(seo_score.score_pack_folder(self.pack)['score'],100)
        self.assertEqual(sell_ready.inspect_listing_lint(self.pack)['score'],100)

    def test_natural_page_counts_and_plain_file_lists_are_understood(self):
        text=GOOD.replace('- 24 PDF planner pages\n- A4 guide\n- Blank notes sheet',
                          'planner.pdf - 24 PDF planner pages\nguide.pdf - instructions')
        self.assertEqual(self.failures(text),{})
        text=GOOD.replace('- 24 PDF planner pages\n- A4 guide','- PDF workbook\n- Guide')+'Page count: 24\n'
        self.assertNotIn('page count/size',self.failures(text))

    def test_natural_use_instructions_do_not_require_a_section_heading(self):
        text=GOOD.replace('How to use: Download, open the PDF, and print the pages.','')
        self.assertNotIn('how to use',self.failures(text))
        for action in ('Print the pages on A4 paper.','Open the PDF file to begin.','Import the CSV file into your workbook.'):
            with self.subTest(action=action):
                clean_desc=DESC.replace('print another copy','keep another copy')
                sample=text.replace(DESC,clean_desc+' '+action)
                self.assertNotIn('how to use',self.failures(sample))

    def test_supported_text_and_calendar_formats_are_recognized(self):
        for format_name in ('HTML','MD','TXT','ICS','EPUB'):
            with self.subTest(format_name=format_name):
                self.assertNotIn('format',self.failures(GOOD.replace('PDF',format_name)))
                sample=GOOD.replace('- 24 PDF planner pages\n- A4 guide\n- Blank notes sheet',
                                    'planner.'+format_name.lower()+' - 24 planner pages\nguide.txt - A4 guide')
                self.assertNotIn('what you get',self.failures(sample))

    def test_alternative_buyer_detail_labels_pass_and_remain_in_store_copy(self):
        text=GOOD.replace('How to use:', 'How it works:').replace('24 PDF planner pages','PDF planner workbook')
        text=text.replace('A4 guide','Simple guide')+'\nFile size: 2 MB\nFile formats: PDF\n'
        self.assertEqual(self.failures(text),{})
        output=listing_fields.build_blocks(TITLE,DESC,'weekly planner, printable, focus','gumroad',whole_text=text)
        self.assertIn('2 MB',output)
        self.assertIn('Download, open the PDF, and print the pages.',output)

    def test_copyable_store_description_keeps_the_checked_buyer_information(self):
        for store in ('gumroad','topmate','instamojo'):
            with self.subTest(store=store):
                text=listing_fields.build_blocks(TITLE,DESC,'weekly planner, printable, focus',store,whole_text=GOOD)
                for expected in ('What you get:', '24 PDF planner pages','How to use:', 'License:', 'Personal use only.'):
                    self.assertIn(expected,text)
                self.assertEqual(text.count('24 PDF planner pages'),1)


if __name__=='__main__':unittest.main()
