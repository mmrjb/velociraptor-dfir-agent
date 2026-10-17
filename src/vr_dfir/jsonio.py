"""Durable, permission-restricted file helpers.

Run records, checkpoints, and raw evidence are credential-adjacent: they may
contain endpoint data. Files are written atomically (temp file + fsync +
rename) with mode 0600 and directories with mode 0700.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

FILE_MODE = 0o600
DIR_MODE = 0o700


def ensure_dir(path: Path | str, mode: int = DIR_MODE) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True, mode=mode)
    with contextlib.suppress(PermissionError):
        os.chmod(p, mode)
    return p


def canonical_json(value: Any) -> str:
    """Stable serialization used for hashing (sorted keys, no ASCII escaping)."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      default=str)


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def atomic_write_bytes(path: Path | str, data: bytes, mode: int = FILE_MODE,
                       dir_mode: int | None = DIR_MODE) -> None:
    """Write ``path`` atomically; ``dir_mode=None`` leaves the existing parent untouched."""
    target = Path(path)
    if dir_mode is not None:
        ensure_dir(target.parent, dir_mode)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, target)
        _fsync_dir(target.parent)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)
        raise


def atomic_write_text(path: Path | str, text: str, mode: int = FILE_MODE,
                      dir_mode: int | None = DIR_MODE) -> None:
    atomic_write_bytes(path, text.encode("utf-8"), mode, dir_mode)


def atomic_write_json(path: Path | str, value: Any, mode: int = FILE_MODE) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
                      mode)


def read_json(path: Path | str) -> Any:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


class JsonlWriter:
    """Append-only JSONL writer with restrictive permissions and explicit flush/fsync."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        ensure_dir(self.path.parent)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, FILE_MODE)
        self._handle = os.fdopen(fd, "a", encoding="utf-8")
        self.lines_written = 0

    def write(self, record: Any) -> None:
        self._handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        self.lines_written += 1

    def write_many(self, records: Iterable[Any]) -> None:
        for record in records:
            self.write(record)

    def sync(self) -> None:
        self._handle.flush()
        os.fsync(self._handle.fileno())

    def close(self) -> None:
        if not self._handle.closed:
            self.sync()
            self._handle.close()

    def __enter__(self) -> JsonlWriter:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def iter_jsonl(path: Path | str) -> Iterator[tuple[int, Any]]:
    """Yield ``(line_number, record)`` with one-based line numbers.

    A truncated final line (from an interrupted write) is skipped; callers that
    need to know compare counts with the manifest.
    """
    with open(path, encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.endswith("\n"):
                return
            stripped = line.strip()
            if not stripped:
                continue
            yield line_no, json.loads(stripped)


def truncate_jsonl_to_complete_lines(path: Path | str) -> int:
    """Remove a partial trailing line left by a crash. Returns the number of complete lines."""
    p = Path(path)
    if not p.exists():
        return 0
    data = p.read_bytes()
    if data and not data.endswith(b"\n"):
        cut = data.rfind(b"\n") + 1
        with open(p, "r+b") as handle:
            handle.truncate(cut)
        data = data[:cut]
    return data.count(b"\n")
