"""Best-effort inventory of durable artifacts available on disk."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..paths import (
    CONTENT_UNDERSTANDING_DIR,
    DOCLING_DIR,
    DOWNLOAD_FILES_DIR,
    NORMALIZE_DIR,
    SCOUT_RAW_DIR,
)


@dataclass(frozen=True)
class ArtifactInventory:
    scout_snapshots: int
    current_source_files: int
    stage2_sidecars: int
    historical_source_files: int
    azure_analysis_json: int
    docling_analysis_json: int
    normalized_outputs_present: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _count_files(root: Path, pattern: str) -> int:
    if not root.is_dir():
        return 0
    return sum(1 for path in root.rglob(pattern) if path.is_file())


def _current_source_files() -> list[Path]:
    if not DOWNLOAD_FILES_DIR.is_dir():
        return []
    values: list[Path] = []
    for path in DOWNLOAD_FILES_DIR.iterdir():
        if not path.is_file():
            continue
        if path.name.startswith(".") or path.name.endswith(".metadata.json"):
            continue
        values.append(path)
    return sorted(values)


def inventory() -> ArtifactInventory:
    outputs = [
        name
        for name in ("documents.jsonl", "pages.jsonl", "chunks.jsonl", "tables.jsonl", "provenance.jsonl")
        if (NORMALIZE_DIR / name).is_file()
    ]
    return ArtifactInventory(
        scout_snapshots=_count_files(SCOUT_RAW_DIR, "*.html.gz"),
        current_source_files=len(_current_source_files()),
        stage2_sidecars=_count_files(DOWNLOAD_FILES_DIR, "*.metadata.json"),
        historical_source_files=_count_files(DOWNLOAD_FILES_DIR / "_versions", "*"),
        azure_analysis_json=_count_files(CONTENT_UNDERSTANDING_DIR / "raw", "*.json"),
        docling_analysis_json=_count_files(DOCLING_DIR / "raw", "*.json"),
        normalized_outputs_present=outputs,
    )


def recovery_plan() -> dict[str, Any]:
    inv = inventory()
    sources = _current_source_files()
    sidecars = {
        path.name[: -len(".metadata.json")]
        for path in DOWNLOAD_FILES_DIR.glob("*.metadata.json")
        if path.is_file()
    } if DOWNLOAD_FILES_DIR.is_dir() else set()

    with_sidecar = 0
    minimal = 0
    for source in sources:
        if source.stem in sidecars:
            with_sidecar += 1
        else:
            minimal += 1

    tier = "none"
    if inv.scout_snapshots and inv.current_source_files:
        tier = "A"
    elif inv.current_source_files and with_sidecar:
        tier = "B"
    elif inv.current_source_files:
        tier = "C"
    elif inv.azure_analysis_json or inv.docling_analysis_json:
        tier = "D"
    elif inv.normalized_outputs_present:
        tier = "E"

    return {
        "inventory": inv.to_dict(),
        "best_recovery_tier": tier,
        "source_files_with_sidecars": with_sidecar,
        "source_files_minimal_identity_only": minimal,
        "can_rebuild_analysis_inventory_without_rerun": bool(
            inv.azure_analysis_json or inv.docling_analysis_json
        ),
        "notes": [
            "Recovered facts must be supported by surviving artifacts.",
            "Missing Scout metadata must remain explicitly unavailable rather than inferred.",
            "A rebuild must target a new SQLite file by default.",
        ],
    }
