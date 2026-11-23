"""Private free-sample upload kits, generated only from selected real products."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
import listing_lint
import selltest
import sample_pdf

FILES=('product.zip','cover.png','thumbnail.png','preview-1.png','preview-2.png','preview-3.png','listing.txt')


def _preview(pdf,page,target,title):
    from PIL import Image, ImageDraw
    import mockups
    raster=Path(target).with_name('render-'+str(page)+'.png')
    renderer=shutil.which('pdftoppm')
    if renderer:
        result=subprocess.run([renderer,'-f',str(page),'-l',str(page),'-scale-to','620','-singlefile','-png',str(pdf),str(raster.with_suffix(''))],
                              stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30)
        if result.returncode:raise ValueError('Sample page could not be rendered')
        with Image.open(raster) as im:picture=im.convert('RGB')
        raster.unlink()
    else:
        # Draw actual PDF page text, rules and boxes when Poppler is absent (Pillow-only CI).
        size,ops=sample_pdf.pdf_pages(pdf)[page-1];w,h=size;scale=620/h
        picture=Image.new('RGB',(int(w*scale),620),'white');draw=ImageDraw.Draw(picture)
        for op in ops:
            text=re.search(r'BT /(F[12]) ([\d.]+) Tf ([\d.-]+) ([\d.-]+) Td \((.*)\) Tj ET',op)
            if text:
                raw=text[5].replace('\\(','(').replace('\\)',')').replace('\\\\','\\')
                raw=raw.encode('latin-1').decode('cp1252','replace')
                font=mockups.font('bold' if text[1]=='F2' else 'regular',max(6,int(float(text[2])*scale)))
                draw.text((float(text[3])*scale,(h-float(text[4])-float(text[2]))*scale),raw,fill='#243e3d',font=font)
            line=re.search(r'([\d.-]+) ([\d.-]+) m ([\d.-]+) ([\d.-]+) l S',op)
            if line:
                x,y,xx,yy=map(float,line.groups());draw.line((x*scale,(h-y)*scale,xx*scale,(h-yy)*scale),fill='#bccdcb')
            box=re.search(r'([\d.-]+) ([\d.-]+) ([\d.-]+) ([\d.-]+) re [SfB]',op)
            if box:
                x,y,ww,hh=map(float,box.groups());draw.rectangle((x*scale,(h-y-hh)*scale,(x+ww)*scale,(h-y)*scale),outline='#bccdcb')
    canvas=Image.new('RGB',(1280,720),'#edf4f2');draw=ImageDraw.Draw(canvas)
    draw.text((40,30),'FREE SAMPLE - PAGE '+str(page),fill='#264b49',font=mockups.font('bold',27))
    picture.thumbnail((1180,610));canvas.paste(picture,((1280-picture.width)//2,90))
    canvas.save(target,'PNG')


def validate(folder):
    from PIL import Image
    folder=Path(folder)
    if folder.is_symlink() or any((folder/n).is_symlink() for n in FILES):raise ValueError('Sample symlink refused')
    quality=listing_lint.quality_report(folder)
    if not quality['ok']:raise ValueError('Sample listing gate: '+'; '.join(quality['top_fixes']))
    with zipfile.ZipFile(folder/'product.zip') as archive:
        if set(archive.namelist())!={'sample.pdf','README.txt','LICENSE.txt'} or archive.testzip():raise ValueError('Sample ZIP refused')
    for name in FILES:
        if not (folder/name).is_file() or (folder/name).stat().st_size>45*1024*1024:raise ValueError('Sample file missing or too large')
    for name in FILES[1:-1]:
        with Image.open(folder/name) as image:
            image.load()
            if image.size!=((600,600) if name=='thumbnail.png' else (1280,720)):raise ValueError('Sample image size refused')
    return folder


def build(aid,products_dir=None,db_path=None):
    import sell_ready
    import tg_listed
    from PIL import Image, ImageOps, ImageDraw
    import mockups
    products=selltest.products_root(products_dir)
    run=selltest.active(db_path)
    pack=next((p for p in (run or {}).get('packs',[]) if p['id']==aid),None)
    if pack is None:raise ValueError('Pack active Sell test lo ledu')
    name=pack['folder']
    if Path(name).name!=name:raise ValueError('Invalid Pack folder')
    source=products/name;upload=products/'upload'/name
    if source.is_symlink() or upload.is_symlink() or not source.is_dir():raise ValueError('Real source Pack missing')
    evaluation=sell_ready.evaluate_pack(upload,products/'marketing')
    if evaluation['status'] not in ('SELLABLE','ALMOST'):raise ValueError('Full Pack is not READY')
    listed=tg_listed.get_listed(aid,db_path);link=listed['url'] if listed else ''
    fingerprint=hashlib.sha256((pack['title']+link).encode())
    for path in sorted(source.iterdir()):
        if path.is_file() and not path.is_symlink() and path.suffix.lower() in ('.pdf','.xlsx','.docx','.html'):
            fingerprint.update(path.name.encode());fingerprint.update(path.read_bytes())
    digest=fingerprint.hexdigest();destination=products/'samples'/name
    if destination.is_symlink():raise ValueError('Sample destination symlink refused')
    meta=destination/'sample.json'
    try:
        cached=json.loads(meta.read_text()) if meta.is_file() else {}
    except (OSError,ValueError):
        cached={}
    if cached.get('source_digest')==digest:
        return validate(destination)
    destination.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.free-sample-',dir=destination.parent) as temp:
        stage=Path(temp)/'kit';stage.mkdir()
        provenance=sample_pdf.make(source,stage/'sample.pdf',pack['title'],link)
        (stage/'README.txt').write_text('FREE SAMPLE\n'+pack['title']+'\nThree real content previews and a full-version page.\nOriginal PDF page numbering is preserved where present.\nFull version: '+pack['title']+'\n'+link+'\nPersonal use only.\n')
        (stage/'LICENSE.txt').write_text('Personal use only. Do not resell or redistribute.\n')
        with zipfile.ZipFile(stage/'product.zip','w',zipfile.ZIP_DEFLATED) as archive:
            for filename in ('sample.pdf','README.txt','LICENSE.txt'):archive.write(stage/filename,filename)
        for i in range(1,4):_preview(stage/'sample.pdf',i,stage/f'preview-{i}.png',pack['title'])
        with Image.open(stage/'preview-1.png') as image:
            cover=image.convert('RGB');draw=ImageDraw.Draw(cover)
            draw.rectangle((0,0,1280,90),fill='#264b49')
            draw.text((40,22),'FREE SAMPLE - '+pack['title'][:52],fill='white',font=mockups.font('bold',26))
            cover.save(stage/'cover.png','PNG');ImageOps.fit(cover,(600,600)).save(stage/'thumbnail.png','PNG')
        title=('Free sample: '+pack['title'][:43]+' - Preview PDF')[:80]
        description=('Try three real content pages from '+pack['title']+'. This free sample contains a four-page PDF: '
                     'three product content previews followed by a page naming the full version. Download and unzip the files, '
                     'open sample.pdf in a PDF reader, and read the included guide. Workbook and HTML products are shown as rendered '
                     'previews of their actual content. The full product is separate. Personal use only; do not resell or share. '
                     'PDF page count: 4. Download size '+str(max(1,(stage/'product.zip').stat().st_size//1024))+' KB.')
        listing='Title: '+title+'\n\nDescription:\n'+description+'\n\nWhat you get:\n- sample.pdf\n- README.txt\n- LICENSE.txt\n\nHow to use:\nDownload, unzip and open the PDF.\n\nLicense:\nPersonal use only.\n\nTags: free sample, pdf preview, sample pages, digital download\n'
        (stage/'listing.txt').write_text(listing)
        (stage/'sample.json').write_text(json.dumps({'free_sample':True,'full_pack_id':aid,'source_digest':digest,'source_pages':provenance},indent=2))
        validate(stage)
        backup=Path(temp)/'previous'
        if destination.exists():os.replace(destination,backup)
        try:os.replace(stage,destination)
        except OSError:
            if backup.exists():os.replace(backup,destination)
            raise
    return destination


def ensure_all(products_dir=None,db_path=None):
    run=selltest.active(db_path);result={}
    for p in (run or {}).get('packs',[]):
        try:build(p['id'],products_dir,db_path);result[p['id']]=True
        except (ValueError,OSError,RuntimeError,ImportError,zipfile.BadZipFile):result[p['id']]=False
    return result


def set_link(aid,url,db_path=None):
    import tg_listed
    clean,reason=tg_listed.validate_url(url)
    if not clean:raise ValueError(reason)
    run=selltest.active(db_path)
    if aid not in {p['id'] for p in (run or {}).get('packs',[])}:raise ValueError('Pack Sell test lo ledu')
    with selltest._connect(db_path) as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS sell_test_sample_links (approval_id INTEGER PRIMARY KEY, url TEXT NOT NULL)')
        conn.execute('INSERT INTO sell_test_sample_links VALUES(?,?) ON CONFLICT(approval_id) DO UPDATE SET url=excluded.url',(aid,clean))
    return clean


def links(db_path=None):
    import sqlite3
    from contextlib import closing
    path=Path(selltest._db(db_path))
    if not path.is_file():return {}
    with closing(sqlite3.connect(path)) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='sell_test_sample_links'").fetchone():return {}
        return dict(conn.execute('SELECT approval_id,url FROM sell_test_sample_links'))
