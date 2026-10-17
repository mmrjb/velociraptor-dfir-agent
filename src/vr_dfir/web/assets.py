"""Location of the built frontend assets shipped inside the package."""

from __future__ import annotations

from pathlib import Path


def static_dir() -> Path | None:
    path = Path(__file__).resolve().parent / "static"
    return path if path.is_dir() else None
