#!/usr/bin/env bash
# Isolated CI dependencies, hosted Python path, four workers, three UTC clocks.
set -euo pipefail
cd "$(dirname "$0")/.."
exec "${CI_PARITY_PYTHON:-python3.11}" tools/ci_parity.py "$@"
