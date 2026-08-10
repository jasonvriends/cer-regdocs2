"""Repository-wide release metadata."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VERSION_PATH = PROJECT_ROOT / "VERSION"


def release_version() -> str:
    value = VERSION_PATH.read_text(encoding="utf-8").strip()
    if not value:
        raise RuntimeError(f"Repository VERSION is empty: {VERSION_PATH}")
    return value
