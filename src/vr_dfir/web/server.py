"""``vr-dfir ui`` launcher. Binds to loopback only; use SSH port forwarding for remote use."""

from __future__ import annotations

import sys
import threading

from vr_dfir.config import Settings
from vr_dfir.errors import ConfigurationError
from vr_dfir.web.security import LOOPBACK_HOSTS


def serve(settings: Settings, *, host: str, port: int, open_browser: bool = False,
          opener=None, runtime=None) -> int:
    if host not in LOOPBACK_HOSTS:
        raise ConfigurationError(
            f"Refusing to bind the UI to {host!r}. This release serves the UI on loopback only.",
            next_action="Bind to 127.0.0.1 and use SSH port forwarding "
                        "(ssh -L 8766:127.0.0.1:8766 user@server).")
    try:
        import uvicorn
    except ImportError as exc:
        raise ConfigurationError(
            "The UI dependencies are not installed.",
            next_action="Install the 'ui' extra: pip install 'velociraptor-dfir-agent[ui]'.") from exc
    from vr_dfir.web.api import create_app
    from vr_dfir.web.assets import static_dir

    app = create_app(settings, runtime=runtime)
    url_host = "127.0.0.1" if host in ("127.0.0.1", "localhost") else "[::1]"
    url = f"http://{url_host}:{port}/"
    assets = static_dir()
    print(f"Velociraptor DFIR Agent UI listening on {url}", file=sys.stderr, flush=True)
    if not assets or not (assets / "index.html").is_file():
        print("Warning: built UI assets are missing; only the API is available.", file=sys.stderr)
    print(f"Remote access: ssh -N -L {port}:127.0.0.1:{port} <user>@<server>, then open "
          f"http://127.0.0.1:{port}/ on your workstation.", file=sys.stderr, flush=True)
    if open_browser and opener is not None:
        threading.Timer(1.0, lambda: opener(url)).start()
    config = uvicorn.Config(app, host=host, port=port, log_level="warning",
                            access_log=False, proxy_headers=False, server_header=False,
                            date_header=False)
    server = uvicorn.Server(config)
    server.run()
    app.state.vr.jobs.shutdown()
    return 0
