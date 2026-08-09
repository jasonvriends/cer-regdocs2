"""Manifest-backed fidelity overlay for artifact-driven rebuilds.

The legacy rebuild path remains useful as a broad artifact fallback. This module
adds two exact-recovery layers after it runs:

* Scout manifest values win over Stage 2 sidecar fallbacks for acquisition fields;
* successful Stage 3 identities/counts are restored from durable manifests after
  verifying the referenced analyzer artifact bytes.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .analysis_manifests import analysis_manifests
from .db.connection import open_ledger
from .db.safety import integrity_report
from .paths import resolve_stored_path, stored_path
from .rebuild import rebuild_create as _artifact_rebuild_create
from .runtime.hashing import sha256_file
from .scout_manifests import document_manifests


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _restore_exact_scout_fields(con: sqlite3.Connection) -> dict[str, int]:
    counts = {"updated": 0, "missing_document": 0, "invalid_manifest": 0}
    for manifest in document_manifests():
        try:
            document_id = str(manifest["document_id"])
            document = manifest.get("document")
            if not isinstance(document, dict) or str(document.get("id") or "") != document_id:
                raise ValueError("document manifest identity mismatch")
            row = con.execute("SELECT 1 FROM documents WHERE id=?", (document_id,)).fetchone()
            if row is None:
                counts["missing_document"] += 1
                continue
            name = document.get("name")
            url = document.get("url")
            con.execute(
                """
                UPDATE documents
                SET name=?, url=?, item_kind=?, filing_date=?, submitter=?, company=?,
                    project=?, filing_number=?, snippet=?
                WHERE id=?
                """,
                (
                    "" if name is None else str(name),
                    "" if url is None else str(url),
                    document.get("item_kind"),
                    document.get("filing_date"),
                    document.get("submitter"),
                    document.get("company"),
                    document.get("project"),
                    document.get("filing_number"),
                    document.get("snippet"),
                    document_id,
                ),
            )
            counts["updated"] += 1
        except Exception:
            counts["invalid_manifest"] += 1
    return counts


def _verify_analysis_artifact(manifest: dict[str, Any]) -> Path:
    raw_path = resolve_stored_path(str(manifest["raw_json_path"]))
    if not raw_path.is_file():
        raise FileNotFoundError(raw_path)
    expected_size = int(manifest["raw_json_size_bytes"])
    if raw_path.stat().st_size != expected_size:
        raise ValueError(f"analysis artifact size mismatch: {raw_path}")
    expected_sha = str(manifest["raw_json_sha256"]).lower()
    actual_sha = sha256_file(raw_path).lower()
    if actual_sha != expected_sha:
        raise ValueError(f"analysis artifact SHA-256 mismatch: {raw_path}")
    return raw_path


def _restore_analysis_manifests(con: sqlite3.Connection, rebuild_id: int) -> dict[str, Any]:
    counts: dict[str, Any] = {
        "selected": 0,
        "restored": 0,
        "invalid": 0,
        "unmatched": 0,
        "errors": [],
    }
    now = con.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ','now')").fetchone()[0]
    for manifest in analysis_manifests():
        counts["selected"] += 1
        document_id = str(manifest.get("document_id") or "")
        source_sha = str(manifest.get("file_sha256") or "").lower()
        try:
            file_row = con.execute(
                """
                SELECT id FROM files
                WHERE document_id=? AND is_current=1 AND lower(sha256)=?
                """,
                (document_id, source_sha),
            ).fetchone()
            if file_row is None:
                counts["unmatched"] += 1
                continue
            raw_path = _verify_analysis_artifact(manifest)
            markdown_path: str | None = None
            if manifest.get("markdown_path"):
                candidate = resolve_stored_path(str(manifest["markdown_path"]))
                if candidate.is_file():
                    expected = manifest.get("markdown_sha256")
                    if expected and sha256_file(candidate).lower() != str(expected).lower():
                        raise ValueError(f"analysis markdown SHA-256 mismatch: {candidate}")
                    markdown_path = stored_path(candidate)

            analyzer_id = str(manifest["analyzer_id"])
            api_version = str(manifest["api_version"])
            artifact_source = manifest.get("artifact_source") or f"rebuild:{manifest.get('provider') or 'analysis'}_manifest"
            created_at = manifest.get("original_created_at") or now
            updated_at = manifest.get("original_updated_at") or created_at
            file_id = int(file_row[0])
            con.execute(
                """
                INSERT INTO analyses(
                    run_id, document_id, file_id, file_sha256, analyzer_id, api_version,
                    status, raw_json_path, markdown_path, page_count, table_count,
                    section_count, warning_count, attempt_count, artifact_source,
                    reconciled_at, created_at, updated_at
                ) VALUES (NULL, ?, ?, ?, ?, ?, 'SUCCEEDED', ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
                ON CONFLICT(file_id,file_sha256,analyzer_id,api_version) DO UPDATE SET
                    document_id=excluded.document_id,
                    status='SUCCEEDED',
                    raw_json_path=excluded.raw_json_path,
                    markdown_path=excluded.markdown_path,
                    page_count=excluded.page_count,
                    table_count=excluded.table_count,
                    section_count=excluded.section_count,
                    warning_count=excluded.warning_count,
                    artifact_source=excluded.artifact_source,
                    reconciled_at=excluded.reconciled_at,
                    updated_at=excluded.updated_at
                """,
                (
                    document_id,
                    file_id,
                    source_sha,
                    analyzer_id,
                    api_version,
                    stored_path(raw_path),
                    markdown_path,
                    manifest.get("page_count"),
                    manifest.get("table_count"),
                    manifest.get("section_count"),
                    manifest.get("warning_count"),
                    artifact_source,
                    now,
                    created_at,
                    updated_at,
                ),
            )
            row = con.execute(
                """
                SELECT id FROM analyses
                WHERE file_id=? AND file_sha256=? AND analyzer_id=? AND api_version=?
                """,
                (file_id, source_sha, analyzer_id, api_version),
            ).fetchone()
            if row is None:
                raise RuntimeError("analysis manifest restore did not resolve an analysis row")
            analysis_id = int(row[0])
            missing = ["original_run_row"] if manifest.get("original_run_id") is not None else []
            con.execute(
                """
                INSERT OR REPLACE INTO recovery_provenance(
                    rebuild_id, entity_type, entity_key, recovered_from, completeness,
                    missing_facts_json, evidence_json, created_at
                ) VALUES (?, 'analysis', ?, 'stage3_analysis_manifest+verified_artifact', ?, ?, ?, ?)
                """,
                (
                    rebuild_id,
                    str(analysis_id),
                    "partial" if missing else "complete",
                    _stable_json(missing),
                    _stable_json({
                        "manifest_path": manifest.get("_manifest_path"),
                        "original_analysis_id": manifest.get("original_analysis_id"),
                        "original_run_id": manifest.get("original_run_id"),
                        "raw_json_path": stored_path(raw_path),
                        "raw_json_sha256": manifest.get("raw_json_sha256"),
                    }),
                    now,
                ),
            )
            counts["restored"] += 1
        except Exception as exc:
            counts["invalid"] += 1
            if len(counts["errors"]) < 50:
                counts["errors"].append({
                    "document_id": document_id,
                    "file_sha256": source_sha,
                    "error": f"{type(exc).__name__}: {exc}",
                })
    return counts


def rebuild_create(output_db: Path) -> dict[str, Any]:
    """Run the artifact rebuild and then apply manifest-backed fidelity overlays."""
    result = _artifact_rebuild_create(output_db)
    db_path = Path(result["output_db"]).expanduser().resolve()
    con = open_ledger(db_path)
    try:
        rebuild_id = int(result["rebuild_id"])
        scout_exact = _restore_exact_scout_fields(con)
        stage3 = _restore_analysis_manifests(con, rebuild_id)
        con.commit()

        successful_analyses = int(
            con.execute("SELECT COUNT(*) FROM analyses WHERE status='SUCCEEDED'").fetchone()[0]
        )
        summary = dict(result.get("summary") or {})
        summary["scout_exact_field_overlay"] = scout_exact
        summary["stage3_manifest_recovery"] = stage3
        summary["successful_analyses_recovered"] = successful_analyses
        con.execute(
            "UPDATE rebuilds SET summary_json=? WHERE id=?",
            (_stable_json(summary), rebuild_id),
        )
        con.commit()
        result["summary"] = summary
        result["integrity"] = integrity_report(con)
        if stage3["invalid"] or stage3["unmatched"] or scout_exact["invalid_manifest"] or scout_exact["missing_document"]:
            result["status"] = "COMPLETED_WITH_GAPS"
            con.execute("UPDATE rebuilds SET status=? WHERE id=?", (result["status"], rebuild_id))
            con.commit()
        return result
    finally:
        con.close()
