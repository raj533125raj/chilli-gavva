#!/usr/bin/env bash
# One-time migration: encrypt and verify local product files, then commit only the bundle.
# Run from any directory with: bash scripts/migrate_products.sh
set -euo pipefail

cd "$(dirname "$0")/.."

fail() { echo "migrate_products: $1. Nothing was committed."; exit 1; }

[ -d exported_content/products ] || fail "exported_content/products not found"
[ ! -e products.tar.gz.enc ] || fail "products.tar.gz.enc already exists"

if [ -z "${DB_KEY:-}" ]; then
  read -r -s -p "DB_KEY (typed hidden): " DB_KEY
  echo
fi
[ -n "${DB_KEY// /}" ] || fail "DB_KEY is empty"
export DB_KEY

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"; unset DB_KEY' EXIT

python3 scripts/db_crypt.py pack-products
python3 scripts/db_crypt.py unpack-products products.tar.gz.enc "$TMP_DIR/products"
python3 - "$TMP_DIR/products" <<'PY'
import os
import sys
sys.path.insert(0, "scripts")
import db_crypt
if not db_crypt.compare_product_trees("exported_content/products", sys.argv[1]):
    raise SystemExit("migrate_products: verification failed. Nothing was committed.")
print("migrate_products: bundle verified.")
PY

git rm -r --cached -- exported_content/products
git add products.tar.gz.enc .gitignore
git commit -m "Encrypt product files"
echo "migrate_products: done. The encrypted bundle is committed; local product files remain in place."
