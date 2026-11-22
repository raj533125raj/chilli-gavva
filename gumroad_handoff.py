"""A private, validated folder containing exactly the files the owner uploads by hand."""
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

import listing_lint
import sell_ready

FOLDER = 'gumroad-upload'
SIZES = {'cover.png':(1280,720),'thumbnail.png':(600,600),
         'preview-1.png':(1280,720),'preview-2.png':(1280,720),'preview-3.png':(1280,720)}
FILES = ('product.zip','cover.png','thumbnail.png','preview-1.png','preview-2.png','preview-3.png','listing.txt')
MAX_FILE_BYTES = 45*1024*1024


def build(pack, source=None):
    """Copy gated listing fields and actual buyer files; never publish or contact a store."""
    pack=Path(pack)
    if pack.is_symlink():
        raise ValueError("Pack symlink undakudadhu")
    pack=pack.resolve()
    report=listing_lint.quality_report(pack)
    if not report['ok']:
        raise ValueError('Listing gate: '+'; '.join(report.get('top_fixes',[])))
    if source is not None:
        import truth_check
        truth=truth_check.check(str(source))
        if truth['verdict'] != 'ready':
            raise ValueError('Truth gate: '+'; '.join(truth.get('problems',[])))
    else:
        evaluation=sell_ready.evaluate_pack(pack)
        if evaluation['status'] not in (sell_ready.STATUS_SELLABLE,sell_ready.STATUS_ALMOST):
            raise ValueError('Pack gate: '+'; '.join(evaluation.get('top_fixes',[])))
    if not (pack/'thumbnail.png').exists():
        try:
            from PIL import Image, ImageOps
            original=Path(source)/'thumbnail.png' if source else sell_ready.truth_source(pack)/'thumbnail.png'
            candidate=original if original.is_file() and not original.is_symlink() else pack/'cover.png'
            if candidate.is_symlink():
                raise ValueError('Thumbnail source symlink undakudadhu')
            with Image.open(candidate) as image:
                ImageOps.fit(image.convert('RGB'),(600,600)).save(pack/'thumbnail.png','PNG')
        except ImportError as exc:
            raise ValueError('Image checks kosam Pillow kavali') from exc
    for name in FILES:
        path=pack/name
        if path.is_symlink() or not path.is_file():
            raise ValueError('Pack lo '+name+' file kavali')
    try:
        with zipfile.ZipFile(pack/'product.zip') as archive:
            if not archive.namelist() or archive.testzip() is not None:
                raise ValueError('Buyer ZIP teravale; pack malli build cheyandi')
    except zipfile.BadZipFile as exc:
        raise ValueError('Buyer ZIP teravale; pack malli build cheyandi') from exc
    try:
        from PIL import Image
    except ImportError as exc:
        raise ValueError('Image checks kosam Pillow kavali') from exc
    for name,size in SIZES.items():
        with Image.open(pack/name) as image:
            image.load()
            if image.size != size:
                raise ValueError(name+' size '+str(size)+' undali')
    destination=pack/FOLDER
    if destination.is_symlink():
        raise ValueError('Upload folder symlink undakudadhu')
    with tempfile.TemporaryDirectory(prefix='.handoff-',dir=pack) as temp:
        staged=Path(temp)/'new';staged.mkdir()
        for name in FILES:shutil.copyfile(pack/name,staged/name)
        previous=Path(temp)/'previous'
        if destination.exists():os.replace(destination,previous)
        try:os.replace(staged,destination)
        except OSError:
            if previous.exists():os.replace(previous,destination)
            raise
    return destination


def oversized(folder, limit=None):
    limit=MAX_FILE_BYTES if limit is None else limit
    return [name for name in FILES if (Path(folder)/name).stat().st_size>limit]


def build_missing(products_dir, db_path=None):
    """Backfill existing ready packs on a robot cycle; keep concrete per-pack refusals."""
    import product_counts
    view=product_counts.snapshot(db_path,products_dir)
    rows=([row[:3] for row in view['packs']] if view is not None
          else sell_ready.find_all_ready_packs(products_dir))
    results={}
    for aid,title,pack in rows:
        if (Path(pack)/FOLDER).is_dir():
            continue
        try:
            results[aid]=str(build(pack))
        except (OSError,ValueError,zipfile.BadZipFile,RuntimeError) as exc:
            results[aid]=type(exc).__name__+': '+str(exc)
    return results
