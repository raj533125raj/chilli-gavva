#!/usr/bin/env bash
# One-time migration: encrypt the plaintext agent database, check the copy, and commit the encrypted file.
# Run it once from the repo folder:  bash scripts/migrate_db.sh
# DB_KEY is typed in hidden (read -s). It is never printed, never written to a file, never on the command line.
set -euo pipefail

cd "$(dirname "$0")/.."

fail() { echo "migrate_db: $1. Nothing was committed."; exit 1; }

[ -f agent_data.db ] || fail "agent_data.db not found"
[ -e agent_data.db.enc ] && fail "agent_data.db.enc already exists"

read -r -s -p "DB_KEY (typed hidden): " DB_KEY
echo
[ -n "${DB_KEY// /}" ] || fail "DB_KEY is empty"
export DB_KEY

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"; unset DB_KEY' EXIT

python3 scripts/db_crypt.py encrypt
python3 -c "import sys; sys.path.insert(0, 'scripts'); import db_crypt; db_crypt.decrypt('agent_data.db.enc', '$TMP_DIR/check.db')"

sum_plain="$(python3 -c "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" agent_data.db)"
sum_check="$(python3 -c "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" "$TMP_DIR/check.db")"
[ "$sum_plain" = "$sum_check" ] || { rm -f agent_data.db.enc; fail "checksum mismatch after decrypt; the encrypted file was removed"; }

git add agent_data.db.enc
git commit -m "Encrypt agent database"
echo "migrate_db: done. agent_data.db.enc is committed and checked (checksums match)."
