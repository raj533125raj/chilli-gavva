"""Three real content pages plus a full-version page, using the robot's PDF writers."""
import re
import zipfile
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
import pdf_design


def pdf_pages(path):
    """Read this robot's uncompressed Base-14 PDF pages in page-tree order.

    Refuse unsupported resources rather than silently losing images, fonts or encrypted content.
    Links to omitted pages are intentionally not copied into a preview.
    """
    data=Path(path).read_bytes()
    if b'/Encrypt' in data or b'/XObject' in data or b'/Filter' in data:
        raise ValueError('Sample PDF source uses unsupported compressed/image resources')
    fonts=set(re.findall(rb'/BaseFont\s*/([^\s/>]+)',data))
    if not fonts or not fonts <= {b'Helvetica',b'Helvetica-Bold'}:
        raise ValueError('Sample source font resources cannot be preserved')
    start=re.search(rb'startxref\s+(\d+)\s+%%EOF',data)
    if not start:
        raise ValueError('PDF cross-reference missing')
    xref=int(start[1]);lines=data[xref:].splitlines()
    if not lines or lines[0]!=b'xref':
        raise ValueError('Unsupported PDF cross-reference')
    offsets={};i=1
    while i<len(lines) and lines[i]!=b'trailer':
        first,count=map(int,lines[i].split());i+=1
        for number in range(first,first+count):
            entry=lines[i].split();i+=1
            if entry[2]==b'n':offsets[number]=int(entry[0])
    ordered=sorted(offsets.items(),key=lambda item:item[1]);objects={}
    for j,(number,offset) in enumerate(ordered):
        end=ordered[j+1][1] if j+1<len(ordered) else xref
        chunk=data[offset:end]
        body=chunk.split(b'obj',1)[1].rsplit(b'endobj',1)[0].strip()
        objects[number]=body
    catalog=next(body for body in objects.values() if re.search(rb'/Type\s*/Catalog\b',body))
    pages_id=int(re.search(rb'/Pages\s+(\d+) 0 R',catalog)[1])
    pages=[]
    def visit(number):
        body=objects[number]
        if re.search(rb'/Type\s*/Page\b',body):
            box=re.search(rb'/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)\s*\]',body)
            content=re.search(rb'/Contents\s+(\d+) 0 R',body)
            if not box or not content:
                raise ValueError('PDF page size/content missing')
            stream=objects[int(content[1])].split(b'stream\n',1)[1].rsplit(b'\nendstream',1)[0]
            pages.append(((float(box[1]),float(box[2])),stream.decode('latin-1').splitlines()))
        else:
            kids=re.search(rb'/Kids\s*\[(.*?)\]',body,re.S)
            if not kids:raise ValueError('PDF page tree missing')
            for child in re.findall(rb'(\d+) 0 R',kids[1]):visit(int(child))
    visit(pages_id)
    return pages


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__();self.parts=[];self.hidden=0
    def handle_starttag(self,tag,attrs):
        if tag in ('style','script'):self.hidden+=1
    def handle_endtag(self,tag):
        if tag in ('style','script'):self.hidden=max(0,self.hidden-1)
    def handle_data(self,data):
        if not self.hidden and data.strip():self.parts.append(' '.join(data.split()))


def content_pages(source):
    """Render real workbook rows, Word paragraphs or HTML templates when there is no PDF."""
    pages=[];provenance=[]
    def add(name,rows,table=False):
        header=rows[0].split(' | ') if table and rows else []
        body=rows[1:] if header else rows
        for offset in range(0,max(1,len(body)),22):
            pdf=pdf_design.Pdf('A4');pdf.new_page()
            for line_index,line in enumerate(pdf_design.wrap_text(name,14,510,bold=True)[:2]):
                pdf.text(42,pdf.h-55-line_index*17,line,14,bold=True,color='#264b49')
            y=pdf.h-100
            page_rows=body[offset:offset+22]
            if header:
                count=len(header);width=510/count
                for row_index,row in enumerate([' | '.join(header)]+page_rows):
                    cells=row.split(' | ')
                    for col in range(count):
                        x=42+col*width
                        if row_index==0:pdf.fill(x,y-24,width,24,'#edf4f2')
                        pdf.rect(x,y-24,width,24,color='#b8cbc7')
                        value=cells[col] if col<len(cells) else ''
                        lines,size=pdf_design.fit_text(value,8,width-8,bold=row_index==0,max_lines=2,min_size=6)
                        for line_index,line in enumerate(lines[:2]):
                            pdf.text(x+4,y-10-line_index*8,line,size,bold=row_index==0)
                    y-=24
            else:
                for row in page_rows:
                    for line in pdf_design.wrap_text(str(row),10,510)[:2]:
                        pdf.text(42,y,line,10);y-=15
                    y-=5
                    if y<70:break
            pdf.line(42,62,pdf.w-42,62)
            pdf.text(42,48,'Preview of actual file content; blank entry rows stay blank.',8)
            pages.append(((pdf.w,pdf.h),pdf.pages[0]));provenance.append(name+' rows '+str(offset+1)+' onward')
    for path in sorted(Path(source).glob('*.docx')):
        with zipfile.ZipFile(path) as archive:
            root=ET.fromstring(archive.read('word/document.xml'))
        rows=[' '.join(t.text or '' for t in p.findall('.//{*}t')) for p in root.findall('.//{*}p')]
        add(path.name,[r for r in rows if r.strip()])
    for path in sorted(Path(source).glob('*.xlsx')):
        with zipfile.ZipFile(path) as archive:
            book=ET.fromstring(archive.read('xl/workbook.xml'))
            shared=[]
            if 'xl/sharedStrings.xml' in archive.namelist():
                shared=[''.join(t.text or '' for t in item.findall('.//{*}t')) for item in ET.fromstring(archive.read('xl/sharedStrings.xml'))]
            for index,tab in enumerate(book.findall('{*}sheets/{*}sheet'),1):
                root=ET.fromstring(archive.read('xl/worksheets/sheet'+str(index)+'.xml'))
                rows=[];formulas=[]
                for row in root.findall('.//{*}row'):
                    cells=[]
                    for cell in row.findall('{*}c'):
                        value=''.join(t.text or '' for t in cell.findall('.//{*}t'))
                        if cell.find('{*}f') is not None:
                            value='Calculated field'
                            formulas.append(cell.get('r','')+': '+(cell.findtext('{*}f','')).replace('$',''))
                        elif not value:
                            value=cell.findtext('{*}v','')
                            if cell.get('t')=='s' and value:value=shared[int(value)]
                        if cell.get('s') in ('2','7','11','16') and value:value='[amount]'
                        cells.append(value)
                    rows.append(' | '.join(cells))
                add(path.name+' - '+tab.get('name','Sheet'),rows,table=any(' | ' in row for row in rows))
                if formulas:
                    add(path.name+' - '+tab.get('name','Sheet')+' - Formulas',formulas)
    for path in sorted(Path(source).glob('*.html')):
        parser=_HTMLText();parser.feed(path.read_text(encoding='utf-8'))
        add(path.name,parser.parts)
    if len(pages)<3:
        raise ValueError('Three real content previews are not available')
    return pages[:3],provenance[:3]


def make(source,destination,title,link=''):
    source=Path(source)
    pdfs=sorted(source.glob('*.pdf'),key=lambda p:(not p.name.endswith('-A4.pdf'),p.name))
    if pdfs:
        pages=pdf_pages(pdfs[0])
        if len(pages)<3:raise ValueError('Source PDF has fewer than three pages')
        pages=pages[:3];provenance=[pdfs[0].name+':'+str(i) for i in range(1,4)]
    else:
        pages,provenance=content_pages(source)
    if len({size for size,ops in pages})!=1:raise ValueError('Sample preview sizes differ')
    pdf=pdf_design.Pdf('A4');pdf.w,pdf.h=pages[0][0];pdf.pages=[list(ops) for size,ops in pages]
    pdf.new_page()
    pdf.fill(0,pdf.h-105,pdf.w,105,'#264b49')
    pdf.text(42,pdf.h-65,'FREE SAMPLE - FULL VERSION',22,bold=True,color='#ffffff')
    y=pdf.h-160
    for line in pdf_design.wrap_text(title,22,pdf.w-84,bold=True):
        pdf.text(42,y,line,22,bold=True,color='#264b49');y-=31
    y-=35
    for text in ['You have tried three real content pages.', 'The complete pack is available as the full version.',
                 'Full version: '+title, 'Open the full product link below.' if link else 'Find the full pack by its name in the owner Store.',link,
                 'Personal use only. Do not resell or share these files.']:
        for line in pdf_design.wrap_text(text,12,pdf.w-84):
            pdf.text(42,y,line,12);y-=20
        y-=15
    pdf.save(str(destination),title='Free sample - '+title)
    return provenance
