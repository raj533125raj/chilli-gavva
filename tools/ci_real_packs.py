"""Expose two actual built upload packs to the sole disposable CI checkout."""
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))


def prepare(destination):
    if not (ROOT.parent / "source.json").is_file() or ROOT.name != "repo":
        raise RuntimeError("Real-pack fixture requires the disposable parity checkout")
    import product_builder
    import sell_proof
    products = Path(destination).resolve()
    # This directory is exclusively owned by the parity runner, outside source.
    if products.exists():
        shutil.rmtree(products)
    products.mkdir(parents=True)
    for aid, kind in ((98001, 'resume'), (98002, 'sheets')):
        folder = product_builder.build_product(sell_proof.spec_for(kind), aid, out_dir=str(products))
        result = product_builder.truth_check_folder(aid, folder, db_path=str(products.parent / 'pack-proof.db'),
                                                   out_dir=str(products), notify_fn=lambda text: False)
        if result.get('verdict') != 'ready':
            raise AssertionError(str(result))
        assert (products / 'upload' / folder / 'product.zip').is_file()
    visible = ROOT / 'exported_content/products'
    visible.parent.mkdir(parents=True, exist_ok=True)
    if visible.is_symlink():
        visible.unlink()
    elif visible.exists():
        backup = ROOT.parent / "previous-products"
        if backup.exists():
            raise RuntimeError("Unexpected products directory; previous backup retained")
        visible.rename(backup)
    visible.symlink_to(products, target_is_directory=True)
    print('REAL PACKS PRESENT: 2 actual ready upload packs', flush=True)


def verify(destination):
    """Tests must leave both real fixtures present and structurally valid."""
    import zipfile
    products = Path(destination)
    for aid in (98001,98002):
        matches = list((products/'upload').glob(str(aid)+'-*'))
        if len(matches)!=1:
            raise AssertionError('Real upload pack fixture missing or ambiguous')
        pack = matches[0]
        if not (products/pack.name).is_dir() or not (pack/'listing.txt').read_text().strip():
            raise AssertionError('Real pack source or listing missing')
        with zipfile.ZipFile(pack/'product.zip') as archive:
            if not archive.namelist() or archive.testzip() is not None:
                raise AssertionError('Real pack buyer ZIP invalid')
    print('REAL PACKS RETAINED: 2 valid buyer ZIPs and listings',flush=True)


if __name__ == '__main__':
    if sys.argv[1]=='--check': verify(sys.argv[2])
    else: prepare(sys.argv[1])
