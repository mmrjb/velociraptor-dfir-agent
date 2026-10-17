"""Artifact parameter validation and serialization.

Velociraptor passes every artifact parameter to the client as a string and converts it
in the artifact preamble according to the declared type
(``services/launcher/parameters.go``, v0.77.3). This module produces those strings:

* ``bool``  -> ``"Y"`` / ``"N"`` (the compiler treats ``TRUE`` or ``^(Y|TRUE|YES|OK)$`` as true)
* ``int``/``int64``/``integer``/``float`` -> decimal string
* ``json_array``/``regex_array``/``multichoice`` -> JSON array string
* ``json`` -> JSON string
* everything else -> the string value unchanged

Types that cannot be validated locally are accepted with an explicit warning.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime

from vr_dfir.velociraptor.artifacts import ArtifactMetadata, ParameterMeta

_TRUE = {"y", "yes", "true", "ok", "1"}
_FALSE = {"n", "no", "false", "0", ""}


@dataclass
class ParameterCheck:
    serialized: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _scalar_to_str(value) -> str:
    if isinstance(value, bool):
        return "Y" if value else "N"
    return str(value)


def serialize_untyped(parameters: dict) -> dict[str, str]:
    """Serialization used when no metadata is available (offline validation)."""
    out = {}
    for key, value in parameters.items():
        if isinstance(value, list):
            out[key] = json.dumps(value, ensure_ascii=False)
        else:
            out[key] = _scalar_to_str(value)
    return out


def _bool(name: str, value, check: ParameterCheck) -> str | None:
    if isinstance(value, bool):
        return "Y" if value else "N"
    if isinstance(value, str) and value.strip().lower() in _TRUE | _FALSE:
        return "Y" if value.strip().lower() in _TRUE else "N"
    check.errors.append(f"{name}: expected a boolean (true/false or Y/N), got {value!r}.")
    return None


def _check_one(meta: ParameterMeta, value, check: ParameterCheck) -> str | None:
    name, ptype = meta.name, meta.type or "string"
    if ptype == "bool":
        return _bool(name, value, check)
    if ptype in ("int", "int64", "integer"):
        if isinstance(value, bool) or not (
            isinstance(value, int) or (isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()))
        ):
            check.errors.append(f"{name}: expected an integer, got {value!r}.")
            return None
        return str(int(value))
    if ptype == "float":
        try:
            if isinstance(value, bool):
                raise ValueError
            return repr(float(value))
        except (TypeError, ValueError):
            check.errors.append(f"{name}: expected a number, got {value!r}.")
            return None
    if ptype == "choices":
        if isinstance(value, list):
            check.errors.append(f"{name}: expected one choice, got a list.")
            return None
        text = _scalar_to_str(value)
        if meta.choices and text not in meta.choices:
            check.errors.append(f"{name}: {text!r} is not one of {meta.choices}.")
            return None
        return text
    if ptype in ("multichoice", "json_array", "regex_array"):
        items = value
        if isinstance(value, str):
            try:
                items = json.loads(value)
            except json.JSONDecodeError:
                check.errors.append(f"{name}: expected a list or a JSON array string.")
                return None
        if not isinstance(items, list):
            check.errors.append(f"{name}: expected a list.")
            return None
        if ptype == "multichoice" and meta.choices:
            bad = [i for i in items if str(i) not in meta.choices]
            if bad:
                check.errors.append(f"{name}: {bad} not in choices {meta.choices}.")
                return None
        if ptype == "regex_array":
            for item in items:
                _regex_warning(name, str(item), check)
        return json.dumps(items, ensure_ascii=False)
    if ptype == "json":
        if isinstance(value, str):
            try:
                json.loads(value)
            except json.JSONDecodeError:
                check.errors.append(f"{name}: expected valid JSON text.")
                return None
            return value
        return json.dumps(value, ensure_ascii=False)
    if ptype == "timestamp":
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
        text = str(value)
        try:
            datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            check.warnings.append(
                f"{name}: {text!r} is not ISO-8601 or epoch; Velociraptor's timestamp() may "
                "still parse it.")
        return text
    if isinstance(value, list):
        check.errors.append(f"{name}: type {ptype!r} does not accept a list.")
        return None
    text = _scalar_to_str(value)
    if ptype == "regex":
        _regex_warning(name, text, check)
    elif ptype == "hidden":
        check.warnings.append(f"{name}: overrides a hidden parameter; confirm this is intended.")
    elif ptype == "csv":
        pass
    elif ptype in ("", "string", "yara"):
        pass
    else:
        check.warnings.append(
            f"{name}: type {ptype!r} is not validated locally; the server applies its own "
            "semantics.")
    if meta.validating_regex:
        try:
            if not re.search(meta.validating_regex, text):
                check.errors.append(f"{name}: does not match the artifact's validating_regex.")
        except re.error:
            check.warnings.append(
                f"{name}: validating_regex could not be evaluated locally (RE2 syntax).")
    return text


def _regex_warning(name: str, pattern: str, check: ParameterCheck) -> None:
    try:
        re.compile(pattern)
    except re.error as exc:
        check.warnings.append(
            f"{name}: {pattern!r} does not compile as a Python regex ({exc}); Velociraptor uses "
            "Go RE2, so verify it on the server.")


def validate_parameters(meta: ArtifactMetadata | None, parameters: dict) -> ParameterCheck:
    check = ParameterCheck()
    if meta is None:
        check.serialized = serialize_untyped(parameters)
        if parameters:
            check.warnings.append(
                "Parameter names and types were not checked (no artifact metadata; offline).")
        return check
    for name, value in parameters.items():
        pmeta = meta.parameter(name)
        if pmeta is None:
            check.errors.append(
                f"{name}: {meta.name} has no parameter with this name "
                f"(available: {[p.name for p in meta.parameters] or 'none'}).")
            continue
        serialized = _check_one(pmeta, value, check)
        if serialized is not None:
            check.serialized[name] = serialized
    return check


def normalized_request_key(org_id: str, client_id: str, artifact: str,
                           serialized: dict[str, str]) -> str:
    """Key used to deduplicate identical collection requests."""
    return json.dumps([org_id, client_id, artifact, sorted(serialized.items())],
                      separators=(",", ":"))
