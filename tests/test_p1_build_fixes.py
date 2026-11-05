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


if __name__ == '__main__':
    unittest.main()
