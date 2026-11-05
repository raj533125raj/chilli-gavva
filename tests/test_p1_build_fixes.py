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
