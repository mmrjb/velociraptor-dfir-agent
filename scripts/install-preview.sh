#!/usr/bin/env bash
# User-owned, isolated offline installation. Never installs a system service.
# Dependencies come only from this release's hash-pinned lock and wheelhouse. The virtualenv
# is created without ensurepip, so Ubuntu's python3.12-venv package is not required.
set -euo pipefail
umask 077
RELEASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-python3.12}"
command -v "$PYTHON" >/dev/null || { echo "Python interpreter not found: $PYTHON" >&2; exit 1; }
TAG="$("$PYTHON" -I -c 'import sys; print(f"{sys.implementation.name}-cp{sys.version_info.major}{sys.version_info.minor}")')"
[[ "$TAG" == cpython-cp312 ]] || { echo 'This offline preview requires CPython 3.12.' >&2; exit 1; }
[[ "$(uname -m)" == x86_64 && "$(uname -s)" == Linux ]] || {
  echo 'This offline preview targets Linux x86_64.' >&2; exit 1;
}
LOCK="$RELEASE/requirements-cp312.txt"
WHEELHOUSE="$RELEASE/wheelhouse/cp312"
shopt -s nullglob
WHEELS=("$RELEASE"/dist/velociraptor_dfir_agent-*.whl)
PIPS=("$WHEELHOUSE"/pip-*-py3-none-any.whl)
[[ ${#WHEELS[@]} == 1 && ${#PIPS[@]} == 1 && -f "$LOCK" && -f "$RELEASE/RELEASE.json" && -f "$RELEASE/preflight.py" ]] || {
  echo 'Run this script from the extracted complete preview release.' >&2; exit 1;
}
VERSION="$("$PYTHON" -I -c 'import json, sys; print(json.load(open(sys.argv[1]))["version"])' \
  "$RELEASE/RELEASE.json")"
DEST="${1:-$HOME/.local/opt/vr-dfir-ai-preview/$VERSION}"
[[ "$DEST" == /* && ! -e "$DEST" ]] || {
  echo 'Choose a new absolute destination; an existing installation will not be overwritten.' >&2; exit 1;
}
# Install-time preflight: platform, Python, release files, offline dependency set, destination.
"$PYTHON" -I "$RELEASE/preflight.py" --phase install --dest "$DEST" || {
  echo 'Preflight failed; nothing was installed.' >&2; exit 1;
}
mkdir -p "$(dirname "$DEST")"
mkdir -m 0700 "$DEST"
# Only the directory created above is removed if a later step fails.
INSTALLED=
trap '[[ -n $INSTALLED ]] || { echo "Installation failed; removing incomplete $DEST" >&2; rm -rf -- "$DEST"; }' EXIT
"$PYTHON" -I -m venv --without-pip "$DEST/venv"
# The pinned pip runs from its wheel and installs itself and the locked set into the venv.
env -u PYTHONPATH -u PYTHONHOME PYTHONNOUSERSITE=1 "$DEST/venv/bin/python" -I "${PIPS[0]}/pip" \
  install --isolated --disable-pip-version-check --no-input --no-cache-dir --no-index \
  --only-binary=:all: --require-hashes --find-links "$WHEELHOUSE" --find-links "$RELEASE/dist" \
  -r "$LOCK"
"$DEST/venv/bin/python" -I -m pip --isolated check
"$DEST/venv/bin/python" - "$DEST" "$RELEASE" <<'PY'
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import platform
import sys
destination = Path(sys.argv[1]).resolve()
release = Path(sys.argv[2])
if '\n' in str(destination) or '\r' in str(destination):
    raise SystemExit('Destination must not contain line breaks.')
(destination / 'state').mkdir(mode=0o700)
(destination / 'preview.env').write_text(
    f'VR_DFIR_STATE_DIR={destination / "state"}\n'
    'VR_DFIR_UI_HOST=127.0.0.1\nVR_DFIR_UI_PORT=8766\n'
    'VELOCIRAPTOR_API_CONFIG=\nVELOCIRAPTOR_ORG_ID=root\n'
    'VELOCIRAPTOR_MODEL_PROVIDER=none\n'
    'OLLAMA_HOST=http://127.0.0.1:11434\nOLLAMA_MODEL=\n')
(destination / 'preview.env').chmod(0o600)
info = json.loads((release / 'RELEASE.json').read_text())
lock = release / info['lock']
(destination / 'INSTALLED_FROM.json').write_text(json.dumps({
    'release': info['name'], 'version': info['version'],
    'source_commit': info['source_commit'], 'release_directory': str(release),
    'lock_sha256': hashlib.sha256(lock.read_bytes()).hexdigest(),
    'python': platform.python_version(),
    'installed_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
}, indent=2) + '\n')
PY
INSTALLED=1
printf '\nInstalled isolated preview %s. Configure %s, then run:\n' "$VERSION" "$DEST/preview.env"
printf '  cd %q\n' "$DEST"
printf '  %q doctor --env-file %q\n' "$DEST/venv/bin/vr-dfir" "$DEST/preview.env"
printf '  %q ui --env-file %q --port 8766\n' "$DEST/venv/bin/vr-dfir" "$DEST/preview.env"
