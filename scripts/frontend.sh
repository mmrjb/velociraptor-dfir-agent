#!/usr/bin/env bash
# Run frontend npm scripts from a mirror directory.
#
# Use this when the checkout lives on a filesystem mounted noexec (for example /tmp on some
# hardened hosts), where native build tools (rolldown, the TypeScript 7 compiler) cannot run.
# On ordinary hosts you can run `npm ci && npm run build` directly in frontend/.
#
#   scripts/frontend.sh build        # type-check and build into src/vr_dfir/web/static
#   scripts/frontend.sh test         # component tests (Vitest)
#   scripts/frontend.sh e2e          # Playwright journeys (needs a running UI, see docs/ui.md)
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="${VR_DFIR_FRONTEND_WORKDIR:-$HOME/.cache/vr-dfir-frontend-work}"
mkdir -p "$WORK"
rsync -a --delete --exclude node_modules --exclude test-results --exclude playwright-report \
  --exclude blob-report "$REPO/frontend/" "$WORK/"
cd "$WORK"
if [ ! -d node_modules ] || [ package-lock.json -nt node_modules/.package-lock.json ]; then
  npm ci --no-audit --no-fund
fi
export VR_DFIR_UI_OUT_DIR="$REPO/src/vr_dfir/web/static"
npm run "$@"
status=$?
for d in test-results playwright-report; do
  if [ -d "$WORK/$d" ]; then rm -rf "$REPO/frontend/$d"; cp -r "$WORK/$d" "$REPO/frontend/$d"; fi
done
exit $status
