"""Durable Stage 3 manifests for SQLite-independent analysis recovery.

Canonical analyzer JSON usually contains enough structure to recompute counts, but
historical artifacts may legitimately predate the current interpretation logic.
These manifests preserve the successful ledger identity and verify the native
artifact bytes without requiring a later rebuild to reinterpret old provider JSON.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .db import open_ledger
from .db.connection import table_exists
from .paths import (
    ANALYSIS_MANIFEST_DIR,
    CONTENT_UNDERSTANDING_DIR,
    DATABASE_PATH,
    DOCLING_DIR,
    resolve_stored_path,
    stored_path,
)
from .runtime.atomic import atomic_write_json
from .runtime.hashing import sha256_file
from .version import release_version

ANALYSIS_SCHEMA = "cer-regdocs-stage3-analysis"
SCHEMA_VERSION = 1


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _identity(document_id: str, file_sha256: str, analyzer_id: str, api_version: str) -> str:
    raw = f"{document_id}\0{file_sha256}\0{analyzer_id}\0{api_version}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _manifest_path(document_id: str, file_sha256: str, analyzer_id: str, api_version: str) -> Path:
    digest = _identity(document_id, file_sha256, analyzer_id, api_version)
    return ANALYSIS_MANIFEST_DIR / digest[:2] / f"{digest}.json"


def _provider(raw_path: Path, artifact_source: Any) -> str:
    with_raw = raw_path.expanduser().resolve()
    try:
        with_raw.relative_to(CONTENT_UNDERSTANDING_DIR.resolve())
        return "azure"
    except ValueError:
        pass
    try:
        with_raw.relative_to(DOCLING_DIR.resolve())
        return "docling"
    except ValueError:
        pass
    source = str(artifact_source or "").casefold()
    return "docling" if "docling" in source else "azure"


def export_analysis_manifests(
    db_path: Path = DATABASE_PATH,
    *,
    verify_artifacts: bool = True,
) -> dict[str, Any]:
    """Export successful current-file Stage 3 ledger identities to durable manifests."""
    db_path = db_path.expanduser().resolve()
    con = open_ledger(db_path, readonly=True)
    try:
        if not table_exists(con, "analyses") or not table_exists(con, "files"):
            raise RuntimeError("Stage 3 manifest export requires analyses and files tables")
        rows = con.execute(
            """
            SELECT a.id, a.run_id, a.document_id, a.file_sha256, a.analyzer_id,
                   a.api_version, a.status, a.raw_json_path, a.markdown_path,
                   a.page_count, a.table_count, a.section_count, a.warning_count,
                   a.artifact_source, a.created_at, a.updated_at
            FROM analyses a
            JOIN files f ON f.id=a.file_id
            WHERE a.status='SUCCEEDED'
              AND f.is_current=1
              AND f.sha256=a.file_sha256
            ORDER BY a.document_id, a.analyzer_id, a.api_version
            """
        ).fetchall()
        summary: dict[str, Any] = {
            "database": str(db_path),
            "release_version": release_version(),
            "verify_artifacts": verify_artifacts,
            "analysis_rows_selected": len(rows),
            "analysis_manifests_written": 0,
            "analysis_artifacts_verified": 0,
            "analysis_artifacts_invalid": 0,
            "errors": [],
        }
        for row in rows:
            try:
                raw_value = row["raw_json_path"]
                if not raw_value:
                    raise ValueError("successful analysis has no raw_json_path")
                raw_path = resolve_stored_path(str(raw_value), legacy_base=db_path.parent)
                if not raw_path.is_file():
                    raise FileNotFoundError(raw_path)
                raw_sha = sha256_file(raw_path)
                raw_size = raw_path.stat().st_size
                if verify_artifacts:
                    summary["analysis_artifacts_verified"] += 1

                markdown_path: Path | None = None
                markdown_sha: str | None = None
                markdown_size: int | None = None
                if row["markdown_path"]:
                    candidate = resolve_stored_path(str(row["markdown_path"]), legacy_base=db_path.parent)
                    if candidate.is_file():
                        markdown_path = candidate
                        markdown_sha = sha256_file(candidate)
                        markdown_size = candidate.stat().st_size

                document_id = str(row["document_id"])
                file_sha = str(row["file_sha256"]).lower()
                analyzer_id = str(row["analyzer_id"])
                api_version = str(row["api_version"])
                payload = {
                    "schema": ANALYSIS_SCHEMA,
                    "schema_version": SCHEMA_VERSION,
                    "provider": _provider(raw_path, row["artifact_source"]),
                    "original_analysis_id": int(row["id"]),
                    "original_run_id": int(row["run_id"]) if row["run_id"] is not None else None,
                    "document_id": document_id,
                    "file_sha256": file_sha,
                    "analyzer_id": analyzer_id,
                    "api_version": api_version,
                    "status": "SUCCEEDED",
                    "raw_json_path": stored_path(raw_path),
                    "raw_json_sha256": raw_sha,
                    "raw_json_size_bytes": raw_size,
                    "markdown_path": stored_path(markdown_path) if markdown_path else None,
                    "markdown_sha256": markdown_sha,
                    "markdown_size_bytes": markdown_size,
                    "page_count": row["page_count"],
                    "table_count": row["table_count"],
                    "section_count": row["section_count"],
                    "warning_count": row["warning_count"],
                    "artifact_source": row["artifact_source"],
                    "original_created_at": row["created_at"],
                    "original_updated_at": row["updated_at"],
                    "exported_at": utcnow(),
                    "release_version": release_version(),
                }
                atomic_write_json(
                    _manifest_path(document_id, file_sha, analyzer_id, api_version), payload
                )
                summary["analysis_manifests_written"] += 1
            except Exception as exc:
                summary["analysis_artifacts_invalid"] += 1
                if len(summary["errors"]) < 50:
                    summary["errors"].append({
                        "analysis_id": int(row["id"]),
                        "document_id": str(row["document_id"]),
                        "error": f"{type(exc).__name__}: {exc}",
                    })

        summary["ok"] = summary["analysis_artifacts_invalid"] == 0
        summary["finished_at"] = utcnow()
        atomic_write_json(ANALYSIS_MANIFEST_DIR / "export-summary.json", summary)
        return summary
    finally:
        con.close()


def _iter_json(root: Path) -> Iterable[dict[str, Any]]:
    if not root.is_dir():
        return []
    values: list[dict[str, Any]] = []
    for path in root.rglob("*.json"):
        if path.name == "export-summary.json":
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            payload["_manifest_path"] = str(path.resolve())
            values.append(payload)
    return values


def analysis_manifests() -> Iterable[dict[str, Any]]:
    return (
        item for item in _iter_json(ANALYSIS_MANIFEST_DIR)
        if item.get("schema") == ANALYSIS_SCHEMA and item.get("schema_version") == SCHEMA_VERSION
    )


def manifest_inventory() -> dict[str, int | bool]:
    return {
        "stage3_analysis_manifests": sum(1 for _ in analysis_manifests()),
        "stage3_manifest_export_summary_present": (ANALYSIS_MANIFEST_DIR / "export-summary.json").is_file(),
    }
