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


if __name__ == '__main__':
    unittest.main()
