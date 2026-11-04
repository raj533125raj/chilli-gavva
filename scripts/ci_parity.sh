#!/usr/bin/env bash
# Run the same offline suite in CI mode and normal local mode, with real images.
set -euo pipefail
cd "$(dirname "$0")/.."
python_bin="${CI_PARITY_PYTHON:-python3}"
# macOS /var is a symlink: unittest's absolute-file loader needs the same
# canonical spelling as its subprocess cwd for the existing isolated drill tests.
task_tmp=$(cd "${TMPDIR:-/tmp}" && pwd -P)
export TMPDIR="$task_tmp"
"$python_bin" -c 'import sys, mockups; assert sys.version_info >= (3, 11), "Python 3.11+ is required"; assert mockups.available(), "Install Pillow and Poppler before running parity"'
log_dir=$(mktemp -d "${TMPDIR:-/tmp}/robot-ci-parity.XXXXXX")
trap 'rm -rf "$log_dir"' EXIT
if CHILLI_TEST=1 "$python_bin" -m unittest discover -s tests > "$log_dir/ci.log" 2>&1; then
  echo "CI mode: PASS"
  sed -n '/^Ran [0-9].* tests in /p; /^OK/p' "$log_dir/ci.log"
else
  cat "$log_dir/ci.log"
  echo "CI mode: FAIL"
  exit 1
fi
if env -u CHILLI_TEST "$python_bin" -m unittest discover -s tests > "$log_dir/local.log" 2>&1; then
  echo "Local mode: PASS"
  sed -n '/^Ran [0-9].* tests in /p; /^OK/p' "$log_dir/local.log"
else
  cat "$log_dir/local.log"
  echo "Local mode: FAIL"
  exit 1
fi
