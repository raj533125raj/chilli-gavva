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
    faq = [('What will I download?', 'The download contains: '+', '.join(files)+'.'),
           ('How do I start?', 'Download the archive, unzip it and read README.txt before opening your files.'),
           ('Can I share these files?', 'Personal use only. Do not resell or redistribute.'),
           ('Is this the complete pack?', 'This is a free preview sample. The full pack is a separate download.' if sample
            else 'This page describes the complete pack. Review the real previews and file list before choosing.')]
    return dict(title=title, description=description, tags=tags, files=files, images=images,
                faq=faq, link=tracked_link(url, 'site') if url else '', sample=sample, pack_id=aid,
                folder=pack['folder'])


def render(model):
    page = landing_page.generate_landing_html(model['folder'], model['title'], model['description'],
        model['files'], model['tags'], model['faq'], model['images'])
    if model['link']:
        page = page.replace('href="[STORE LINK]"', 'href="'+html.escape(model['link'], quote=True)+'"')
        page = page.replace('[STORE LINK]', 'Get the free sample' if model['sample'] else 'View the full pack')
    else:
        page = page.replace('<a href="[STORE LINK]" class="cta-btn">[STORE LINK]</a>',
                            '<p>Store link pending owner review.</p>')
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
        previous = conn.execute('SELECT id,payload FROM approvals WHERE kind=? ORDER BY id DESC', (KIND,)).fetchall()
        latest = next(((aid, raw) for aid, raw in previous if json.loads(raw)['slug'] == slug), None)
        old = latest if latest and latest[1] == payload else None
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
        public_check((source/'index.html').read_text())
        dest.mkdir(parents=True, exist_ok=True)
        for name in payload['hashes']:
            if name != 'page.json':
                target = safe_path(dest, name)
                shutil.copyfile(source/name, target)
        built.append(slug)
    return built
