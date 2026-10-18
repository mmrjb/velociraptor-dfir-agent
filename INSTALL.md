# Install the AI preview alongside the internal application

The supplied offline bundle targets CPython 3.12 on Linux x86_64 with compatible manylinux
wheels. No root account, Node.js, systemd changes, model downloads, or package index is needed
for a release installation. Python's venv/ensurepip support must already be installed.
The source declares Python >=3.10; CI covers 3.10 through 3.13. The supplied offline
wheelhouse is specifically for CPython 3.12 on Linux x86_64.

## Offline release

Transfer the archive and its `.sha256` file to a directory owned by your non-root application
account. Check the checksum and extract as in README.md. From the extracted directory run:

```bash
bash install-preview.sh
```

The default destination is `$HOME/.local/opt/vr-dfir-ai-preview/0.4.0a2`. To use another
**new** directory, pass its absolute path as the first argument. The installer refuses an
existing destination. It installs offline into a private virtualenv, creates `preview.env`
with AI disabled and empty API configuration, and keeps runtime state under that directory.
It does not register a global command or service. Existing internal 0.3.x files, config,
state, port 8765, and Velociraptor services are not installation destinations.

Run the exact executable printed by the installer with `--env-file <destination>/preview.env`.
Start it from `<destination>`, rather than a root-private temporary directory. Example:

```bash
cd "$HOME/.local/opt/vr-dfir-ai-preview/0.4.0a2"
./venv/bin/vr-dfir --version
./venv/bin/vr-dfir doctor --env-file "$PWD/preview.env"
./venv/bin/vr-dfir ui --env-file "$PWD/preview.env" --port 8766
```

The UI binds to loopback. From your workstation run:

```bash
ssh -N -L 8766:127.0.0.1:8766 USER@SERVER
```

Open `http://127.0.0.1:8766/`. Replace USER and SERVER with your SSH account and server.
Keep the app in an existing terminal multiplexer session, or use the optional systemd
service below, if it must survive a disconnected SSH terminal.

## Enable live reads and AI

Edit the new `preview.env` as described in CONFIGURATION.md. Grant the application account
read access to the API client file without making it world-readable. Set an existing Ollama
model, run `doctor --probe --probe-model`, and restart the preview UI to reload configuration.
A successful probe only checks reachability/model availability. Complete the real analysis
checks in VALIDATION.md separately.

## Optional systemd service

The installer itself does not create a service. The included
[`docs/systemd/vr-dfir-ai-preview.service`](docs/systemd/vr-dfir-ai-preview.service) is an
optional administrator-managed template. It assumes an existing `vrdfir` account and an
isolated installation owned by that account at `/opt/vr-dfir-ai-preview/0.4.0a2`.
Its UI binds to `127.0.0.1:8766`; runtime state belongs in this preview's own `state/` directory.

Before installing at that path, an administrator can create its parent:

```bash
sudo install -d -m 0750 -o vrdfir -g vrdfir /opt/vr-dfir-ai-preview
```

Run the release installer as `vrdfir`, passing `/opt/vr-dfir-ai-preview/0.4.0a2` as its
destination. The account needs read/traverse access to the extracted release directory.
Configure the generated `preview.env` with the API credential path and optional Ollama
settings, retaining its private permissions. Use this preview's own state path and port;
configure required values individually rather than copying an older application's entire
environment file. Before starting the service, run from the preview installation directory:

```bash
sudo -u vrdfir /opt/vr-dfir-ai-preview/0.4.0a2/venv/bin/vr-dfir doctor \
  --env-file /opt/vr-dfir-ai-preview/0.4.0a2/preview.env --probe --probe-model
```

After a successful check, run the following from a source checkout or release directory
containing the service template:

```bash
sudo install -m 0644 docs/systemd/vr-dfir-ai-preview.service \
  /etc/systemd/system/vr-dfir-ai-preview.service
sudo systemctl daemon-reload
sudo systemctl enable --now vr-dfir-ai-preview
sudo systemctl status vr-dfir-ai-preview --no-pager -l
curl --retry 5 --retry-connrefused --retry-delay 1 \
  --connect-timeout 2 --max-time 5 --fail --silent --show-error \
  http://127.0.0.1:8766/api/v1/status
```

If your installation path or account differs, edit a copy of the unit to match it before
installing. This unit has a distinct name from the internal `velociraptor-dfir-agent` service.
After changing `preview.env`, run `sudo systemctl restart vr-dfir-ai-preview`.
Inspect startup errors with `sudo journalctl -u vr-dfir-ai-preview -n 60 --no-pager`.
Use the SSH tunnel described above to open the UI from a workstation.

## Upgrade the administrator-managed preview from 0.4.0a1

Use a new destination, `/opt/vr-dfir-ai-preview/0.4.0a2`, and keep the old installation for
rollback. Wait for any running analysis to finish. Verify the archive checksum, extract the
release to a directory readable/traversable by `vrdfir`, then run its installer:

```bash
sudo -u vrdfir bash ./install-preview.sh /opt/vr-dfir-ai-preview/0.4.0a2
sudo -u vrdfir /opt/vr-dfir-ai-preview/0.4.0a2/venv/bin/vr-dfir --version
```

Stop the preview before copying state, and preserve the current service unit:

```bash
sudo cp -a /etc/systemd/system/vr-dfir-ai-preview.service \
  /opt/vr-dfir-ai-preview/0.4.0a1/vr-dfir-ai-preview.service.before-a2
sudo systemctl stop vr-dfir-ai-preview
sudo -u vrdfir cp -a /opt/vr-dfir-ai-preview/0.4.0a1/state/. \
  /opt/vr-dfir-ai-preview/0.4.0a2/state/
sudo -u vrdfir /opt/vr-dfir-ai-preview/0.4.0a2/venv/bin/python - <<'PY'
from pathlib import Path
old = Path('/opt/vr-dfir-ai-preview/0.4.0a1')
new = Path('/opt/vr-dfir-ai-preview/0.4.0a2')
lines = (old / 'preview.env').read_text().splitlines()
lines = [line for line in lines
         if line.split('=', 1)[0].strip().removeprefix('export ').strip()
         != 'VR_DFIR_STATE_DIR']
(new / 'preview.env').write_text('\n'.join(lines) + f'\nVR_DFIR_STATE_DIR={new}/state\n')
(new / 'preview.env').chmod(0o600)
PY
```

From the extracted release directory install its new service template, then start it:

```bash
sudo install -m 0644 docs/systemd/vr-dfir-ai-preview.service \
  /etc/systemd/system/vr-dfir-ai-preview.service
sudo systemctl daemon-reload
sudo systemctl start vr-dfir-ai-preview
curl --retry 5 --retry-connrefused --retry-delay 1 --connect-timeout 2 --max-time 5 \
  --fail --silent --show-error http://127.0.0.1:8766/api/v1/status
```

Existing report files retain their original rendering until rebuilt. Run from the new
installation directory, replacing `INVESTIGATION_ID` with the existing ID:

```bash
cd /opt/vr-dfir-ai-preview/0.4.0a2
sudo -u vrdfir ./venv/bin/vr-dfir report --id INVESTIGATION_ID \
  --env-file "$PWD/preview.env" --format all
```

This reads saved evidence and analysis only. It rewrites the three derived report files;
it does not call Ollama or Velociraptor. Reload the report preview and export its HTML again.
Prepared model-input metadata is distinguished from successful inference. Older records
without selection metadata show unknown coverage. HTML embeds the bundled Latin/Persian
fonts and their licenses; Markdown relies on its viewer's fonts.

For rollback, stop the preview, restore the saved unit from the a1 directory, run
`systemctl daemon-reload`, and start the preview. That unit uses the untouched a1 state copy;
work performed after the a2 upgrade is in the a2 state copy. Keep both copies until review.

## Source development

From a reviewed source checkout, using a virtualenv separate from the internal application:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
cd frontend
npm ci
npm run build
cd ..
.venv/bin/vr-dfir ui --port 8766 --state-dir "$PWD/vr-dfir-state"
```

This development path needs a package index and npm registry. Build on an executable
filesystem; `scripts/frontend.sh` supports hosts where the checkout is mounted noexec.
There is no public PyPI release for this private candidate.

## Stop or remove

Ctrl+C stops the foreground application. Keep `state/` if you need the investigations.
For the optional managed service, run `sudo systemctl disable --now vr-dfir-ai-preview`
before removing its installation; the unit is at
`/etc/systemd/system/vr-dfir-ai-preview.service`.
A release lives entirely in its chosen destination; removal is a manual decision after
backing up that state. No internal 0.3.x rollback or upgrade script is used for this edition.
