"""CCI-3: build the twelve real sellable packs ONCE per test run, in parallel, and share them.

test_ocg_all_commands_real_packs and test_och_secret_sweep both need the same twelve packs
(tools/sell_proof.spec_for + product_builder.build_product, staged like the robot does). Building them
in each module cost about 90 s per module. This helper builds them once per process (the first caller
pays), with one worker process per pack group, and removes the folder at exit.

Not a test module (no test_ prefix), so the runner does not collect it.
"""
from __future__ import annotations

import atexit
import concurrent.futures
import multiprocessing
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT, ROOT / "tools"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

MAX_WORKERS = 4
_CACHE: dict = {}


def _build_one(task):
    """Worker: one real pack. Returns (kind, folder). Runs in its own process."""
    index, kind, products, build_db = task
    import gumroad_images
    import marketing_auto
    import product_builder as pb
    import sell_proof
    import store_kits
    folder = pb.build_product(sell_proof.spec_for(kind), index, out_dir=str(products))
    report = pb.truth_check_folder(
        index, folder, db_path=build_db, out_dir=str(products),
        notify_fn=lambda _text: False,
    )
    if report.get("verdict") != "ready":
        raise AssertionError(f"{kind}: truth check {report.get('verdict')}: {report.get('problems')}")
    pack = Path(products) / "upload" / folder
    if not (pack / "product.zip").is_file():
        raise AssertionError(f"{kind}: upload pack has no product.zip")
    store_kits.build(pack)
    if gumroad_images.available():
        gumroad_images.for_pack(pack)
    marketing_auto.build_for_pack(pack, build_db, products_dir=products)
    return kind, folder


def real_packs():
    """(products folder, marketing folder) of the twelve real packs. Built on the first call only."""
    if "packs" in _CACHE:
        return _CACHE["packs"]
    import sell_proof
    import marketing_auto
    root = Path(tempfile.mkdtemp(prefix="real_packs_"))
    atexit.register(shutil.rmtree, str(root), ignore_errors=True)
    products = root / "exported_content" / "products"
    products.mkdir(parents=True, exist_ok=True)
    build_db = str(root / "build.db")
    tasks = [(index, kind, str(products), build_db)
             for index, kind in enumerate(sell_proof.FORMAT_ORDER, 1)]
    # fork keeps the parent's already-imported modules and patches; it is the same code the robot runs
    context = multiprocessing.get_context("fork")
    workers = max(1, min(MAX_WORKERS, os.cpu_count() or 1, len(tasks)))
    with concurrent.futures.ProcessPoolExecutor(max_workers=workers, mp_context=context) as pool:
        list(pool.map(_build_one, tasks))
    _CACHE["packs"] = (products, marketing_auto.marketing_root(products), build_db, root)
    return _CACHE["packs"]
