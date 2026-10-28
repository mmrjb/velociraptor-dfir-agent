"""Exact transport of evidence values to the browser.

JSON numbers outside the IEEE-754 safe integer range lose digits in JavaScript, and a browser
re-serializing a parsed object can reorder integer-like keys. API responses therefore carry
unsafe integers as ``{"__vr_bigint__": "<digits>"}`` (a presentation wrapper, never stored)
and the original row text as rendered by the server (``raw_text``), so Copy and the raw view
show exactly what was saved.
"""

from __future__ import annotations

import json
from typing import Any

SAFE_INT = 2**53 - 1
BIGINT_KEY = "__vr_bigint__"


def protect(value: Any) -> Any:
    """Copy of ``value`` with unsafe integers wrapped; everything else unchanged."""
    if isinstance(value, bool) or value is None or isinstance(value, (str, float)):
        return value
    if isinstance(value, int):
        return {BIGINT_KEY: str(value)} if abs(value) > SAFE_INT else value
    if isinstance(value, dict):
        return {k: protect(v) for k, v in value.items()}
    if isinstance(value, list):
        return [protect(v) for v in value]
    return value


def raw_text(row: Any) -> str:
    """The saved row as indented JSON with original key order and exact numbers."""
    return json.dumps(row, ensure_ascii=False, indent=2)


def value_text(value: Any) -> str:
    """Exact text of one value: strings unchanged, others as JSON."""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2 if isinstance(value, (dict, list))
                      else None)
