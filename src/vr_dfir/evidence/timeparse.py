"""Explicit timestamp parsing for event times.

Only declared formats are accepted. A number is interpreted only with a declared unit, never by
its magnitude. A string must carry an explicit zone (``Z`` or ``+hh:mm``) unless the source
mapping declares the zone. Anything else is left untimed with a reason, so a timeline can count
excluded rows instead of guessing. Parsed values are microseconds since the Unix epoch (UTC);
the original value is never rewritten.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

FORMATS = ("iso8601", "epoch_s", "epoch_ms", "epoch_us", "epoch_ns")
# Statuses of one row's event time. "ok" is the only timed status.
TIME_STATUSES = ("ok", "unmapped", "missing", "naive", "invalid", "out_of_range")
UNTIMED_LABELS = {
    "unmapped": "No event-time field is mapped for this source",
    "missing": "The mapped event-time field is absent, null or empty",
    "naive": "The timestamp has no time zone, so its instant is ambiguous",
    "invalid": "The timestamp could not be parsed in the declared format",
    "out_of_range": "The timestamp is outside 1980-2100 or its zone offset is invalid",
}
_MIN = datetime(1980, 1, 1, tzinfo=timezone.utc)
_MAX = datetime(2100, 1, 1, tzinfo=timezone.utc)
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_ISO_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:[.,](\d{1,9}))?)?"
    r"\s*(Z|z|UTC|[+-]\d{2}(?::?\d{2})?)?$")
_UNITS = {"epoch_s": 1_000_000, "epoch_ms": 1_000, "epoch_us": 1, "epoch_ns": 0.001}


@dataclass(frozen=True)
class ParsedTime:
    status: str
    micros: int | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def iso(self) -> str | None:
        return micros_to_iso(self.micros) if self.micros is not None else None


def micros_to_iso(micros: int) -> str:
    moment = _EPOCH + timedelta(microseconds=int(micros))
    text = moment.isoformat().replace("+00:00", "Z")
    return text


def _in_range(moment: datetime) -> bool:
    return _MIN <= moment < _MAX


def _to_micros(moment: datetime) -> int:
    delta = moment - _EPOCH
    return (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds


def parse_iso(value: str, *, assume_utc: bool = False) -> ParsedTime:
    text = value.strip()
    match = _ISO_RE.match(text)
    if not match:
        return ParsedTime("invalid", detail="not an ISO-8601 date and time")
    year, month, day, hour, minute, second, fraction, zone = match.groups()
    if zone is None:
        if not assume_utc:
            return ParsedTime("naive", detail="no time zone")
        offset = timedelta(0)
    elif zone in ("Z", "z", "UTC"):
        offset = timedelta(0)
    else:
        sign = -1 if zone[0] == "-" else 1
        digits = zone[1:].replace(":", "")
        hours = int(digits[:2])
        minutes = int(digits[2:4] or 0)
        if hours > 14 or minutes > 59 or (hours == 14 and minutes):
            return ParsedTime("out_of_range", detail=f"invalid zone offset {zone}")
        offset = sign * timedelta(hours=hours, minutes=minutes)
    micro = int((fraction or "0")[:6].ljust(6, "0"))
    try:
        moment = datetime(int(year), int(month), int(day), int(hour), int(minute),
                          int(second or 0), micro, tzinfo=timezone(offset))
    except ValueError as exc:
        return ParsedTime("invalid", detail=str(exc))
    moment = moment.astimezone(timezone.utc)
    if not _in_range(moment):
        return ParsedTime("out_of_range", detail="outside 1980-2100")
    return ParsedTime("ok", _to_micros(moment))


def parse_epoch(value, unit: str) -> ParsedTime:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        if isinstance(value, str) and re.fullmatch(r"\d{1,20}(\.\d+)?", value.strip()):
            value = float(value) if "." in value else int(value)
        else:
            return ParsedTime("invalid", detail="not a number")
    if value <= 0:
        return ParsedTime("invalid", detail="zero or negative epoch")
    micros = int(value * _UNITS[unit])
    try:
        moment = _EPOCH + timedelta(microseconds=micros)
    except OverflowError:
        return ParsedTime("out_of_range", detail="epoch overflow")
    if not _in_range(moment):
        return ParsedTime("out_of_range", detail="outside 1980-2100 for the declared unit")
    return ParsedTime("ok", micros)


def parse_time(value, fmt: str, *, assume_utc: bool = False) -> ParsedTime:
    """Parse ``value`` in the declared ``fmt``. Absent/empty values are ``missing``."""
    if fmt not in FORMATS:
        raise ValueError(f"unknown time format {fmt!r}")
    if value is None or value == "":
        return ParsedTime("missing")
    if fmt == "iso8601":
        if not isinstance(value, str):
            return ParsedTime("invalid", detail="expected a string")
        return parse_iso(value, assume_utc=assume_utc)
    return parse_epoch(value, fmt)
