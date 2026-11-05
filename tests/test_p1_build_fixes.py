"""P1 product promises: exercise actual rendered PDF and workbook output offline."""
import copy
import os
from pathlib import Path
import re
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET

import pdf_design as pd
import product_builder as pb
import truth_check as tc


class TableHeaderTests(unittest.TestCase):
    def test_every_column_header_and_row_cell_is_drawn(self):
        for labels in (["Bill", "Due", "Amount", "Paid"], ["Date", "Item", "Price", "Quantity", "Amount"]):
            for size in ("A4", "Letter"):
                with self.subTest(columns=len(labels), size=size), tempfile.TemporaryDirectory() as d:
                    pdf = pd.Pdf(size, style="A")
                    pd.render_page(pdf, {"title": "Sales table", "sections": [
                        {"type": "table", "heading": "Sales", "columns": labels, "count": 3}]})
                    path = os.path.join(d, 'table.pdf');pdf.save(path)
                    items = tc._pdf_pages(path)[0]["items"]
                    headers = [item for item in items if item[4] in labels]
                    self.assertEqual([item[4] for item in headers], labels)
                    positions = [item[2] for item in headers]
                    gaps = [b-a for a,b in zip(positions, positions[1:])]
                    self.assertLess(max(gaps)-min(gaps), .2)
                    self.assertTrue(all(pd.MARGIN <= item[2] < pdf.w-pd.MARGIN for item in headers))
                    stream = '\n'.join(pdf.pages[0])
                    self.assertGreaterEqual(stream.count(' re S'), 3)
                    verticals = re.findall(r'([\d.]+) ([\d.]+) m ([\d.]+) ([\d.]+) l S', stream)
                    self.assertGreaterEqual(sum(x1==x2 and y1!=y2 for x1,y1,x2,y2 in verticals), 3*(len(labels)-1))


class HabitGridTests(unittest.TestCase):
    def test_seven_and_31_day_grids_have_every_label_and_empty_tick_box(self):
        for days in (7, 31):
            for size in ("A4", "Letter"):
                for style in ("A", "B"):
                    with self.subTest(days=days, size=size, style=style), tempfile.TemporaryDirectory() as d:
                        pdf = pd.Pdf(size, style=style)
                        section = {"type": "grid", "heading": "Habits", "days": days, "count": 6,
                                   "items": [f"Habit {n}" for n in range(1, 7)]}
                        original = copy.deepcopy(section)
                        pd.render_page(pdf, {"title": "Habit grid", "sections": [section]})
                        path = os.path.join(d, 'habit.pdf');pdf.save(path)
                        pages = tc._pdf_pages(path)
                        self.assertEqual(len(pages), 1 if days == 7 else 2)
                        labels = [item for page in pages for item in page['items'] if item[4].isdigit()]
                        self.assertCountEqual([item[4] for item in labels], [str(n) for n in range(1, days + 1)])
                        self.assertTrue(all(pd.MARGIN <= item[2] and item[2]+pd.text_width(item[4],item[1]) <= pdf.w-pd.MARGIN+.1 for item in labels))
                        stream = '\n'.join('\n'.join(p) for p in pdf.pages)
                        self.assertEqual(len(re.findall(r' 9 9 re S', stream)), 6*days)
                        self.assertEqual(section, original)

    def test_existing_habit_table_specs_become_tick_grids(self):
        pdf = pd.Pdf('A4', style='A')
        pd.render_page(pdf, {'title': 'Habits', 'sections': [{'type':'table','columns':['Habit','Mon','Tue','Wed','Thu','Fri','Sat','Sun'],'count':6}]})
        self.assertEqual('\n'.join(pdf.pages[0]).count(' 9 9 re S'), 42)


class SanitizerCountTests(unittest.TestCase):
    def test_page_counts_only_follow_real_pdf_output_and_keep_grammar(self):
        text = 'Seven pages in A4 and US Letter. Seven A4 and US Letter pages.'
        sheet = {'kind':'sheets','sheets':[{'name':'Ledger'}]}
        self.assertEqual(pb._sanitize_text(text, sheet), text)
        self.assertEqual(pb._sanitize_text(text, sheet, facts={'kind':'sheets','pages':0,'sheets':['Instructions','Ledger'],'md':{},'csv':{},'titles':[],'headings':[]}), text)
        planner = {'kind':'planner','pages':[{'title':'Notes'}]*5}
        self.assertEqual(pb._sanitize_text('Seven pages', planner), 'Seven pages')
        self.assertEqual(pb._sanitize_text('Six A4 and US Letter pages', planner), 'Seven A4 and US Letter pages')
        self.assertEqual(pb._sanitize_text('8 pages', planner), '7 pages')
        self.assertEqual(pb._sanitize_text('One page', planner), 'Seven pages')
        self.assertEqual(pb._sanitize_text('Two sheets and Seven pages', planner), 'Two sheets and Seven pages')
        self.assertEqual(pb._sanitize_text('12 monthly calendar pages and Two extra pages', dict(planner,kind='dated_planner')), '12 monthly calendar pages and Two extra pages')


class SheetNormalizationTests(unittest.TestCase):
    def test_month_grid_keeps_32_columns_and_cap_is_40(self):
        raw = {'name':'Month Grid','columns':['Habit']+[f'Day {n}' for n in range(1,32)],'rows':[['Read']+['x']*31]}
        result = pb._norm_sheet(raw)
        self.assertEqual(len(result['columns']),32)
        self.assertEqual(result['columns'][-1]['name'],'Day 31')
        self.assertEqual(len(result['rows'][0]),32)
        raw['columns'] += [f'Extra {n}' for n in range(10)]
        self.assertEqual(len(pb._norm_sheet(raw)['columns']),40)


class PageNormalizationTests(unittest.TestCase):
    def test_supported_section_types_and_their_metadata_survive_and_render(self):
        for kind in ['table','checkboxes','grid','blocks','numbered','index','bullets','nnl']:
            section = {'heading':kind,'type':kind,'count':2,'columns':['Name','One','Two','Three','Four'],
                       'days':31,'note':'Colour one block','items':[['Notes','7']] if kind=='index' else ['Read','Walk']}
            spec = {'kind':'planner','listing':{'title':'Tracker'}}
            reason = pb._norm_pages({'pages':[{'title':'Tracker','sections':[section]}]},spec)
            self.assertIsNone(reason)
            kept = spec['pages'][0]['sections'][0]
            self.assertEqual(kept['type'],kind)
            if kind in ('table','grid'): self.assertEqual(kept['columns'],section['columns'])
            if kind=='grid': self.assertEqual(kept['days'],31)
            if kind=='index': self.assertEqual(kept['items'],[['Notes','7']])
            self.assertEqual(kept['note'],'Colour one block')
            pdf = pd.Pdf('Letter',style='A');pd.render_page(pdf,spec['pages'][0])
            self.assertGreater(len(pdf.pages[0]),0)


NS = {'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}

def workbook_parts(path):
    with zipfile.ZipFile(path) as archive:
        return {name:ET.fromstring(archive.read(name)) for name in archive.namelist() if name.endswith('.xml')}


class WorkbookInstructionTests(unittest.TestCase):
    def test_every_formula_cell_is_grey_and_money_formats_survive(self):
        sheet = pb._norm_sheet({'name':'Ledger','columns':[{'name':'Income','type':'money'},{'name':'Spent','type':'money'}],
                               'rows':[[10,3],[7,2]],'balance':{'in':'Income','out':'Spent'}})
        spec = {'listing':{'title':'Ledger'},'instructions':[]}
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d,'ledger.xlsx');pb.build_workbook(path,spec,[sheet])
            parts = workbook_parts(path)
        styles = parts['xl/styles.xml'].find('m:cellXfs',NS)
        fills = parts['xl/styles.xml'].find('m:fills',NS)
        formula_count = 0
        for name,root in parts.items():
            if not name.startswith('xl/worksheets/'): continue
            for cell in root.findall('.//m:c',NS):
                if cell.find('m:f',NS) is not None:
                    formula_count += 1
                    style = styles[int(cell.get('s','0'))]
                    self.assertEqual(fills[int(style.get('fillId'))].find('m:patternFill/m:fgColor',NS).get('rgb'),'FFD9D9D9')
                    if cell.get('r') in ('C2','C3'): self.assertEqual(style.get('numFmtId'),'164')
        self.assertGreater(formula_count,0)
        instructions = ' '.join(node.text or '' for node in parts['xl/worksheets/sheet1.xml'].findall('.//m:t',NS))
        self.assertIn('Grey cells contain formulas',instructions)
        self.assertNotIn('Google Sheets',instructions)
        self.assertNotIn('Apple Numbers',instructions)


class WorkbookSummaryTests(unittest.TestCase):
    def test_resume_tracker_preserves_explicit_dashboard_preference(self):
        data={'resume':{'sections':[{'heading':'Summary','bullets':['Your summary']}]},
              'cover_letter':{'paragraphs':['Dear Hiring Manager']}}
        for flag in (False,True):
            spec={}
            self.assertIsNone(pb._norm_resume(dict(data,dashboard=flag),spec))
            self.assertEqual(spec['dashboard'],flag)


    def test_dashboard_is_opt_in_and_in_out_totals_never_mix(self):
        sheet = pb._norm_sheet({'name':'Month','columns':[{'name':'Date','type':'date'},{'name':'Category'},
                   {'name':'Amount','type':'money'},{'name':'Type','options':['In','Out']}],
                   'rows':[['2027-01-01','Pay',20000,'In'],['2027-01-02','Rent',8000,'Out']]})
        for dashboard in (False,True):
            spec = {'listing':{'title':'Month'},'instructions':[],'dashboard':dashboard}
            with tempfile.TemporaryDirectory() as d:
                path = os.path.join(d,'month.xlsx');pb.build_workbook(path,spec,[sheet]);parts=workbook_parts(path)
            names = [node.get('name') for node in parts['xl/workbook.xml'].findall('.//m:sheet',NS)]
            self.assertEqual(names,['Instructions']+(['Dashboard'] if dashboard else [])+['Month'])
            root = parts[f'xl/worksheets/sheet{3 if dashboard else 2}.xml']
            formulas = [node.text for node in root.findall('.//m:f',NS)]
            self.assertIn('SUMIF($D$2:$D$3,"In",C2:C3)',formulas)
            self.assertIn('SUMIF($D$2:$D$3,"Out",C2:C3)',formulas)
            self.assertNotIn('SUM(C2:C3)',formulas)
            text = [node.text for node in root.findall('.//m:t',NS)]
            self.assertIn('TOTAL IN',text);self.assertIn('TOTAL OUT',text)
            normalized = {}
            self.assertIsNone(pb._norm_sheets({'sheets':[sheet],'dashboard':dashboard},normalized))
            self.assertEqual(normalized['dashboard'],dashboard)


if __name__ == '__main__':
    unittest.main()


class DatedListingPagesTests(unittest.TestCase):
    def spec(self, **overrides):
        return {'name':'Dated focus', 'kind':'planner', 'year':2028, 'short_name':'dated-focus',
                'listing':{'title':'Dated focus', 'description':'Dated daily pages and dated weekly pages. A year overview. A4 and US Letter.', 'buyer':'adults'},
                'instructions':['Print the pages you need.'], 'pages':[], **overrides}

    def test_listing_promises_normalize_into_real_dated_options(self):
        spec=self.spec()
        self.assertIsNone(pb._norm_pages({'year':2028,'dated_pages':{'start':'2028-02-28','end':'2028-03-01'}},spec))
        self.assertTrue(all(spec['dated_pages'][key] for key in ('daily','weekly','year_overview')))
        self.assertFalse(spec['daily_page'])
        text=spec['listing']['description']
        self.assertEqual(pb._sanitize_text(text,spec),text)
        with tempfile.TemporaryDirectory() as d:
            for size in ('A4','Letter'):
                pd.build_planner_pdf(os.path.join(d,f'focus-{size}.pdf'),spec,size)
            Path(d,'listing.md').write_text(text)
            result=tc.check(d,with_images=False)
            self.assertEqual(result['verdict'],'ready',result)
            self.assertEqual(result['pages'],7)  # cover, instructions, overview, week, three days
            self.assertIn('dated daily pages',result['proven'])
            self.assertIn('dated weekly pages',result['proven'])

    def test_dates_weekdays_and_layout_in_both_styles_and_sizes(self):
        for style in ('A','B'):
            for size in ('A4','Letter'):
                with self.subTest(style=style,size=size), tempfile.TemporaryDirectory() as d:
                    spec=self.spec(style=style,calendar='uk-academic',dated_pages={
                        'daily':True,'weekly':True,'year_overview':True,'start':'2028-12-30','end':'2029-01-02'})
                    count=pd.build_planner_pdf(os.path.join(d,f'focus-{size}.pdf'),spec,size)
                    f=tc.facts(d)
                    self.assertEqual(count,9)
                    self.assertEqual(tc._dated_evidence(f),(
                        ['2028-12-30','2028-12-31','2029-01-01','2029-01-02'],['2028-12-25','2029-01-01']))
                    self.assertEqual(tc.file_quality(f),[])
                    for page in f['page_items']:
                        for _,font_size,x,y,text in page['items']:
                            self.assertGreaterEqual(x,pd.MARGIN-.1)
                            self.assertLessEqual(x+pd.text_width(text,font_size),pd.PAGE_SIZES[size][0]-pd.MARGIN+.2)
                            self.assertGreaterEqual(y,pd.FOOTER_Y-.1)

    def test_full_leap_year_cover_groups_dates_and_counts_actual_pages(self):
        spec=self.spec(dated_pages={'daily':True,'weekly':True,'year_overview':True})
        with tempfile.TemporaryDirectory() as d:
            count=pd.build_planner_pdf(os.path.join(d,'focus-A4.pdf'),spec,'A4')
            f=tc.facts(d)
            daily,weekly=tc._dated_evidence(f)
            self.assertEqual(len(daily),366)
            self.assertEqual(len(weekly),54)
            self.assertEqual(count,423)
            self.assertEqual(len(daily),len(set(daily)))
            cover=' '.join(t for *_,t in f['page_items'][0]['items'])
            self.assertIn('Pages 4-57',cover)
            self.assertIn('Pages 58-423',cover)
            self.assertEqual(tc.file_quality(f),[])

    def test_ranges_and_flags_are_validated_and_undated_products_stay_small(self):
        for options in ({'daily':'yes'}, {'daily':True,'start':'20280228'}, {'daily':True,'start':'2028-02-30'},
                        {'weekly':True,'start':'2028-02-01','end':'2028-01-01'},
                        {'daily':True,'start':'2027-12-31'}, {'daily':True,'end':'2029-01-01'}):
            with self.subTest(options=options):
                spec=self.spec()
                self.assertIsNotNone(pb._norm_pages({'year':2028,'dated_pages':options},spec))
        for text in ('A daily page and weekly pages.', 'An undated weekly spread. No dated daily pages.'):
            spec=self.spec(listing={'title':'Focus','description':text})
            data={'pages':[{'title':'Notes','sections':[{'type':'lines','count':3}]}]}
            self.assertIsNone(pb._norm_pages(data,spec))
            self.assertNotIn('dated_pages',spec)
            with tempfile.TemporaryDirectory() as d:
                self.assertEqual(pd.build_planner_pdf(os.path.join(d,'focus-A4.pdf'),spec,'A4'),3)

    def test_monthly_planner_keeps_one_overview_and_adds_requested_dates(self):
        spec=self.spec(kind='dated_planner')
        self.assertIsNone(pb._norm_dated({'dated_pages':{'daily':True,'weekly':True,
                           'start':'2027-01-01','end':'2027-01-02'}},spec))
        with tempfile.TemporaryDirectory() as d:
            count=pd.build_dated_planner_pdf(os.path.join(d,'focus-A4.pdf'),spec,'A4')
            f=tc.facts(d)
            self.assertEqual(count,18)
            self.assertEqual(sum('at a glance' in title for title in f['titles']),1)
            self.assertEqual(tc._dated_evidence(f),(['2027-01-01','2027-01-02'],['2026-12-27']))

    def test_one_page_kind_does_not_prove_the_other_or_fake_week_labels(self):
        for daily,weekly,missing in ((True,False,'dated weekly pages'),(False,True,'dated daily pages')):
            with tempfile.TemporaryDirectory() as d:
                spec=self.spec(dated_pages={'daily':daily,'weekly':weekly,'start':'2028-02-28','end':'2028-03-01'})
                pd.build_planner_pdf(os.path.join(d,'focus-A4.pdf'),spec,'A4')
                Path(d,'listing.md').write_text('Includes dated daily pages and dated weekly pages.')
                result=tc.check(d,with_images=False)
                self.assertTrue(any(missing in p for p in result['problems']),result)
        with tempfile.TemporaryDirectory() as d:
            spec=self.spec(pages=[{'title':'Week 1','sections':[{'type':'lines','count':12}]}])
            pd.build_planner_pdf(os.path.join(d,'focus-A4.pdf'),spec,'A4')
            self.assertFalse(tc._proven('weekly or daily dated pages',tc.facts(d))[0])

    def test_parse_and_product_build_preserve_the_promises_and_real_page_count(self):
        import json
        raw=self.spec()
        raw['listing']['description'] += ' Seven pages.'
        raw['dated_pages']={'start':'2028-02-28','end':'2028-03-01'}
        spec,reason=pb.parse_spec(json.dumps(raw),'ADHD Planners Agent')
        self.assertIsNone(reason)
        with tempfile.TemporaryDirectory() as d:
            folder=pb.build_product(spec,'dates',out_dir=d)
            result=tc.check(os.path.join(d,folder),with_images=False)
            self.assertEqual(result['verdict'],'ready',result)
            self.assertEqual(result['pages'],7)
            self.assertIn('dated daily pages',result['proven'])
            self.assertIn('dated weekly pages',result['proven'])
            self.assertIn('count: 7 pages',result['proven'])

    def test_invalid_dates_and_wrong_weekdays_do_not_prove_dated_pages(self):
        for title,weekday in (('29 February 2027','Monday'),('29 February 2028','Monday')):
            with self.subTest(title=title), tempfile.TemporaryDirectory() as d:
                spec=self.spec(pages=[{'title':title,'subtitle':weekday,
                                      'sections':[{'type':'lines','count':12}]}])
                pd.build_planner_pdf(os.path.join(d,'focus-A4.pdf'),spec,'A4')
                self.assertFalse(tc._proven('dated daily pages',tc.facts(d))[0])
        with tempfile.TemporaryDirectory() as d:
            spec=self.spec(dated_pages={'weekly':True,'start':'2028-02-28','end':'2028-03-01'})
            path=Path(d,'focus-A4.pdf')
            pd.build_planner_pdf(path,spec,'A4')
            self.assertTrue(tc._proven('dated weekly pages',tc.facts(d))[0])
            data=path.read_bytes().replace(b'TUESDAY 29 FEBRUARY 2028',b'MONDAY 29 FEBRUARY 2028 ')
            path.write_bytes(data)  # same byte length, so PDF offsets stay valid
            self.assertFalse(tc._proven('dated weekly pages',tc.facts(d))[0])
