"""Render every product page and preview, and report measurable layout defects.

Uses real Poppler glyph boxes and page rasters, with Pillow as the only Python
image dependency. Reports stay outside buyer packs and outside the repository.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
try:
    from PIL import Image, ImageChops, ImageStat
except ImportError:
    Image = None

SAFE = 5 * 72 / 25.4
BOTTOM_LIMIT = .35
NEAR_BLANK_INK = .002


def available():
    return Image is not None and bool(shutil.which('pdftoppm') and shutil.which('pdftotext'))


def image_metrics(path, height_pt=None):
    with Image.open(path) as source:
        gray = source.convert('L')
        w,h = gray.size
        # The most frequent shade is the paper, including tinted planner paper.
        histogram = gray.histogram()
        background = max(range(256), key=histogram.__getitem__)
        ink = ImageChops.difference(gray, Image.new('L',gray.size,background)).point(lambda v:255 if v>20 else 0)
        ratio = sum(ink.histogram()[1:])/(w*h)
        bottom = None
        if height_pt:
            sx = w / (height_pt*w/h)
            inset = max(1,round(SAFE*sx))
            # Footer text is navigation, not substantive content filling the page.
            region=ink.crop((inset,0,w-inset,max(1,round(h-50*h/height_pt))))
            box=region.getbbox()
            if box:bottom=(h-box[3])/h
        return {'width':w,'height':h,'ink':ratio,'empty_bottom':bottom}


def duplicate_images(left,right):
    with Image.open(left) as a,Image.open(right) as b:
        a=a.convert('RGB').resize((256,256));b=b.convert('RGB').resize((256,256))
        diff=ImageChops.difference(a,b)
        mean=sum(ImageStat.Stat(diff).mean)/3
        changed=sum(diff.convert('L').point(lambda v:255 if v>12 else 0).histogram()[1:])/(256*256)
        return mean<1.5 and changed<.03


def expected_size(path,kind):
    name=Path(path).stem.lower()
    if 'letter' in name:return (612,792)
    if 'a4' in name:return (595.28,841.89)
    if kind=='digital_planner':return (1194,834)
    return None


def check_pdf(path,out,kind=''):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    path=Path(path)
    xml=subprocess.check_output(['pdftotext','-bbox-layout',str(path),'-'])
    doc=ET.fromstring(xml)
    pages=[e for e in doc.iter() if e.tag.rsplit('}',1)[-1]=='page']
    subprocess.run(['pdftoppm','-r','60','-png',str(path),str(out/'page')],check=True,
                   stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    rasters=sorted(out.glob('page-*.png'))
    flags=[];details=[]
    if len(rasters)!=len(pages):raise ValueError('Rendered page count differs from text page count')
    expected=expected_size(path,kind)
    for number,(page,raster) in enumerate(zip(pages,rasters),1):
        w,h=float(page.get('width')),float(page.get('height'))
        issue=[]
        if expected and any(abs(a-b)>1 for a,b in zip((w,h),expected)):issue.append('wrong page size')
        elif not expected and (w,h) not in [(float(pages[0].get('width')),float(pages[0].get('height')))]:issue.append('wrong page size')
        words=[]
        for word in page.iter():
            if word.tag.rsplit('}',1)[-1]!='word':continue
            box=tuple(float(word.get(k)) for k in ('xMin','yMin','xMax','yMax'))
            words.append((box,''.join(word.itertext())))
        clipped=[text for (x1,y1,x2,y2),text in words if x1<SAFE or y1<SAFE or x2>w-SAFE or y2>h-SAFE]
        if clipped:issue.append('text cut at edge: '+repr(clipped[:3]))
        overlap=None
        for i,(a,ta) in enumerate(words):
            for b,tb in words[i+1:]:
                if min(a[2],b[2])-max(a[0],b[0])>.8 and min(a[3],b[3])-max(a[1],b[1])>.5:
                    overlap=(ta,tb);break
            if overlap:break
        if overlap:issue.append('overlapping text: '+repr(overlap))
        metrics=image_metrics(raster,h)
        if metrics['ink']<NEAR_BLANK_INK:issue.append('blank or near-blank page')
        if metrics['empty_bottom'] is None or metrics['empty_bottom']>BOTTOM_LIMIT:
            issue.append('empty bottom exceeds 35%')
        for message in issue:flags.append({'file':path.name,'page':number,'issue':message})
        details.append(dict(page=number,size=[w,h],raster=str(raster),**metrics))
    return {'pages':details,'flags':flags}


def check_pack(folder,out,kind):
    folder,out=Path(folder),Path(out)
    out.mkdir(parents=True,exist_ok=True)
    flags=[];pages=[]
    for pdf in sorted(folder.rglob('*.pdf')):
        result=check_pdf(pdf,out/pdf.stem,kind)
        pages.extend(result['pages']);flags.extend(result['flags'])
    previews=sorted(p for p in folder.rglob('*.png') if p.name.lower().startswith('preview'))
    covers=sorted(p for p in folder.rglob('*.png') if p.name.lower().startswith('cover'))
    for number,path in enumerate(previews,1):
        metrics=image_metrics(path)
        # Decode the entire preview and keep an inspection image in the report.
        with Image.open(path) as source:
            source.load();source.convert('RGB').save(out/f'preview-{number}.png')
        if metrics['ink']<NEAR_BLANK_INK:flags.append({'file':path.name,'page':0,'issue':'blank or near-blank preview'})
        for cover in covers:
            if duplicate_images(cover,path):flags.append({'file':path.name,'page':0,'issue':'cover looks the same as preview'})
    if not previews:flags.append({'file':'','page':0,'issue':'no preview images'})
    return {'format':kind,'pdf_pages':len(pages),'previews':len(previews),'flags':flags,'pages':pages,'folder':str(folder)}


def audit(out,kinds=None):
    import product_builder
    from tools import sell_proof
    out=Path(out).resolve()
    if out==ROOT or ROOT in out.parents:raise ValueError('Visual QA output must be outside the repository')
    out.mkdir(parents=True,exist_ok=True)
    rows=[]
    for index,kind in enumerate(kinds or sell_proof.FORMAT_ORDER,1):
        products=out/'products'/kind
        name=product_builder.build_product(sell_proof.spec_for(kind),index,out_dir=str(products))
        row=check_pack(products/name,out/'renders'/kind,kind)
        rows.append(row)
        print(f"{kind} | {row['pdf_pages']} pages | {row['previews']} previews | {len(row['flags'])} flags",flush=True)
    (out/'report.json').write_text(json.dumps(rows,indent=2))
    return rows


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',required=True)
    parser.add_argument('--only',default='')
    args=parser.parse_args(argv)
    if not available():raise SystemExit('Pillow and Poppler are required')
    rows=audit(args.out,args.only.split(',') if args.only else None)
    count=sum(len(r['flags']) for r in rows)
    print(f'VISUAL QA: {len(rows)} formats, {sum(r["pdf_pages"] for r in rows)} PDF pages, {count} flags')
    return 1 if count else 0


if __name__=='__main__':sys.exit(main())
