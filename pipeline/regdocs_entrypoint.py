#!/usr/bin/env python3
"""Shared release-aware facade for public REGDOCS pipeline commands.

Public stage scripts use this helper so ``--version`` consistently reports the
repository release while implementation/component identities remain available
through ``--diagnostics`` and inside durable run/artifact provenance.
"""

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


def diagnostics(
    public_script: str | Path,
    implementation_name: str,
    component: Mapping[str, Any],
) -> dict[str, Any]:
    public_path = Path(public_script).resolve()
    implementation_path = public_path.with_name(implementation_name).resolve()
    result: dict[str, Any] = {
        "release_version": release_version(),
        "component": component.get("name"),
        "component_version": component.get("version"),
        "public_entrypoint": stored_path(public_path),
        "implementation": stored_path(implementation_path),
        "implementation_sha256": sha256_file(implementation_path),
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
        print(
            json.dumps(
                diagnostics(public_script, implementation_name, component),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    _direct_banner(component)
    implementation_path = Path(public_script).resolve().with_name(implementation_name)
    if not implementation_path.is_file():
        raise RuntimeError(f"Pipeline implementation is missing: {implementation_path}")
    os.execv(sys.executable, [sys.executable, str(implementation_path), *arguments])
    return 0
