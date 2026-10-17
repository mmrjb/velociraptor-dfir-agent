#!/usr/bin/env bash
# User-owned, isolated offline installation. Never installs a system service.
set -euo pipefail
umask 077
RELEASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-python3.12}"
TAG="$("$PYTHON" -c 'import sys; print(f"cp{sys.version_info.major}{sys.version_info.minor}")')"
[[ "$TAG" == cp312 ]] || { echo 'This offline preview requires CPython 3.12.' >&2; exit 1; }
[[ "$(uname -m)" == x86_64 && "$(uname -s)" == Linux ]] || {
  echo 'This offline preview targets Linux x86_64.' >&2; exit 1;
}
DEST="${1:-$HOME/.local/opt/vr-dfir-ai-preview/0.4.0a1}"
[[ "$DEST" == /* && ! -e "$DEST" ]] || {
  echo 'Choose a new absolute destination; an existing installation will not be overwritten.' >&2; exit 1;
}
shopt -s nullglob
WHEELS=("$RELEASE"/dist/velociraptor_dfir_agent-*.whl)
[[ ${#WHEELS[@]} == 1 && -d "$RELEASE/wheelhouse/$TAG" ]] || {
  echo 'Run this script from the extracted complete preview release.' >&2; exit 1;
}
mkdir -p "$(dirname "$DEST")"
mkdir -m 0700 "$DEST"
"$PYTHON" -m venv "$DEST/venv"
"$DEST/venv/bin/python" -m pip install --disable-pip-version-check --no-input --no-index \
  --find-links "$RELEASE/wheelhouse/$TAG" "${WHEELS[0]}[ui,mcp]"
"$DEST/venv/bin/python" - "$DEST" <<'PY'
from pathlib import Path
import sys
destination = Path(sys.argv[1]).resolve()
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
PY
printf '\nInstalled isolated preview. Configure %s, then run:\n' "$DEST/preview.env"
printf '  cd %q\n' "$DEST"
printf '  %q doctor --env-file %q\n' "$DEST/venv/bin/vr-dfir" "$DEST/preview.env"
printf '  %q ui --env-file %q --port 8766\n' "$DEST/venv/bin/vr-dfir" "$DEST/preview.env"
