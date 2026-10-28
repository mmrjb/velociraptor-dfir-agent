"""Documented network-range classification for display.

Labels describe address *space* (loopback, RFC 1918 private, documentation ranges, ...), never
intent or reputation. ``VR_DFIR_INTERNAL_NETWORKS`` adds operator-declared internal ranges.
No address is looked up, enriched or called malicious.
"""

from __future__ import annotations

import ipaddress
from functools import lru_cache

from vr_dfir.errors import ConfigurationError

_RANGES = [
    ("loopback", "Loopback", "RFC 1122 / RFC 4291", ["127.0.0.0/8", "::1/128"]),
    ("unspecified", "Unspecified (any address)", "RFC 1122 / RFC 4291", ["0.0.0.0/32", "::/128"]),
    ("link_local", "Link-local", "RFC 3927 / RFC 4291", ["169.254.0.0/16", "fe80::/10"]),
    ("documentation", "Documentation range (not routable)", "RFC 5737 / RFC 3849",
     ["192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "2001:db8::/32"]),
    ("private", "Private (RFC 1918)", "RFC 1918", ["10.0.0.0/8", "172.16.0.0/12",
                                                  "192.168.0.0/16"]),
    ("shared", "Shared address space (CGNAT)", "RFC 6598", ["100.64.0.0/10"]),
    ("unique_local", "Unique local (IPv6 private)", "RFC 4193", ["fc00::/7"]),
    ("multicast", "Multicast", "RFC 5771 / RFC 4291", ["224.0.0.0/4", "ff00::/8"]),
]
_NETS = [(cls, label, basis, [ipaddress.ip_network(n) for n in nets])
         for cls, label, basis, nets in _RANGES]


@lru_cache(maxsize=16)
def parse_internal(spec: str | None) -> tuple:
    if not spec:
        return ()
    nets = []
    for part in spec.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError as exc:
            raise ConfigurationError(f"VR_DFIR_INTERNAL_NETWORKS has an invalid range: "
                                     f"{part[:60]!r}") from exc
    return tuple(nets)


def classify(value, internal: str | None = None) -> dict | None:
    """Address class with its documented basis, or ``None`` for a non-address value."""
    if not isinstance(value, str):
        return None
    text = value.strip().strip("[]")
    if "%" in text:
        text = text.split("%", 1)[0]
    try:
        addr = ipaddress.ip_address(text)
    except ValueError:
        return None
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    for net in parse_internal(internal):
        if addr.version == net.version and addr in net:
            return {"address": str(addr), "class": "configured_internal",
                    "label": "Configured internal network",
                    "basis": f"VR_DFIR_INTERNAL_NETWORKS ({net})"}
    for cls, label, basis, nets in _NETS:
        if any(addr.version == n.version and addr in n for n in nets):
            return {"address": str(addr), "class": cls, "label": label, "basis": basis}
    return {"address": str(addr), "class": "public", "label": "Public address space",
            "basis": "Not in a documented special-purpose or configured internal range"}
