"""Best-effort inventory of durable artifacts available on disk."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..paths import (
    ANALYSIS_MANIFEST_DIR,
    CONTENT_UNDERSTANDING_DIR,
    DOCLING_DIR,
    DOWNLOAD_FILES_DIR,
    NORMALIZE_DIR,
    SCOUT_DOCUMENT_MANIFEST_DIR,
    SCOUT_MANIFEST_DIR,
    SCOUT_RAW_DIR,
    SCOUT_SNAPSHOT_MANIFEST_DIR,
)

HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")


@dataclass(frozen=True)
class ArtifactInventory:
    scout_snapshots: int
    scout_document_manifests: int
    scout_snapshot_manifests: int
    scout_manifest_export_summary_present: bool
    current_source_files: int
    stage2_sidecars: int
    historical_source_files: int
    azure_analysis_json: int
    azure_canonical_analysis_json: int
    azure_range_result_json: int
    azure_range_metadata_json: int
    azure_other_json: int
    docling_analysis_json: int
    docling_canonical_analysis_json: int
    stage3_analysis_manifests: int
    stage3_manifest_export_summary_present: bool
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


def _canonical_analysis_count(raw_root: Path) -> int:
    if not raw_root.is_dir():
        return 0
    count = 0
    for path in raw_root.rglob("*.json"):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(raw_root)
        except ValueError:
            continue
        if len(rel.parts) == 4 and HEX64.fullmatch(Path(rel.parts[-1]).stem):
            count += 1
    return count


def _azure_breakdown(raw_root: Path) -> dict[str, int]:
    result = {"total": 0, "canonical": 0, "range_result": 0, "range_metadata": 0, "other": 0}
    if not raw_root.is_dir():
        return result
    for path in raw_root.rglob("*.json"):
        if not path.is_file():
            continue
        result["total"] += 1
        try:
            rel = path.relative_to(raw_root)
        except ValueError:
            result["other"] += 1
            continue
        if len(rel.parts) == 4 and HEX64.fullmatch(Path(rel.parts[-1]).stem):
            result["canonical"] += 1
        elif ".parts" in rel.parts and path.name.endswith(".meta.json"):
            result["range_metadata"] += 1
        elif ".parts" in rel.parts and path.name.startswith("pages-"):
            result["range_result"] += 1
        else:
            result["other"] += 1
    return result


def inventory() -> ArtifactInventory:
    outputs = [
        name
        for name in ("documents.jsonl", "pages.jsonl", "chunks.jsonl", "tables.jsonl", "provenance.jsonl")
        if (NORMALIZE_DIR / name).is_file()
    ]
    azure = _azure_breakdown(CONTENT_UNDERSTANDING_DIR / "raw")
    docling_raw = DOCLING_DIR / "raw"
    return ArtifactInventory(
        scout_snapshots=_count_files(SCOUT_RAW_DIR, "*.html.gz"),
        scout_document_manifests=_count_files(SCOUT_DOCUMENT_MANIFEST_DIR, "*.json"),
        scout_snapshot_manifests=_count_files(SCOUT_SNAPSHOT_MANIFEST_DIR, "*.json"),
        scout_manifest_export_summary_present=(SCOUT_MANIFEST_DIR / "export-summary.json").is_file(),
        current_source_files=len(_current_source_files()),
        stage2_sidecars=_count_files(DOWNLOAD_FILES_DIR, "*.metadata.json"),
        historical_source_files=_count_files(DOWNLOAD_FILES_DIR / "_versions", "*"),
        azure_analysis_json=azure["total"],
        azure_canonical_analysis_json=azure["canonical"],
        azure_range_result_json=azure["range_result"],
        azure_range_metadata_json=azure["range_metadata"],
        azure_other_json=azure["other"],
        docling_analysis_json=_count_files(docling_raw, "*.json"),
        docling_canonical_analysis_json=_canonical_analysis_count(docling_raw),
        stage3_analysis_manifests=_count_files(ANALYSIS_MANIFEST_DIR, "*.json") - (1 if (ANALYSIS_MANIFEST_DIR / "export-summary.json").is_file() else 0),
        stage3_manifest_export_summary_present=(ANALYSIS_MANIFEST_DIR / "export-summary.json").is_file(),
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

    tier_a_ready = bool(
        inv.scout_document_manifests
        and inv.scout_snapshot_manifests
        and inv.current_source_files
    )
    tier = "none"
    if tier_a_ready:
        tier = "A"
    elif inv.scout_snapshots and inv.current_source_files:
        tier = "A_RAW_EVIDENCE_NEEDS_MANIFESTS"
    elif inv.current_source_files and with_sidecar:
        tier = "B"
    elif inv.current_source_files:
        tier = "C"
    elif inv.azure_canonical_analysis_json or inv.docling_canonical_analysis_json:
        tier = "D"
    elif inv.normalized_outputs_present:
        tier = "E"

    return {
        "inventory": inv.to_dict(),
        "best_recovery_tier": tier,
        "tier_a_rebuild_ready": tier_a_ready,
        "tier_a_prepare_needed": bool(inv.scout_snapshots and not tier_a_ready),
        "source_files_with_sidecars": with_sidecar,
        "source_files_minimal_identity_only": minimal,
        "can_rebuild_analysis_inventory_without_rerun": bool(
            inv.stage3_analysis_manifests
            or inv.azure_canonical_analysis_json
            or inv.docling_canonical_analysis_json
        ),
        "notes": [
            "Recovered facts must be supported by surviving artifacts.",
            "Raw Scout HTML proves content but needs durable manifests to reconstruct request/document provenance exactly.",
            "Successful Stage 3 manifests preserve ledger identity/counts while artifact hashes prove the analyzer bytes.",
            "Missing Scout metadata must remain explicitly unavailable rather than inferred.",
            "A rebuild must target a new SQLite file by default.",
        ],
    }
