# Install the AI preview alongside the internal application

The supplied offline bundle targets CPython 3.12 on Linux x86_64 with compatible manylinux
wheels. No root account, Node.js, systemd changes, model downloads, or package index is needed
for a release installation. Python's venv/ensurepip support must already be installed.
The source declares Python >=3.10, but this candidate has only been exercised on 3.12.

## Offline release

Transfer the archive and its `.sha256` file to a directory owned by your non-root application
account. Check the checksum and extract as in README.md. From the extracted directory run:

```bash
bash install-preview.sh
```

The default destination is `$HOME/.local/opt/vr-dfir-ai-preview/0.4.0a1`. To use another
**new** directory, pass its absolute path as the first argument. The installer refuses an
existing destination. It installs offline into a private virtualenv, creates `preview.env`
with AI disabled and empty API configuration, and keeps runtime state under that directory.
It does not register a global command or service. Existing internal 0.3.x files, config,
state, port 8765, and Velociraptor services are not installation destinations.

Run the exact executable printed by the installer with `--env-file <destination>/preview.env`.
Start it from `<destination>`, rather than a root-private temporary directory. Example:

```bash
cd "$HOME/.local/opt/vr-dfir-ai-preview/0.4.0a1"
./venv/bin/vr-dfir --version
./venv/bin/vr-dfir doctor --env-file "$PWD/preview.env"
./venv/bin/vr-dfir ui --env-file "$PWD/preview.env" --port 8766
```

The UI binds to loopback. From your workstation run:

```bash
ssh -N -L 8766:127.0.0.1:8766 USER@SERVER
```

Open `http://127.0.0.1:8766/`. Replace USER and SERVER with your SSH account and server.
Keep the app in an existing terminal multiplexer session if it must survive a disconnected
SSH terminal. A managed service can be added after lab acceptance.

## Enable live reads and AI

Edit the new `preview.env` as described in CONFIGURATION.md. Grant the application account
read access to the API client file without making it world-readable. Set an existing Ollama
model, run `doctor --probe --probe-model`, and restart the preview UI to reload configuration.
A successful probe only checks reachability/model availability. Complete the real analysis
checks in VALIDATION.md separately.

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
A release lives entirely in its chosen destination; removal is a manual decision after
backing up that state. No internal 0.3.x rollback or upgrade script is used for this edition.
