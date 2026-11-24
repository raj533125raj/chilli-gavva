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


if __name__ == '__main__':
    prepare(sys.argv[1])
