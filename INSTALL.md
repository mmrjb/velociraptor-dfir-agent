# Install the AI preview alongside the internal application

The supplied offline bundle targets CPython 3.12 on Linux x86_64 with compatible manylinux
wheels (glibc 2.17 or later; resolved for Ubuntu 24.04's CPython 3.12.3 and glibc 2.39).
No root account, Node.js, npm build, systemd changes, model downloads, or package index is
needed for a release installation. The UI is already built into the application wheel.
The source declares Python >=3.10; CI covers 3.10 through 3.13. The supplied offline
wheelhouse is specifically for CPython 3.12 on Linux x86_64.

## Offline bundle

The complete offline bundle used in lab validation is not attached to a GitHub Release.
For installation directly from this public repository, use the source instructions in
README.md. This section applies when an administrator has provided the complete bundle,
`velociraptor-dfir-ai-preview-<version>-linux-x86_64-cp312.tar.gz`.

Transfer the archive and its `.sha256` file to a directory owned by your non-root application
account. Verify the checksum first (`sha256sum -c <archive>.sha256`), then extract it with
`tar -xzf`. Inside the extracted directory, `sha256sum -c --quiet SHA256SUMS` checks every
file and `RELEASE.json` records the version, source commit and target. Run the read-only
preflight, then the installer:

```bash
sudo bash preflight.sh        # report only; changes nothing
bash install-preview.sh
```

The preflight checks the operating system (Ubuntu 24.04 is the target; others warn),
architecture, glibc, CPython 3.12 and its required modules, every release file against
`SHA256SUMS`, and that the locked dependency set installs offline, hash-checked, with this
interpreter in a throwaway virtualenv. It checks the destination (absent, writable by the
service account, not on a `noexec` mount, free space) and the release directory's
readability for that account. It test-binds the preview port (8766) and staging port
(18766) on loopback, and names the owner of any conflicting listener. It reports the
existing `vr-dfir-ai-preview` service, its account, version, allow-listed environment keys
and state size, plus Nginx gateway listen lines and neighbouring services (8765, 8089,
11434). Every `[FAIL]` line must be resolved; exit status 1 means not ready. `--mode
fresh|upgrade`, `--dest`, `--account`, `--port`, `--staging-port` and `--unit` override the
defaults. The installer runs the install-time subset itself and stops before creating
anything if it fails. sudo is needed only to see listener owners and private files.

The default destination is `$HOME/.local/opt/vr-dfir-ai-preview/<version>`. To use another
**new** directory, pass its absolute path as the first argument. The installer refuses an
existing destination. It installs offline into a private virtualenv, creates `preview.env`
with AI disabled and empty API configuration, and keeps runtime state under that directory.
It does not register a global command or service. Existing internal 0.3.x files, config,
state, port 8765, and Velociraptor services are not installation destinations.

Dependencies are reproducible: `requirements-cp312.txt` pins every package, including pip,
by version and SHA-256, and the installer runs pip with `--no-index --require-hashes`
against the bundled `wheelhouse/cp312` only. The virtualenv is created `--without-pip` and
the bundled pip installs itself, so Ubuntu's `python3.12-venv` (ensurepip) package is not
required. The build machine's virtualenv is never copied. If a step fails, the installer
removes the incomplete destination it created. `INSTALLED_FROM.json` in the destination
records the release, source commit, lock hash and Python version used.

Run the exact executable printed by the installer with `--env-file <destination>/preview.env`.
Start it from `<destination>`, rather than a root-private temporary directory. Example:

```bash
cd "$HOME/.local/opt/vr-dfir-ai-preview/0.5.0a1"
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

## Optional direct LAN access without an SSH tunnel

An administrator can install an [authenticated Nginx HTTPS gateway](docs/lan-access.md)
on the same server. It serves the preview on the server's LAN IPv4 address at port 8766,
and optionally an already installed internal UI at port 8765. The application services
keep their existing loopback listeners. The guide includes TLS, password setup, firewall
scope, session isolation, verification and rollback, with a reusable
[Nginx configuration template](docs/nginx/vr-dfir-lan.conf.example).

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
isolated installation owned by that account at `/opt/vr-dfir-ai-preview/0.5.0a1`.
Its UI binds to `127.0.0.1:8766`; runtime state belongs in this preview's own `state/` directory.

Before installing at that path, an administrator can create its parent:

```bash
sudo install -d -m 0750 -o vrdfir -g vrdfir /opt/vr-dfir-ai-preview
```

Run the release installer as `vrdfir`, passing `/opt/vr-dfir-ai-preview/0.5.0a1` as its
destination. The account needs read/traverse access to the extracted release directory.
Configure the generated `preview.env` with the API credential path and optional Ollama
settings, retaining its private permissions. Use this preview's own state path and port;
configure required values individually rather than copying an older application's entire
environment file. Before starting the service, run from the preview installation directory:

```bash
sudo -u vrdfir /opt/vr-dfir-ai-preview/0.5.0a1/venv/bin/vr-dfir doctor \
  --env-file /opt/vr-dfir-ai-preview/0.5.0a1/preview.env --probe --probe-model
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
Use the SSH tunnel described above or the optional [LAN HTTPS gateway](docs/lan-access.md)
to open the UI from a workstation.

## Upgrade the administrator-managed preview from 0.4.0a2 to 0.5.0a1

0.5.0a1 reads 0.4.0a2 state without a migration: it only adds `derived/`, `selection.json(l)`
and `report/selected.*` beside existing files, and 0.4.0a2 was checked to still read a state
used by 0.5.0a1. Keep the old installation and a state backup until the upgrade is accepted.
The internal UI (port 8765), the Velociraptor server and Ollama are not part of this upgrade.

Set the paths once (adjust them to your installation):

```bash
REL=/path/to/transferred/release    # directory holding the archive and its .sha256
OLD=/opt/vr-dfir-ai-preview/0.4.0a2
NEW=/opt/vr-dfir-ai-preview/0.5.0a1
```

1. **Verify the archive, then inspect the current service.** Check the checksum before
   extracting, and note the unit's `User`, `WorkingDirectory`, `ExecStart` and any drop-ins:

   ```bash
   cd "$REL"
   sha256sum -c velociraptor-dfir-ai-preview-0.5.0a1-linux-x86_64-cp312.tar.gz.sha256
   tar -xzf velociraptor-dfir-ai-preview-0.5.0a1-linux-x86_64-cp312.tar.gz
   sudo bash velociraptor-dfir-ai-preview-0.5.0a1-linux-x86_64-cp312/preflight.sh --mode upgrade
   sudo systemctl cat vr-dfir-ai-preview
   OLD_STATE=$(sudo -u vrdfir sed -n 's/^VR_DFIR_STATE_DIR=//p' "$OLD/preview.env")
   echo "$OLD_STATE"   # must be the old preview's own state directory
   ```

2. **Back up the configuration** with restrictive permissions:

   ```bash
   BK=/var/backups/vr-dfir-ai-preview-$(date +%Y%m%d-%H%M%S)
   sudo install -d -m 0700 "$BK"
   sudo cp -a /etc/systemd/system/vr-dfir-ai-preview.service "$BK/"
   sudo test -d /etc/systemd/system/vr-dfir-ai-preview.service.d && \
     sudo cp -a /etc/systemd/system/vr-dfir-ai-preview.service.d "$BK/"
   sudo cp -a "$OLD/preview.env" "$BK/"
   ```

3. **Install the candidate separately and stage it on another loopback port** with a copy
   of the state (the running preview is not touched):

   ```bash
   sudo -u vrdfir bash "$REL/velociraptor-dfir-ai-preview-0.5.0a1-linux-x86_64-cp312/install-preview.sh" "$NEW"
   sudo -u vrdfir "$NEW/venv/bin/vr-dfir" --version          # vr-dfir 0.5.0a1
   sudo -u vrdfir mkdir -m 0700 "$NEW/staging-state"
   sudo -u vrdfir cp -a "$OLD_STATE/." "$NEW/staging-state/"
   sudo -u vrdfir sh -c "grep -v -E '^(VR_DFIR_STATE_DIR|VR_DFIR_UI_PORT)=' '$OLD/preview.env' \
     > '$NEW/staging.env'; printf 'VR_DFIR_STATE_DIR=%s\nVR_DFIR_UI_PORT=18766\n' \
     '$NEW/staging-state' >> '$NEW/staging.env'; chmod 600 '$NEW/staging.env'"
   sudo -u vrdfir "$NEW/venv/bin/vr-dfir" doctor --env-file "$NEW/staging.env" --probe --probe-model
   sudo -u vrdfir "$NEW/venv/bin/vr-dfir" ui --env-file "$NEW/staging.env" \
     --host 127.0.0.1 --port 18766
   ```

   From a workstation: `ssh -N -L 18766:127.0.0.1:18766 USER@SERVER`, then open
   `http://127.0.0.1:18766/`. Do not expose 18766 on the LAN; the gateway template covers
   only 8765/8766. Stop the staging UI with Ctrl+C when done. The staging copy is for
   testing; work done there is not carried over.

4. **Quiesce the preview and copy its state** for promotion. Wait until no investigation is
   running, then:

   ```bash
   sudo systemctl stop vr-dfir-ai-preview
   sudo tar -C "$(dirname "$OLD_STATE")" -cpzf "$BK/state-before-0.5.0a1.tar.gz" \
     "$(basename "$OLD_STATE")"
   sudo -u vrdfir cp -a "$OLD_STATE/." "$NEW/state/"
   sudo -u vrdfir sh -c "grep -v -E '^VR_DFIR_STATE_DIR=' '$OLD/preview.env' > '$NEW/preview.env'; \
     printf 'VR_DFIR_STATE_DIR=%s\n' '$NEW/state' >> '$NEW/preview.env'; chmod 600 '$NEW/preview.env'"
   ```

5. **Switch the service** with a drop-in, leaving the original unit file unchanged:

   ```bash
   sudo install -d -m 0755 /etc/systemd/system/vr-dfir-ai-preview.service.d
   printf '[Service]\nWorkingDirectory=%s\nExecStart=\nExecStart=%s/venv/bin/vr-dfir ui --env-file %s/preview.env --host 127.0.0.1 --port 8766\n' \
     "$NEW" "$NEW" "$NEW" | sudo tee /etc/systemd/system/vr-dfir-ai-preview.service.d/50-release.conf
   sudo systemctl daemon-reload
   sudo systemctl start vr-dfir-ai-preview
   ```

6. **Verify** status, the served UI, reports and the existing HTTPS access:

   ```bash
   curl --retry 5 --retry-connrefused --retry-delay 1 --fail --silent --show-error \
     http://127.0.0.1:8766/api/v1/status | grep -o '"version":"[^"]*"'   # "0.5.0a1"
   curl -s http://127.0.0.1:8766/ | grep -o 'assets/index-[A-Za-z0-9_-]*\.js'
   ls "$NEW"/venv/lib/python3.12/site-packages/vr_dfir/web/static/assets/index-*.js
   ```

   The two asset names must match. Open an existing investigation, its Overview and a
   report; then repeat the gateway checks in [docs/lan-access.md](docs/lan-access.md)
   (401 without login, 200 with it, 403 for a foreign Origin, a saved review succeeding),
   with both UIs open if the internal UI is also exposed.

**Rollback.** Stop the preview, remove only the drop-in, and start the old release with its
untouched state:

```bash
sudo systemctl stop vr-dfir-ai-preview
sudo rm /etc/systemd/system/vr-dfir-ai-preview.service.d/50-release.conf
sudo systemctl daemon-reload
sudo systemctl start vr-dfir-ai-preview
curl --fail --silent http://127.0.0.1:8766/api/v1/status | grep -o '"version":"[^"]*"'   # "0.4.0a2"
```

The old release keeps using `$OLD_STATE`, which 0.5.0a1 never wrote to. Work done after the
promotion (reviews, pins, notes, new investigations) is in `$NEW/state`; 0.4.0a2 can read
that copy too (it ignores the new sidecar files) if you need it. `$BK` holds the unit,
drop-ins, environment file and a state archive. Keep everything until the upgrade is accepted.

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
The project is not published on PyPI. Install from this repository or a complete offline bundle.

## Stop or remove

Ctrl+C stops the foreground application. Keep `state/` if you need the investigations.
For the optional managed service, run `sudo systemctl disable --now vr-dfir-ai-preview`
before removing its installation; the unit is at
`/etc/systemd/system/vr-dfir-ai-preview.service`.
A release lives entirely in its chosen destination; removal is a manual decision after
backing up that state. No internal 0.3.x rollback or upgrade script is used for this edition.
