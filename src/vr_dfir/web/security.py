"""Local-session security for the UI API.

* Host header must be a loopback name (127.0.0.1, localhost, ::1) on any port, which
  also covers SSH port forwarding to a different local port.
* A state-changing request must carry the session cookie and a matching
  ``X-CSRF-Token`` header (HMAC of the session ID with a per-process secret). If the browser
  sends ``Origin``, it must match ``Host``.
* The session is issued automatically by ``GET /api/v1/session``; the analyst never copies
  a token or enters a password. Nothing is stored in localStorage.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from urllib.parse import urlsplit

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
SESSION_COOKIE = "vrdfir_session"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _split_host(value: str) -> tuple[str, str]:
    value = (value or "").strip().lower()
    if value.startswith("["):
        end = value.find("]")
        return value[: end + 1], value[end + 2:] if end != -1 else ""
    if value.count(":") == 1:
        host, _, port = value.partition(":")
        return host, port
    return value, ""


def host_allowed(host_header: str) -> bool:
    host, port = _split_host(host_header)
    if port and not port.isdigit():
        return False
    return host in LOOPBACK_HOSTS


def origin_matches(origin: str, host_header: str) -> bool:
    try:
        parts = urlsplit(origin)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    return (parts.netloc or "").lower() == (host_header or "").lower()


class SessionManager:
    def __init__(self, ttl_seconds: int = 12 * 3600):
        self.secret = secrets.token_bytes(32)
        self.ttl = ttl_seconds
        self._sessions: dict[str, float] = {}

    def new_session(self) -> str:
        sid = secrets.token_urlsafe(32)
        self._sessions[sid] = time.time()
        if len(self._sessions) > 1000:
            cutoff = time.time() - self.ttl
            self._sessions = {k: v for k, v in self._sessions.items() if v > cutoff}
        return sid

    def valid(self, sid: str | None) -> bool:
        if not sid:
            return False
        created = self._sessions.get(sid)
        return created is not None and time.time() - created < self.ttl

    def csrf_for(self, sid: str) -> str:
        return hmac.new(self.secret, sid.encode(), hashlib.sha256).hexdigest()

    def check_csrf(self, sid: str | None, token: str | None) -> bool:
        if not sid or not token or not self.valid(sid):
            return False
        return hmac.compare_digest(self.csrf_for(sid), token)

    def sign(self, payload: str) -> str:
        return hmac.new(self.secret, payload.encode(), hashlib.sha256).hexdigest()


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; "
        "frame-ancestors 'none'; form-action 'self'"),
}
