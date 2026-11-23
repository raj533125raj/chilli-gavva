"""Private, immutable landing drafts; only owner-approved snapshots enter site/."""
from contextlib import closing
import hashlib
import html
import json
from pathlib import Path
import shutil
import sqlite3
import zipfile
from urllib.parse import urlencode

import guardrails
import landing_page
import selltest

KIND = 'selltest_landing'


def tracked_link(url, source):
    import tg_listed
    clean, reason = tg_listed.validate_url(url)
    if not clean:
        raise ValueError(reason)
    return clean + '?' + urlencode({'utm_source': source, 'utm_campaign': 'selltest'})


def member(aid, db_path=None):
    pack = next((p for p in (selltest.active(db_path) or {}).get('packs', []) if p['id'] == aid), None)
    if pack is None or Path(pack['folder']).name != pack['folder']:
        raise ValueError('Pack Sell test lo ledu; /selltest list chudandi.')
    return pack


def safe_path(root, relative):
    root = Path(root).resolve()
    relative = Path(relative)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('Unsafe landing path')
    path = root
    for part in relative.parts:
        path = path / part
        if path.is_symlink():
            raise ValueError('Landing symlink refused')
    if not path.resolve().is_relative_to(root):
        raise ValueError('Landing path outside products')
    return path


def public_check(text):
    import secret_guard
    import sell_ready
    if secret_guard.scan_text(html.unescape(text)) or sell_ready.check_money_text(html.unescape(text)):
        raise ValueError('Public page contains private data or amounts')


def page_model(aid, sample=False, products_dir=None, db_path=None):
    import free_sample
    import selltest_listings
    import tg_listed
    import listing_lint
    products = selltest.products_root(products_dir)
    pack = member(aid, db_path)
    source = safe_path(products, Path('samples' if sample else 'upload') / pack['folder'])
    if not listing_lint.quality_report(source)['ok']:
        raise ValueError('Listing gate fail; /upload '+str(aid)+' chudandi.')
    for name in ('listing.txt', 'product.zip', 'cover.png', 'preview-1.png', 'preview-2.png', 'preview-3.png'):
        safe_path(source, name)
        if not (source/name).is_file():
            raise ValueError('Landing source file missing: '+name)
    if sample:
        free_sample.validate(source)
        info = landing_page.parse_listing_file(source/'listing.txt')
        title, description, tags = info['title'], info['description'], info['tags']
        url = free_sample.links(db_path).get(aid, '')
    else:
        copy = selltest_listings.make(source, pack['format'])
        title, description, tags = copy[selltest.title_variant(aid, db_path)], copy['description'], copy['tags']
        listed = tg_listed.get_listed(aid, db_path)
        url = listed['url'] if listed else ''
    with zipfile.ZipFile(source/'product.zip') as archive:
        if archive.testzip():
            raise ValueError('Buyer ZIP damaged')
        files = sorted(n for n in archive.namelist() if not n.endswith('/'))
        if not files or any(Path(n).is_absolute() or '..' in Path(n).parts for n in files):
            raise ValueError('Buyer ZIP file list unsafe')
    images = ['cover.png', 'preview-1.png', 'preview-2.png', 'preview-3.png']
    readme = next((name for name in files if Path(name).name.lower().startswith('readme.')), None)
    how = 'Download the archive and unzip it.' + (' Read '+readme+' before opening your files.' if readme else ' Open the included files in a compatible viewer.')
    faq = [('What will I download?', 'The download contains: '+', '.join(files)+'.'),
           ('How do I start?', how),
           ('Can I share these files?', 'Personal use only. Do not resell or redistribute.'),
           ('Is this the complete pack?', 'This is a free preview sample. The full pack is a separate download.' if sample
            else 'This page describes the complete pack. Review the real previews and file list before choosing.')]
    return dict(title=title, description=description, tags=tags, files=files, images=images,
                faq=faq, link=tracked_link(url, 'site') if url else '', sample=sample, pack_id=aid,
                folder=pack['folder'], url=base_url(db_path)+f"/selltest-{aid}-{'sample' if sample else 'pack'}/")


def render(model):
    page = landing_page.generate_landing_html(model['folder'], model['title'], model['description'],
        model['files'], model['tags'], model['faq'], model['images'])
    if model['link']:
        page = page.replace('href="[STORE LINK]"', 'href="'+html.escape(model['link'], quote=True)+'"')
        page = page.replace('[STORE LINK]', 'Get the free sample' if model['sample'] else 'View the full pack')
    else:
        page = page.replace('<a href="[STORE LINK]" class="cta-btn">[STORE LINK]</a>',
                            '<p>Store link pending owner review.</p>')
    import re
    schema = {'@context':'https://schema.org', '@type':'Product', 'name':model['title'],
              'description':landing_page.make_meta_description(model['description'], model['title']),
              'url':model['url'], 'image':[model['url']+name for name in model['images']]}
    schema_json = json.dumps(schema).replace('<', '\\u003c').replace('>', '\\u003e')
    page = re.sub(r'<script type="application/ld\+json">.*?</script>', '', page, flags=re.S)
    page = page.replace('content="./index.html"', 'content="'+html.escape(model['url'], quote=True)+'"')
    page = page.replace('property="og:image" content="cover.png"',
                        'property="og:image" content="'+html.escape(model['url']+'cover.png', quote=True)+'"')
    page = page.replace('</head>', '<link rel="canonical" href="'+html.escape(model['url'], quote=True)+'">\n'+
                        '<script type="application/ld+json">'+schema_json+'</script>\n</head>')
    public_check(page)
    return page


def draft(aid, sample=False, products_dir=None, db_path=None):
    products = selltest.products_root(products_dir)
    model = page_model(aid, sample, products, db_path)
    page = render(model)
    source = safe_path(products, Path('samples' if sample else 'upload')/model['folder'])
    contents = {'index.html': page.encode(), 'page.json': json.dumps(model, sort_keys=True).encode()}
    for name in model['images']:
        # Decode every image so a broken asset cannot enter an approved page.
        if landing_page.HAS_PILLOW:
            with landing_page.Image.open(source/name) as image:
                image.verify()
        contents[name] = (source/name).read_bytes()
    hashes = {n: hashlib.sha256(data).hexdigest() for n, data in contents.items()}
    digest = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    slug = f"selltest-{aid}-{'sample' if sample else 'pack'}"
    relative = Path('site-drafts')/slug/digest
    target = safe_path(products, relative)
    payload = json.dumps({'directory': str(relative), 'hashes': hashes, 'slug': slug,
                          'pack_id': aid, 'sample': sample, 'linked': bool(model['link'])}, sort_keys=True)
    with closing(guardrails.connect(db_path)) as conn:
        previous = conn.execute('SELECT id,payload,status FROM approvals WHERE kind=? ORDER BY id DESC', (KIND,)).fetchall()
        latest = next(((aid, raw, status) for aid, raw, status in previous if json.loads(raw)['slug'] == slug), None)
        old = latest if latest and latest[1] == payload and latest[2] != 'rejected' else None
    if target.exists():
        for name, data in contents.items():
            if safe_path(target, name).read_bytes() != data:
                raise ValueError('Landing snapshot changed; restore original files')
    else:
        target.mkdir(parents=True)
        for name, data in contents.items():
            (target/name).write_bytes(data)
    if old:
        return old[0]
    return guardrails.request_approval(KIND, 'Landing Draft: '+model['title'], payload, db_path=db_path)


def prepare(aid=None, products_dir=None, db_path=None):
    packs = (selltest.active(db_path) or {}).get('packs', [])
    if aid is not None:
        packs = [member(aid, db_path)]
    results = []
    for pack in packs:
        for sample in (False, True):
            try:
                approval = draft(pack['id'], sample, products_dir, db_path)
                results.append(f"Pack #{pack['id']} {'sample' if sample else 'paid'} Draft #{approval}: /view {approval}; /approve {approval}")
            except (OSError, ValueError, zipfile.BadZipFile) as exc:
                results.append(f"Pack #{pack['id']} {'sample' if sample else 'paid'} Draft blocked; /upload {pack['id']}"+
                               (' sample' if sample else '')+' tho files check cheyandi.')
    return results


def snapshots(db_path=None):
    path = Path(selltest._db(db_path))
    if not path.is_file():
        return []
    with closing(sqlite3.connect(path)) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='approvals'").fetchone():
            return []
        rows = conn.execute('SELECT id,status,payload FROM approvals WHERE kind=? ORDER BY id', (KIND,)).fetchall()
    return [(aid, status, json.loads(payload)) for aid, status, payload in rows]


def build_approved(db_path=None, site_dir=None, products_dir=None):
    """Called by the existing publish-content build, never from the robot cycle."""
    products = selltest.products_root(products_dir)
    site = Path(site_dir)
    import free_sample, tg_listed
    paid_links = {row['id']:row['url'] for row in tg_listed.all_listed(db_path)}
    sample_links = free_sample.links(db_path)
    latest = {}
    for aid, status, payload in snapshots(db_path):
        latest[payload['slug']] = (aid, status, payload)
    built = []
    for slug, (aid, status, payload) in latest.items():
        dest = safe_path(site, slug)
        # A changed pending/rejected revision must not ship an older approved version.
        if status != 'approved' or not payload['linked']:
            if dest.exists():
                shutil.rmtree(dest)
            continue
        source = safe_path(products, payload['directory'])
        for name, digest in payload['hashes'].items():
            path = safe_path(source, name)
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError('Approved landing snapshot changed')
        model = json.loads((source/'page.json').read_text())
        current_link = (sample_links if payload['sample'] else paid_links).get(payload['pack_id'])
        if (model.get('url') != base_url(db_path)+'/'+slug+'/' or not current_link or
                model.get('link') != tracked_link(current_link, 'site')):
            if dest.exists(): shutil.rmtree(dest)
            continue
        public_check((source/'index.html').read_text())
        dest.mkdir(parents=True, exist_ok=True)
        for name in payload['hashes']:
            if name != 'page.json':
                target = safe_path(dest, name)
                shutil.copyfile(source/name, target)
        built.append(slug)
    return built


def base_url(db_path=None):
    import os
    path = Path(selltest._db(db_path))
    if path.is_file():
        with closing(sqlite3.connect(path)) as conn:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='sell_test_site_config'").fetchone():
                row = conn.execute('SELECT base_url FROM sell_test_site_config WHERE id=1').fetchone()
                if row:
                    return validate_base(row[0])
    # Default is this repository's actual GitHub Pages project path; custom domains use /site base.
    return validate_base(os.environ.get('SITE_BASE_URL', 'https://raj533125raj.github.io/chilli-gavva'))


def validate_base(url):
    import re
    from urllib.parse import urlsplit
    parts = urlsplit(url)
    if (parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or
            parts.query or parts.fragment or parts.port or not re.fullmatch(r'[a-zA-Z0-9.-]+', parts.netloc) or
            not re.fullmatch(r'(?:/[a-zA-Z0-9_.-]+)*/?', parts.path) or '..' in parts.path or
            '.' not in parts.hostname or parts.hostname.endswith(('.local', '.invalid'))):
        raise ValueError('Public https Site URL kavali; query leda login vivaralu undakudadhu.')
    public_check(url)
    return url.rstrip('/')


def set_base(url, db_path=None):
    url = validate_base(url)
    with closing(guardrails.connect(db_path)) as conn, conn:
        conn.execute('CREATE TABLE IF NOT EXISTS sell_test_site_config (id INTEGER PRIMARY KEY CHECK(id=1), base_url TEXT NOT NULL)')
        conn.execute('INSERT INTO sell_test_site_config VALUES(1,?) ON CONFLICT(id) DO UPDATE SET base_url=excluded.base_url', (url,))
    return url


def sitemap(site_dir, db_path=None):
    """Index only rendered approved public pages, never the private drafts directory."""
    import xml.etree.ElementTree as ET
    site = Path(site_dir)
    namespace = 'http://www.sitemaps.org/schemas/sitemap/0.9'
    ET.register_namespace('', namespace)
    tree = ET.Element('{'+namespace+'}urlset')
    for page in sorted(site.glob('*/index.html')):
        if page.is_symlink() or page.parent.is_symlink():
            raise ValueError('Public Site symlink refused')
        entry = ET.SubElement(tree, '{'+namespace+'}url')
        ET.SubElement(entry, '{'+namespace+'}loc').text = base_url(db_path)+'/'+page.parent.name+'/'
    target = safe_path(site, 'sitemap.xml')
    ET.ElementTree(tree).write(target, encoding='utf-8', xml_declaration=True)
    safe_path(site, 'robots.txt').write_text('User-agent: *\nAllow: /\nSitemap: '+base_url(db_path)+'/sitemap.xml\n')


def preview(aid, sample=False, products_dir=None, db_path=None):
    """Render the actual saved HTML body as a full-content PNG using Pillow.

    This is a readable content proof, not a browser/CSS screenshot. It includes every
    heading, paragraph, file, image, FAQ and button from the immutable draft HTML.
    """
    from html.parser import HTMLParser
    from PIL import Image, ImageDraw
    import mockups
    import re
    aid_draft = draft(aid, sample, products_dir, db_path)
    payload = next(data for number, _, data in snapshots(db_path) if number == aid_draft)
    source = safe_path(selltest.products_root(products_dir), payload['directory'])

    class Content(HTMLParser):
        def __init__(self):
            super().__init__(); self.body = False; self.blocks = []; self.current = None
        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == 'body': self.body = True
            if not self.body: return
            if tag in ('h1', 'h2', 'h3', 'p', 'li', 'a') or tag == 'span' and attrs.get('class') == 'tag-badge':
                self.current = [tag, '']; self.blocks.append(self.current)
            elif tag == 'img':
                self.blocks.append(['img', attrs['src']])
        def handle_endtag(self, tag):
            if tag == 'body': self.body = False
            if self.current and tag == self.current[0]: self.current = None
        def handle_data(self, data):
            if self.body and self.current: self.current[1] += data

    parser = Content(); parser.feed((source/'index.html').read_text())
    width = 900; padding = 48; available = width-2*padding
    measurement = ImageDraw.Draw(Image.new('RGB', (1, 1)))
    operations = []; y = padding
    for tag, content in parser.blocks:
        if tag == 'img':
            with Image.open(safe_path(source, content)) as image:
                picture = image.convert('RGB'); picture.thumbnail((available, 440))
                operations.append(('img', y, picture.copy())); y += picture.height+24
            continue
        text = re.sub(r'\s+', ' ', content).strip()
        if not text: continue
        size = {'h1':34, 'h2':27, 'h3':22}.get(tag, 20)
        font = mockups.font('bold' if tag.startswith('h') or tag == 'a' else 'regular', size)
        words = text.split(); lines = []; line = ''
        for word in words:
            candidate = (line+' '+word).strip()
            if measurement.textlength(candidate, font=font) > available-24 and line:
                lines.append(line); line = word
            else: line = candidate
        if line: lines.append(line)
        height = len(lines)*(size+9)+16
        operations.append(('text', y, (lines, font, size, tag, height))); y += height+16
    canvas = Image.new('RGB', (width, y+padding), '#f5f8f7'); draw = ImageDraw.Draw(canvas)
    for kind, top, value in operations:
        if kind == 'img': canvas.paste(value, ((width-value.width)//2, top))
        else:
            lines, font, size, tag, height = value
            if tag == 'a': draw.rounded_rectangle((padding-12, top-4, width-padding+12, top+height), radius=10, fill='#264b49')
            for i, line in enumerate(lines):
                draw.text((padding, top+i*(size+9)), line, font=font, fill='white' if tag == 'a' else '#243e3d')
    target = safe_path(source.parent, source.name+'-preview.png')
    canvas.save(target, 'PNG')
    return target
