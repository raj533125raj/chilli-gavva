"""Real pack/sample pages remain private until their exact snapshots are approved."""
import hashlib
import json
import os
from pathlib import Path
import unittest
from unittest import mock
import zipfile
from html.parser import HTMLParser
try:
    from PIL import Image
except ImportError:
    Image = None
import guardrails
import selltest_site as pages
from tests.test_cxx_selltest import NOW, CHAT


class PageParser(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.tags = []; self.refs = []; self.stack = []
        self.feed(text); self.close()
        if self.stack: raise ValueError('Unclosed HTML tags: '+str(self.stack))
    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
        if tag not in ('area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'):
            self.stack.append(tag)
        for key, value in attrs:
            if key in ('href', 'src'): self.refs.append(value)

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag: raise ValueError('Unbalanced HTML: '+tag)


@unittest.skipUnless(Image is not None, 'Pillow required')
class LandingTests(unittest.TestCase):
    def setUp(self):
        from tests.test_cxad_sample_copy import SampleListingTests
        self.fixture = SampleListingTests('test_missing_paid_link_is_honest_and_adding_link_refreshes_cached_sample')
        self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        self.db = self.fixture.db; self.products = self.fixture.products
        import tg_listed, free_sample
        tg_listed.set_listed(27, 'https://example.gumroad.com/l/full-planner', self.db, NOW)
        free_sample.build(27, self.products, self.db)
        free_sample.set_link(27, 'https://example.gumroad.com/l/sample-planner', self.db)
        self.site = self.products.parent/'public-site'

    def drafts(self):
        return [pages.draft(27, sample, self.products, self.db) for sample in (False, True)]

    def test_real_files_gated_copy_images_faq_and_tracked_buttons(self):
        ids = self.drafts()
        self.assertEqual(ids, self.drafts())
        for aid, status, data in pages.snapshots(self.db):
            self.assertEqual(status, 'pending')
            folder = self.products/data['directory']
            text = (folder/'index.html').read_text()
            model = json.loads((folder/'page.json').read_text())
            source = self.products/('samples' if data['sample'] else 'upload')/model['folder']
            with zipfile.ZipFile(source/'product.zip') as archive:
                self.assertEqual(model['files'], sorted(n for n in archive.namelist() if not n.endswith('/')))
            for name in model['files']: self.assertIn(name, text)
            self.assertIn('Frequently', text)
            readme = next(name for name in model['files'] if Path(name).name.lower().startswith('readme.'))
            self.assertIn('Read '+readme, text)
            parser = PageParser(text)
            self.assertIn('h1', [tag for tag, _ in parser.tags])
            links = [attrs['href'] for tag, attrs in parser.tags if tag == 'a' and attrs.get('href', '').startswith('https:')]
            self.assertTrue(links)
            self.assertTrue(all('utm_source=site' in link and 'utm_campaign=selltest' in link for link in links))
            for ref in parser.refs:
                if not ref.startswith(('https:', '#')):
                    self.assertTrue((folder/ref).is_file(), ref)
            for name in model['images']:
                self.assertEqual((folder/name).read_bytes(), (source/name).read_bytes())
            pages.public_check(text)

    def test_only_exact_owner_approved_snapshots_enter_existing_publish_flow(self):
        import site_builder
        ids = self.drafts()
        self.assertFalse(any(slug.startswith('selltest-') for slug in site_builder.build(self.db, self.site, self.products)))
        guardrails.decide(ids[0], True, self.db)
        built = site_builder.build(self.db, self.site, self.products)
        self.assertEqual(built, ['selltest-27-pack'])
        self.assertFalse((self.site/'selltest-27-sample').exists())
        data = pages.snapshots(self.db)[0][2]
        path = self.products/data['directory']/'index.html'
        path.write_text(path.read_text()+'changed')
        with self.assertRaises(ValueError): site_builder.build(self.db, self.site, self.products)

    def test_link_changes_require_a_new_owner_approval(self):
        import tg_listed, site_builder
        ids = self.drafts(); guardrails.decide(ids[0], True, self.db)
        site_builder.build(self.db, self.site, self.products)
        tg_listed.set_listed(27, 'https://example.gumroad.com/l/revised', self.db, NOW)
        changed = pages.draft(27, False, self.products, self.db)
        self.assertNotEqual(changed, ids[0])
        site_builder.build(self.db, self.site, self.products)
        self.assertFalse((self.site/'selltest-27-pack').exists())

    def test_absent_link_has_no_fake_button_and_cannot_ship(self):
        import tg_listed
        tg_listed.remove_listed(27, self.db)
        aid = pages.draft(27, False, self.products, self.db)
        guardrails.decide(aid, True, self.db)
        self.assertEqual(pages.build_approved(self.db, self.site, self.products), [])
        data = pages.snapshots(self.db)[0][2]
        text = (self.products/data['directory']/'index.html').read_text()
        self.assertNotIn('[STORE LINK]', text)
        self.assertIn('pending owner', text)

    def test_real_plugin_loader_and_owner_boundary(self):
        import telegram_inbox, tg_site
        ctx = {'db_path': self.db, 'products_dir': self.products, 'chat_id': CHAT, 'now': NOW}
        with mock.patch.dict(os.environ, {'TELEGRAM_CHAT_ID': CHAT}), \
             mock.patch.object(telegram_inbox, 'build_plugin_ctx', return_value=ctx):
            handled, reply = telegram_inbox.call_plugins('/site prepare 27', 'unused', CHAT, db_path=self.db, plugins=[tg_site])
            self.assertTrue(handled); self.assertIn('/approve', reply['text'])
            self.assertIsNone(tg_site.handle('/site prepare 27', dict(ctx, chat_id='other')))
        self.assertIsNone(tg_site.handle('/today', ctx))

    def test_public_guard_and_path_guard_reject_unsafe_content(self):
        with self.assertRaises(ValueError): pages.safe_path(self.products, '../outside')
        link = self.products/'unsafe'; link.symlink_to(self.site)
        with self.assertRaises(ValueError): pages.safe_path(self.products, 'unsafe/index.html')
        with self.assertRaises(ValueError): pages.public_check(chr(36)+str(9))

    def test_sitemap_schema_and_open_graph_only_reference_approved_real_pages(self):
        import re
        import site_builder
        import xml.etree.ElementTree as ET
        pages.set_base('https://example.com/shop', self.db)
        ids = self.drafts()
        for aid in ids: guardrails.decide(aid, True, self.db)
        slugs = site_builder.build(self.db, self.site, self.products)
        urls = [element.text for element in ET.parse(self.site/'sitemap.xml').getroot().iter() if element.tag.endswith('}loc')]
        self.assertEqual(set(urls), {'https://example.com/shop/'+slug+'/' for slug in slugs})
        for slug in ('selltest-27-pack', 'selltest-27-sample'):
            text = (self.site/slug/'index.html').read_text()
            schema = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', text, re.S)[1])
            self.assertEqual(schema['@type'], 'Product')
            self.assertNotRegex(json.dumps(schema).lower(), r'price|currency|offers')
            self.assertEqual(schema['url'], 'https://example.com/shop/'+slug+'/')
            parser = PageParser(text)
            og = next(attrs['content'] for tag, attrs in parser.tags if tag == 'meta' and attrs.get('property') == 'og:image')
            self.assertEqual(og, schema['url']+'cover.png')
            self.assertTrue((self.site/slug/'cover.png').is_file())
            self.assertIn('rel="canonical"', text)
        guardrails.decide(ids[1], False, self.db)  # already approved cannot silently change
        self.assertIn('/sitemap.xml', (self.site/'robots.txt').read_text())
        for unsafe in ('http://example.com', 'https://name@example.com', 'https://example.com/?key=private', 'https://example.com/../private'):
            with self.assertRaises(ValueError): pages.set_base(unsafe, self.db)

    def test_preview_renders_actual_html_content_and_loader_sends_png(self):
        import telegram_inbox, tg_site
        path = pages.preview(27, False, self.products, self.db)
        with Image.open(path) as image:
            image.load(); self.assertEqual(image.width, 900); self.assertGreater(image.height, 2000)
            self.assertGreater(len(image.getcolors(image.width*image.height)), 100)
        sent = []
        ctx = {'db_path':self.db, 'products_dir':self.products, 'chat_id':CHAT, 'now':NOW,
               'send_document':lambda p, caption='':sent.append((Path(p).name, Path(p).read_bytes())) or True}
        with mock.patch.dict(os.environ, {'TELEGRAM_CHAT_ID':CHAT}), \
             mock.patch.object(telegram_inbox, 'build_plugin_ctx', return_value=ctx):
            handled, reply = telegram_inbox.call_plugins('/site preview 27 sample', 'unused', CHAT, db_path=self.db, plugins=[tg_site])
        self.assertTrue(handled); self.assertTrue(reply['handled'])
        self.assertTrue(sent[0][0].endswith('.png')); self.assertTrue(sent[0][1].startswith(b'\x89PNG'))
