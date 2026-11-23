"""Read-only sell-test completeness checks with concrete owner repair commands."""
import hashlib
import json
from pathlib import Path
import zipfile
import free_sample
import gumroad_handoff
import post_planner
import selltest
import selltest_site


def kit_ok(folder, sample=False):
    try:
        folder = Path(folder)
        if folder.is_symlink(): return False
        for name in gumroad_handoff.FILES:
            path = selltest_site.safe_path(folder, name)
            if not path.is_file() or path.stat().st_size == 0: return False
        if sample:
            free_sample.validate(folder)
            extra = selltest_site.safe_path(folder, 'emails.txt')
            if not extra.is_file(): return False
            selltest_site.public_check(extra.read_text())
        else:
            import listing_lint, sell_ready
            if not listing_lint.quality_report(folder)['ok']: return False
            if sell_ready.evaluate_pack(folder)['status'] not in ('SELLABLE', 'ALMOST'): return False
            handoff = selltest_site.safe_path(folder, gumroad_handoff.FOLDER)
            for name in gumroad_handoff.FILES:
                path = selltest_site.safe_path(handoff, name)
                if path.read_bytes() != (folder/name).read_bytes(): return False
            copy = selltest_site.safe_path(handoff, 'listing-ab.txt')
            if not copy.is_file(): return False
        with zipfile.ZipFile(folder/'product.zip') as archive:
            if not archive.namelist() or archive.testzip(): return False
        from PIL import Image
        for name, size in gumroad_handoff.SIZES.items():
            with Image.open(folder/name) as image:
                image.load()
                if image.size != size: return False
        return True
    except (OSError, ValueError, ImportError, zipfile.BadZipFile):
        return False


def pins_ok(products, pack, expected_url):
    if not expected_url: return False
    try:
        import pin_ready
        folder = selltest_site.safe_path(products, Path('marketing')/pack['folder']/'pinterest')
        for path in folder.iterdir():
            selltest_site.safe_path(folder, path.name)
        if pin_ready.check_pins(folder): return False
        data = json.loads((folder/pin_ready.META_FILE).read_text())
        expected = selltest_site.tracked_link(expected_url, 'pinterest')
        return bool(data.get('pins')) and all(pin.get('link') == expected for pin in data['pins'])
    except (OSError, ValueError, ImportError):
        return False


def page_status(pack, products, db_path, snapshots):
    ids = []; states = []
    for sample in (False, True):
        slug = f"selltest-{pack['id']}-{'sample' if sample else 'pack'}"
        latest = next(((aid, status, data) for aid, status, data in reversed(snapshots) if isinstance(data, dict) and data.get('slug') == slug), None)
        if not latest or latest[1] not in ('pending', 'approved'): return False, ''
        aid, status, data = latest
        try:
            folder = selltest_site.safe_path(products, data['directory'])
            for name, digest in data['hashes'].items():
                path = selltest_site.safe_path(folder, name)
                if hashlib.sha256(path.read_bytes()).hexdigest() != digest: return False, ''
            model = json.loads((folder/'page.json').read_text())
            if json.dumps(model, sort_keys=True) != json.dumps(selltest_site.page_model(pack['id'], sample, products, db_path), sort_keys=True): return False, ''
        except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile):
            return False, ''
        ids.append(aid); states.append(status)
    label = 'Approved' if all(status == 'approved' for status in states) else 'Draft'
    commands = '; '.join('/approve '+str(aid) for aid, status in zip(ids, states) if status == 'pending')
    return True, label + (' '+commands if commands else '')


def report(db_path=None, products_dir=None, now=None):
    import tg_listed
    run = selltest.active(db_path)
    if not run:
        return 'Sell test inka start kaaledu.\nNext: /selltest pampandi.'
    products = selltest.products_root(products_dir)
    paid = {row['id']:row['url'] for row in tg_listed.all_listed(db_path)}
    samples = free_sample.links(db_path)
    today = post_planner.owner_today(now).isoformat()
    measured = {row['id'] for row in selltest.stats(db_path) if row['day'] == today}
    try:
        snapshots = selltest_site.snapshots(db_path)
    except (OSError, ValueError): snapshots = []
    lines = ['Sell test Doctor: prathi Pack missing steps ikkada unnayi.']
    first_fix = None
    for pack in run['packs']:
        aid = pack['id']; fields = []
        def field(label, ok, command, detail=''):
            nonlocal first_fix
            fields.append(label+('✅'+detail if ok else '❌ '+command))
            if not ok and first_fix is None: first_fix = command
        try:
            upload = selltest_site.safe_path(products, Path('upload')/pack['folder'])
            sample = selltest_site.safe_path(products, Path('samples')/pack['folder'])
            full_ok = kit_ok(upload); sample_ok = kit_ok(sample, True)
            if sample_ok:
                sample_ok = bool(paid.get(aid)) and paid[aid] in (sample/'README.txt').read_text().splitlines()
            pin_ok = full_ok and (aid not in samples or sample_ok) and pins_ok(products, pack, samples.get(aid) or paid.get(aid))
            site_ok, site_label = page_status(pack, products, db_path, snapshots)
        except (OSError, ValueError):
            full_ok = sample_ok = pin_ok = site_ok = False; site_label = ''
        field('Kit', full_ok, f'/upload {aid}')
        field('Sample', sample_ok, f'/upload {aid} sample')
        field('Paid link', aid in paid, f'/listed {aid} <link>')
        field('Free link', aid in samples, f'/selltest sample {aid} <link>')
        field('Pins', pin_ok, '/post today')
        field('Site', site_ok, f'/site prepare {aid}', ' '+site_label)
        command = f'/selltest stats {aid} views <views> sales <sales>' if 1 <= selltest.day_number(run, now) <= 14 else '/selltest learn'
        field('Stats', aid in measured, command)
        lines.append(f"#{aid}: " + ' | '.join(fields))
    lines.append('Next: '+(first_fix or '/today')+' pampandi.')
    return '\n'.join(lines)
