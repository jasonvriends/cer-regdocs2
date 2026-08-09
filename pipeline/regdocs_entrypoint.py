#!/usr/bin/env python3
"""Release-aware compatibility facade for transitional stage launchers."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from regdocs_paths import PIPELINE_LOG_PATH, PROJECT_ROOT, stored_path

VERSION_PATH = PROJECT_ROOT / "VERSION"
LEGACY_IMPLEMENTATION_DIR = PROJECT_ROOT / "regdocs_atlas" / "stages" / "legacy"


def release_version() -> str:
    value = VERSION_PATH.read_text(encoding="utf-8").strip()
    if not value:
        raise RuntimeError(f"Repository VERSION is empty: {VERSION_PATH}")
    return value


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def implementation_path(public_script: str | Path, implementation_name: str) -> Path:
    sibling = Path(public_script).resolve().with_name(implementation_name)
    if sibling.is_file():
        return sibling
    migrated = LEGACY_IMPLEMENTATION_DIR / implementation_name
    if migrated.is_file():
        return migrated.resolve()
    raise RuntimeError(
        f"Pipeline implementation is missing: checked {sibling} and {migrated}"
    )


def diagnostics(
    public_script: str | Path,
    implementation_name: str,
    component: Mapping[str, Any],
) -> dict[str, Any]:
    public_path = Path(public_script).resolve()
    implementation = implementation_path(public_path, implementation_name)
    result: dict[str, Any] = {
        "release_version": release_version(),
        "component": component.get("name"),
        "component_version": component.get("version"),
        "public_entrypoint": stored_path(public_path),
        "implementation": stored_path(implementation),
        "implementation_sha256": sha256_file(implementation),
        "python_version": platform.python_version(),
        "python_executable": sys.executable,
        "canonical_pipeline_log": stored_path(PIPELINE_LOG_PATH),
    }
    for key, value in component.items():
        if key not in {"name", "version"} and value is not None:
            result[key] = value
    return result


def _direct_banner(component: Mapping[str, Any]) -> None:
    if os.environ.get("REGDOCS_STAGE"):
        return
    name = str(component.get("name") or "pipeline").upper().replace("_", "/").replace("-", "/")
    print(f"REGDOCS ATLAS {release_version()}")
    print(f"MODE  {name}")
    print(f"LOG   {stored_path(PIPELINE_LOG_PATH)} (canonical when launched through pipeline.py)")


def delegate(
    public_script: str | Path,
    implementation_name: str,
    component: Mapping[str, Any],
    argv: Sequence[str] | None = None,
) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if "--version" in arguments:
        print(release_version())
        return 0
    if "--diagnostics" in arguments:
        print(json.dumps(
            diagnostics(public_script, implementation_name, component),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ))
        return 0

    _direct_banner(component)
    implementation = implementation_path(public_script, implementation_name)
    os.execv(sys.executable, [sys.executable, str(implementation), *arguments])
    return 0
