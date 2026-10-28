#!/usr/bin/env bash
# Read-only installation preflight (see preflight.py). From the extracted release:
#   sudo bash preflight.sh [--mode auto|fresh|upgrade] [--dest DIR] [--account NAME] ...
# sudo lets it show listener owners and read the existing preview's environment file.
set -euo pipefail
RELEASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -z "${PYTHON:-}" ]]; then
  # Prefer the supported interpreter; fall back only to report what is wrong with python3.
  PYTHON=python3.12
  command -v "$PYTHON" >/dev/null 2>&1 || PYTHON=python3
fi
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "[FAIL] python            $PYTHON not found; Ubuntu 24.04 provides /usr/bin/python3.12"
  echo
  echo 'Result: NOT READY - install CPython 3.12 or set PYTHON=/path/to/python3.12.'
  exit 1
fi
exec "$PYTHON" -I "$RELEASE/preflight.py" "$@"
