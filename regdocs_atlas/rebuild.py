"""Artifact-driven SQLite reconstruction with explicit recovery provenance."""

from __future__ import annotations

import gzip
import hashlib
import json
import mimetypes
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .artifacts.inventory import inventory, recovery_plan
from .db.connection import open_ledger, table_exists
from .db.migrations import migrate, verify_schema
from .db.safety import integrity_report
from .paths import (
    CONTENT_UNDERSTANDING_DIR,
    DOCLING_DIR,
    DOWNLOAD_FILES_DIR,
    resolve_stored_path,
    stored_path,
)
from .runtime.hashing import sha256_file
from .scout_manifests import document_manifests, snapshot_manifests
from .version import release_version

SIDECAR_SCHEMA = "cer-regdocs-document-sidecar"
HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def _parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if value in (None, ""):
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _current_source_files() -> list[Path]:
    if not DOWNLOAD_FILES_DIR.is_dir():
        return []
    result: list[Path] = []
    for path in DOWNLOAD_FILES_DIR.iterdir():
        if not path.is_file():
            continue
        if path.name.startswith(".") or path.name.endswith(".metadata.json"):
            continue
        result.append(path.resolve())
    return sorted(result, key=lambda p: p.name.casefold())


def _sidecar_path(document_id: str) -> Path:
    return DOWNLOAD_FILES_DIR / f"{document_id}.metadata.json"


def _trusted_sidecar(document_id: str, source_sha: str) -> tuple[dict[str, Any] | None, list[str]]:
    path = _sidecar_path(document_id)
    if not path.is_file():
        return None, ["stage2_sidecar"]
    try:
        payload = _json_object(path)
    except Exception as exc:
        return None, [f"stage2_sidecar_invalid:{type(exc).__name__}"]
    if payload.get("schema") != SIDECAR_SCHEMA:
        return None, ["stage2_sidecar_schema"]
    if str(payload.get("document_id") or "") != document_id:
        return None, ["stage2_sidecar_document_id"]
    file_info = payload.get("file") if isinstance(payload.get("file"), dict) else {}
    recorded_sha = str(file_info.get("sha256") or payload.get("sha256") or "").lower()
    if not recorded_sha or recorded_sha != source_sha.lower():
        return None, ["stage2_sidecar_sha256"]
    return payload, []


def _missing_from_sidecar(sidecar: dict[str, Any], *, scout_available: bool) -> list[str]:
    missing: list[str] = [] if scout_available else ["scout_raw_evidence"]
    for key in (
        "title", "source_url", "item_kind", "filing_date", "submitter",
        "company", "project", "filing_number",
    ):
        if sidecar.get(key) in (None, ""):
            missing.append(key)
    metadata = sidecar.get("metadata") if isinstance(sidecar.get("metadata"), dict) else {}
    if not metadata.get("container_memberships") and not metadata.get("compound_memberships"):
        kind = str(sidecar.get("item_kind") or "").casefold()
        if kind in {"folder", "compound document"}:
            missing.append("container_relationships")
    return sorted(set(missing))


def _minimal_missing() -> list[str]:
    return [
        "title", "source_url", "item_kind", "filing_date", "submitter", "company",
        "project", "filing_number", "scout_raw_evidence", "container_relationships",
        "original_downloaded_at", "original_first_seen_at",
    ]


@dataclass(frozen=True)
class RecoveredFile:
    document_id: str
    file_id: int
    sha256: str
    path: Path
    completeness: str
    missing_facts: list[str]


def _insert_recovery_provenance(
    con: sqlite3.Connection,
    *,
    rebuild_id: int,
    entity_type: str,
    entity_key: str,
    recovered_from: str,
    completeness: str,
    missing_facts: Iterable[str],
    evidence: dict[str, Any],
) -> None:
    con.execute(
        """
        INSERT OR REPLACE INTO recovery_provenance(
            rebuild_id, entity_type, entity_key, recovered_from, completeness,
            missing_facts_json, evidence_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            rebuild_id, entity_type, entity_key, recovered_from, completeness,
            stable_json(sorted(set(missing_facts))), stable_json(evidence), utcnow(),
        ),
    )


def _queue_scout_repair(
    con: sqlite3.Connection,
    *,
    rebuild_id: int,
    document_id: str,
    missing_facts: list[str],
    priority: str,
    reason: str,
) -> None:
    if not missing_facts:
        return
    now = utcnow()
    con.execute(
        """
        INSERT OR REPLACE INTO recovery_tasks(
            rebuild_id, document_id, task_type, reason, priority, status,
            missing_facts_json, created_at, updated_at
        ) VALUES (?, ?, 'SCOUT_REFRESH', ?, ?, 'PENDING', ?, ?, ?)
        """,
        (rebuild_id, document_id, reason, priority, stable_json(missing_facts), now, now),
    )


def _verify_snapshot_artifact(payload: dict[str, Any]) -> Path:
    raw_path = resolve_stored_path(str(payload["relative_path"]))
    if not raw_path.is_file():
        raise FileNotFoundError(raw_path)
    compressed_size = int(payload["compressed_size_bytes"])
    if raw_path.stat().st_size != compressed_size:
        raise ValueError(
            f"compressed size mismatch for {raw_path}: "
            f"{raw_path.stat().st_size} != {compressed_size}"
        )
    with gzip.open(raw_path, "rb") as stream:
        raw = stream.read()
    expected_size = int(payload["size_bytes"])
    if len(raw) != expected_size:
        raise ValueError(f"uncompressed size mismatch for {raw_path}: {len(raw)} != {expected_size}")
    digest = hashlib.sha256(raw).hexdigest()
    expected_sha = str(payload["content_sha256"]).lower()
    if digest.lower() != expected_sha:
        raise ValueError(f"content SHA-256 mismatch for {raw_path}")
    return raw_path


def _recover_scout_snapshots(
    con: sqlite3.Connection,
    rebuild_id: int,
) -> tuple[dict[str, int], dict[int, int]]:
    counts = {"recovered": 0, "invalid": 0}
    original_to_new: dict[int, int] = {}
    for manifest in snapshot_manifests():
        try:
            raw_path = _verify_snapshot_artifact(manifest)
            source_kind = str(manifest["source_kind"])
            source_url = str(manifest["source_url"])
            content_sha = str(manifest["content_sha256"]).lower()
            con.execute(
                """
                INSERT OR IGNORE INTO raw_snapshots(
                    run_id, document_id, source_kind, source_url, final_url,
                    fetched_at, http_status, content_type, content_sha256,
                    size_bytes, compressed_size_bytes, relative_path,
                    response_headers_json, parser_version
                ) VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    manifest.get("document_id"), source_kind, source_url,
                    manifest.get("final_url"), str(manifest["fetched_at"]),
                    manifest.get("http_status"), manifest.get("content_type"),
                    content_sha, int(manifest["size_bytes"]),
                    int(manifest["compressed_size_bytes"]), stored_path(raw_path),
                    stable_json(manifest.get("response_headers") or {}),
                    str(manifest["parser_version"]),
                ),
            )
            row = con.execute(
                """
                SELECT id FROM raw_snapshots
                WHERE source_kind=? AND source_url=? AND content_sha256=?
                """,
                (source_kind, source_url, content_sha),
            ).fetchone()
            if row is None:
                raise RuntimeError("snapshot insert could not be resolved")
            new_id = int(row[0])
            original = manifest.get("original_snapshot_id")
            if original is not None:
                original_to_new[int(original)] = new_id
            missing = ["original_run_row"] if manifest.get("original_run_id") is not None else []
            _insert_recovery_provenance(
                con, rebuild_id=rebuild_id, entity_type="raw_snapshot",
                entity_key=str(new_id), recovered_from="scout_snapshot_manifest+raw_html",
                completeness="partial" if missing else "complete", missing_facts=missing,
                evidence={
                    "manifest_path": manifest.get("_manifest_path"),
                    "raw_path": stored_path(raw_path),
                    "content_sha256": content_sha,
                    "original_snapshot_id": original,
                    "original_run_id": manifest.get("original_run_id"),
                },
            )
            counts["recovered"] += 1
        except Exception:
            counts["invalid"] += 1
    return counts, original_to_new


def _mapped_snapshot_id(value: Any, mapping: dict[int, int]) -> int | None:
    if value in (None, ""):
        return None
    try:
        return mapping.get(int(value))
    except (TypeError, ValueError):
        return None


def _remap_snapshot_references(value: Any, mapping: dict[int, int]) -> Any:
    if isinstance(value, list):
        return [_remap_snapshot_references(item, mapping) for item in value]
    if not isinstance(value, dict):
        return value
    result: dict[str, Any] = {}
    for key, item in value.items():
        if key in {"snapshot_id", "detail_snapshot_id"}:
            result[key] = _mapped_snapshot_id(item, mapping)
        elif key == "snapshot_ids" and isinstance(item, list):
            result[key] = [
                mapped for raw in item
                if (mapped := _mapped_snapshot_id(raw, mapping)) is not None
            ]
        else:
            result[key] = _remap_snapshot_references(item, mapping)
    return result


def _recover_scout_documents(
    con: sqlite3.Connection,
    rebuild_id: int,
    snapshot_mapping: dict[int, int],
) -> dict[str, int]:
    counts = {"recovered": 0, "partial": 0, "invalid": 0, "non_files": 0}
    now = utcnow()
    for manifest in document_manifests():
        try:
            document_id = str(manifest["document_id"])
            document = manifest.get("document")
            if not isinstance(document, dict) or str(document.get("id") or "") != document_id:
                raise ValueError("document manifest identity mismatch")
            metadata = _remap_snapshot_references(
                _parse_json_object(document.get("metadata")), snapshot_mapping
            )
            detail_snapshot_id = _mapped_snapshot_id(
                document.get("detail_snapshot_id"), snapshot_mapping
            )
            missing: list[str] = []
            if document.get("detail_snapshot_id") is not None and detail_snapshot_id is None:
                missing.append("detail_snapshot_evidence")
            scout_status = str(document.get("scout_status") or "PENDING")
            if scout_status != "SUCCEEDED":
                missing.append(f"scout_status:{scout_status}")
            completeness = "complete" if not missing else "partial"
            acquisition_state = "RECOVERED_COMPLETE" if not missing else "RECOVERED_PARTIAL"
            scout_refresh_needed = 0 if not missing else 1
            metadata["recovery"] = {
                "rebuild_id": rebuild_id,
                "recovered_from": "scout_document_manifest",
                "completeness": completeness,
                "missing_facts": sorted(set(missing)),
                "recovered_at": now,
            }
            first_seen = str(document.get("first_seen_at") or now)
            last_seen = str(document.get("last_seen_at") or first_seen)
            created_at = str(document.get("created_at") or first_seen)
            updated_at = str(document.get("updated_at") or now)
            is_file = 1 if bool(document.get("is_file")) else 0
            con.execute(
                """
                INSERT INTO documents(
                    id, name, url, item_kind, is_file, filing_date, submitter,
                    company, project, filing_number, snippet, metadata, status,
                    scout_status, download_status, process_status, export_status,
                    detail_status, detail_last_attempt_at, detail_succeeded_at,
                    detail_snapshot_id, file_path, hash, last_error, retry_count,
                    first_seen_at, last_seen_at, created_at, updated_at,
                    acquisition_state, scout_refresh_needed, recovery_rebuild_id,
                    recovery_missing_facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING',
                          'PENDING', 'PENDING', ?, ?, ?, ?, NULL, NULL, NULL, 0,
                          ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document_id, str(document.get("name") or ""),
                    str(document.get("url") or ""), document.get("item_kind"), is_file,
                    document.get("filing_date"), document.get("submitter"),
                    document.get("company"), document.get("project"),
                    document.get("filing_number"), document.get("snippet"),
                    stable_json(metadata), str(document.get("status") or "RECOVERED"),
                    scout_status, str(document.get("detail_status") or "PENDING"),
                    document.get("detail_last_attempt_at"),
                    document.get("detail_succeeded_at"), detail_snapshot_id,
                    first_seen, last_seen, created_at, updated_at, acquisition_state,
                    scout_refresh_needed, rebuild_id, stable_json(sorted(set(missing))),
                ),
            )
            _insert_recovery_provenance(
                con, rebuild_id=rebuild_id, entity_type="document",
                entity_key=document_id, recovered_from="scout_document_manifest",
                completeness=completeness, missing_facts=missing,
                evidence={
                    "manifest_path": manifest.get("_manifest_path"),
                    "release_version": manifest.get("release_version"),
                },
            )
            if missing:
                _queue_scout_repair(
                    con, rebuild_id=rebuild_id, document_id=document_id,
                    missing_facts=sorted(set(missing)), priority="NORMAL",
                    reason="Scout manifest records an incomplete acquisition state",
                )
                counts["partial"] += 1
            if not is_file:
                counts["non_files"] += 1
            counts["recovered"] += 1
        except Exception:
            counts["invalid"] += 1
    return counts


def _recover_source(con: sqlite3.Connection, rebuild_id: int, source: Path) -> RecoveredFile:
    document_id = source.stem
    source_sha = sha256_file(source)
    size_bytes = source.stat().st_size
    sidecar, sidecar_issues = _trusted_sidecar(document_id, source_sha)
    now = utcnow()
    existing = con.execute(
        """
        SELECT id, name, url, item_kind, filing_date, submitter, company, project,
               filing_number, snippet, metadata, status, scout_status,
               first_seen_at, last_seen_at, created_at, acquisition_state,
               scout_refresh_needed, recovery_missing_facts_json
        FROM documents WHERE id=?
        """,
        (document_id,),
    ).fetchone()
    scout_available = existing is not None

    if sidecar is not None:
        missing = _missing_from_sidecar(sidecar, scout_available=scout_available)
        recovered_from = (
            "scout_document_manifest+stage2_sidecar+source_file"
            if scout_available else "stage2_sidecar+source_file"
        )
        pipeline = sidecar.get("pipeline") if isinstance(sidecar.get("pipeline"), dict) else {}
        file_info = sidecar.get("file") if isinstance(sidecar.get("file"), dict) else {}
        sidecar_metadata = dict(sidecar.get("metadata") or {}) if isinstance(sidecar.get("metadata"), dict) else {}
        if existing is not None:
            metadata = _parse_json_object(existing["metadata"])
            for key, value in sidecar_metadata.items():
                metadata.setdefault(key, value)
            title = str(existing["name"] or sidecar.get("title") or "")
            url = str(existing["url"] or sidecar.get("source_url") or "")
            item_kind = existing["item_kind"] or sidecar.get("item_kind")
            filing_date = existing["filing_date"] or sidecar.get("filing_date")
            submitter = existing["submitter"] or sidecar.get("submitter")
            company = existing["company"] or sidecar.get("company")
            project = existing["project"] or sidecar.get("project")
            filing_number = existing["filing_number"] or sidecar.get("filing_number")
            snippet = existing["snippet"] or sidecar.get("snippet")
            first_seen = str(existing["first_seen_at"] or pipeline.get("first_seen_at") or now)
            last_seen = str(existing["last_seen_at"] or pipeline.get("last_seen_at") or first_seen)
            created_at = str(existing["created_at"] or pipeline.get("created_at") or first_seen)
            acquisition_state = str(existing["acquisition_state"] or "RECOVERED_COMPLETE")
            scout_refresh_needed = int(existing["scout_refresh_needed"] or 0)
            try:
                existing_missing = json.loads(existing["recovery_missing_facts_json"] or "[]")
            except json.JSONDecodeError:
                existing_missing = []
            if isinstance(existing_missing, list):
                missing = sorted(set(missing) | {str(item) for item in existing_missing})
        else:
            metadata = sidecar_metadata
            title = str(sidecar.get("title") or "")
            url = str(sidecar.get("source_url") or "")
            item_kind = str(sidecar.get("item_kind") or "") or None
            filing_date = sidecar.get("filing_date")
            submitter = sidecar.get("submitter")
            company = sidecar.get("company")
            project = sidecar.get("project")
            filing_number = sidecar.get("filing_number")
            snippet = sidecar.get("snippet")
            first_seen = str(pipeline.get("first_seen_at") or now)
            last_seen = str(pipeline.get("last_seen_at") or first_seen)
            created_at = str(pipeline.get("created_at") or first_seen)
            acquisition_state = "RECOVERED_PARTIAL" if missing else "RECOVERED_COMPLETE"
            scout_refresh_needed = 1 if missing else 0
        downloaded_at = str(file_info.get("downloaded_at") or now)
        mime_type = str(file_info.get("content_type") or sidecar.get("content_type") or "") or None
        extension = str(file_info.get("extension") or sidecar.get("extension") or source.suffix).lstrip(".")
        original_filename = file_info.get("original_filename")
    else:
        missing = _minimal_missing() + sidecar_issues
        recovered_from = "source_file"
        metadata = {}
        title = ""
        url = ""
        item_kind = None
        filing_date = submitter = company = project = filing_number = snippet = None
        first_seen = last_seen = created_at = downloaded_at = now
        mime_type = mimetypes.guess_type(source.name)[0]
        extension = source.suffix.lstrip(".") or None
        original_filename = source.name
        acquisition_state = "RECOVERED_MINIMAL"
        scout_refresh_needed = 1

    completeness = "minimal" if sidecar is None and not scout_available else "partial" if missing else "complete"
    metadata["recovery"] = {
        "rebuild_id": rebuild_id,
        "recovered_from": recovered_from,
        "completeness": completeness,
        "missing_facts": sorted(set(missing)),
        "recovered_at": now,
    }
    stored = stored_path(source)
    if existing is None:
        con.execute(
            """
            INSERT INTO documents(
                id, name, url, item_kind, is_file, filing_date, submitter, company, project,
                filing_number, snippet, metadata, status, scout_status, download_status,
                process_status, export_status, detail_status, file_path, hash, retry_count,
                first_seen_at, last_seen_at, created_at, updated_at, acquisition_state,
                scout_refresh_needed, recovery_rebuild_id, recovery_missing_facts_json
            ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, 'RECOVERED', 'RECOVERY_NEEDED',
                      'SUCCEEDED', 'PENDING', 'PENDING', 'RECOVERY_NEEDED', ?, ?, 0,
                      ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document_id, title, url, item_kind, filing_date, submitter, company, project,
                filing_number, snippet, stable_json(metadata), stored, source_sha,
                first_seen, last_seen, created_at, now, acquisition_state,
                scout_refresh_needed, rebuild_id, stable_json(sorted(set(missing))),
            ),
        )
    else:
        con.execute(
            """
            UPDATE documents
            SET name=?, url=?, item_kind=?, is_file=1, filing_date=?, submitter=?,
                company=?, project=?, filing_number=?, snippet=?, metadata=?,
                download_status='SUCCEEDED', file_path=?, hash=?, updated_at=?,
                acquisition_state=?, scout_refresh_needed=?, recovery_rebuild_id=?,
                recovery_missing_facts_json=?
            WHERE id=?
            """,
            (
                title, url, item_kind, filing_date, submitter, company, project,
                filing_number, snippet, stable_json(metadata), stored, source_sha, now,
                acquisition_state, scout_refresh_needed, rebuild_id,
                stable_json(sorted(set(missing))), document_id,
            ),
        )
    cursor = con.execute(
        """
        INSERT INTO files(
            document_id, path, original_filename, mime_type, extension,
            size_bytes, sha256, downloaded_at, is_current
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (document_id, stored, original_filename, mime_type, extension, size_bytes, source_sha, downloaded_at),
    )
    file_id = int(cursor.lastrowid)
    _insert_recovery_provenance(
        con, rebuild_id=rebuild_id, entity_type="document", entity_key=document_id,
        recovered_from=recovered_from, completeness=completeness, missing_facts=missing,
        evidence={
            "source_path": stored,
            "source_sha256": source_sha,
            "sidecar_path": stored_path(_sidecar_path(document_id)) if sidecar is not None else None,
        },
    )
    sidecar_file = sidecar.get("file") if sidecar is not None and isinstance(sidecar.get("file"), dict) else {}
    _insert_recovery_provenance(
        con, rebuild_id=rebuild_id, entity_type="file",
        entity_key=f"{document_id}:{source_sha}", recovered_from="source_file",
        completeness="complete", missing_facts=[] if sidecar_file.get("downloaded_at") else ["original_downloaded_at"],
        evidence={"path": stored, "sha256": source_sha, "size_bytes": size_bytes},
    )
    if missing and scout_refresh_needed:
        _queue_scout_repair(
            con, rebuild_id=rebuild_id, document_id=document_id,
            missing_facts=sorted(set(missing)), priority="HIGH" if completeness == "minimal" else "NORMAL",
            reason="Scout evidence/metadata remains incomplete after artifact recovery",
        )
    else:
        con.execute(
            "DELETE FROM recovery_tasks WHERE rebuild_id=? AND document_id=? AND task_type='SCOUT_REFRESH'",
            (rebuild_id, document_id),
        )
    return RecoveredFile(document_id, file_id, source_sha, source, completeness, sorted(set(missing)))


def _analysis_counts(payload: dict[str, Any]) -> tuple[int | None, int | None, int | None, int]:
    contents = payload.get("contents")
    if not isinstance(contents, list) or not contents:
        raise ValueError("analysis artifact has no contents[]")
    pages = tables = sections = 0
    for content in contents:
        if not isinstance(content, dict):
            continue
        pages += len(content.get("pages") or []) if isinstance(content.get("pages"), list) else 0
        tables += len(content.get("tables") or []) if isinstance(content.get("tables"), list) else 0
        sections += len(content.get("sections") or []) if isinstance(content.get("sections"), list) else 0
    warnings = payload.get("warnings")
    warning_count = len(warnings) if isinstance(warnings, list) else 0
    return pages or None, tables or None, sections or None, warning_count


def _analysis_artifacts(raw_root: Path) -> Iterable[tuple[Path, str, str, str, str]]:
    if not raw_root.is_dir():
        return []
    values: list[tuple[Path, str, str, str, str]] = []
    for path in raw_root.rglob("*.json"):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(raw_root)
        except ValueError:
            continue
        if len(rel.parts) != 4:
            continue
        analyzer_id, version, document_id, filename = rel.parts
        sha = Path(filename).stem
        if not HEX64.fullmatch(sha):
            continue
        values.append((path.resolve(), analyzer_id, version, document_id, sha.lower()))
    return values


def _recover_analyses(
    con: sqlite3.Connection,
    rebuild_id: int,
    recovered: dict[str, RecoveredFile],
) -> dict[str, int]:
    counts = {"azure": 0, "docling": 0, "invalid": 0, "unmatched": 0}
    providers = (("azure", CONTENT_UNDERSTANDING_DIR), ("docling", DOCLING_DIR))
    now = utcnow()
    for provider, root in providers:
        for raw_path, analyzer_id, version, document_id, source_sha in _analysis_artifacts(root / "raw"):
            current = recovered.get(document_id)
            if current is None or current.sha256.lower() != source_sha:
                counts["unmatched"] += 1
                continue
            try:
                payload = _json_object(raw_path)
                payload_analyzer = str(payload.get("analyzerId") or analyzer_id)
                payload_version = str(payload.get("apiVersion") or version)
                if payload_analyzer != analyzer_id or payload_version != version:
                    raise ValueError("artifact path identity disagrees with payload identity")
                page_count, table_count, section_count, warning_count = _analysis_counts(payload)
                markdown = root / "markdown" / analyzer_id / version / document_id / f"{source_sha}.md"
                cursor = con.execute(
                    """
                    INSERT OR IGNORE INTO analyses(
                        run_id, document_id, file_id, file_sha256, analyzer_id, api_version,
                        status, raw_json_path, markdown_path, page_count, table_count,
                        section_count, warning_count, attempt_count, artifact_source,
                        reconciled_at, created_at, updated_at
                    ) VALUES (NULL, ?, ?, ?, ?, ?, 'SUCCEEDED', ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
                    """,
                    (
                        document_id, current.file_id, source_sha, analyzer_id, version,
                        stored_path(raw_path), stored_path(markdown) if markdown.is_file() else None,
                        page_count, table_count, section_count, warning_count,
                        f"rebuild:{provider}_artifact", now, now, now,
                    ),
                )
                row = con.execute(
                    "SELECT id FROM analyses WHERE file_id=? AND file_sha256=? AND analyzer_id=? AND api_version=?",
                    (current.file_id, source_sha, analyzer_id, version),
                ).fetchone()
                analysis_id = int(row[0]) if row is not None else int(cursor.lastrowid)
                _insert_recovery_provenance(
                    con, rebuild_id=rebuild_id, entity_type="analysis", entity_key=f"{analysis_id}",
                    recovered_from=f"{provider}_analysis_artifact", completeness="partial",
                    missing_facts=["original_run", "original_timing", "operation_history"],
                    evidence={
                        "document_id": document_id, "file_sha256": source_sha,
                        "analyzer_id": analyzer_id, "api_version": version,
                        "raw_json_path": stored_path(raw_path),
                    },
                )
                counts[provider] += 1
            except Exception:
                counts["invalid"] += 1
    return counts


def rebuild_create(output_db: Path) -> dict[str, Any]:
    """Create a new ledger from durable Scout, Stage 2, and Stage 3 artifacts."""
    output_db = output_db.expanduser().resolve()
    if output_db.exists():
        raise FileExistsError(
            f"Refusing to overwrite existing database: {output_db}. Choose a new --output path."
        )
    output_db.parent.mkdir(parents=True, exist_ok=True)
    release = release_version()
    inv = inventory().to_dict()
    plan = recovery_plan()
    con = open_ledger(output_db)
    rebuild_id: int | None = None
    try:
        migrate(con, release)
        now = utcnow()
        cursor = con.execute(
            """
            INSERT INTO rebuilds(
                status, started_at, output_db_path, inventory_json, plan_json,
                summary_json, release_version, created_at
            ) VALUES ('RUNNING', ?, ?, ?, ?, '{}', ?, ?)
            """,
            (now, str(output_db), stable_json(inv), stable_json(plan), release, now),
        )
        rebuild_id = int(cursor.lastrowid)
        con.commit()

        scout_snapshot_counts, snapshot_mapping = _recover_scout_snapshots(con, rebuild_id)
        scout_document_counts = _recover_scout_documents(con, rebuild_id, snapshot_mapping)
        con.commit()

        recovered: dict[str, RecoveredFile] = {}
        source_failures: list[dict[str, str]] = []
        for source in _current_source_files():
            try:
                item = _recover_source(con, rebuild_id, source)
                recovered[item.document_id] = item
                con.commit()
            except Exception as exc:
                con.rollback()
                source_failures.append({"path": str(source), "error": f"{type(exc).__name__}: {exc}"})

        analysis_counts = _recover_analyses(con, rebuild_id, recovered)
        con.commit()
        task_rows = con.execute(
            "SELECT priority, COUNT(*) FROM recovery_tasks WHERE rebuild_id=? AND status='PENDING' GROUP BY priority",
            (rebuild_id,),
        ).fetchall()
        tasks = {str(row[0]): int(row[1]) for row in task_rows}
        total_documents = int(con.execute("SELECT COUNT(*) FROM documents").fetchone()[0])
        raw_snapshots = int(con.execute("SELECT COUNT(*) FROM raw_snapshots").fetchone()[0])
        current_files = int(con.execute("SELECT COUNT(*) FROM files WHERE is_current=1").fetchone()[0])
        summary = {
            "documents_recovered_total": total_documents,
            "scout_documents_recovered": scout_document_counts,
            "scout_snapshots_recovered": scout_snapshot_counts,
            "current_source_files_recovered": current_files,
            "source_failures": len(source_failures),
            "source_failure_examples": source_failures[:20],
            "raw_snapshots_recovered": raw_snapshots,
            "analyses_recovered": analysis_counts,
            "scout_repair_queue": tasks,
            "normalizations_recovered": 0,
            "normalization_note": (
                "Stage 4 rows are not reconstructed until generation/artifact manifests can prove "
                "normalizer/config/input identities; existing JSONL remains preserved on disk."
            ),
        }
        schema = verify_schema(con)
        integrity = integrity_report(con)
        serious_gaps = bool(source_failures) or scout_snapshot_counts["invalid"] > 0 or scout_document_counts["invalid"] > 0
        status = "SUCCEEDED" if schema["ok"] and integrity["ok"] and not serious_gaps else "COMPLETED_WITH_GAPS"
        con.execute(
            "UPDATE rebuilds SET status=?, finished_at=?, summary_json=? WHERE id=?",
            (status, utcnow(), stable_json(summary), rebuild_id),
        )
        con.commit()
        return {
            "status": status, "output_db": str(output_db), "rebuild_id": rebuild_id,
            "summary": summary, "schema": schema, "integrity": integrity,
        }
    except Exception:
        if rebuild_id is not None and table_exists(con, "rebuilds"):
            try:
                con.execute("UPDATE rebuilds SET status='FAILED', finished_at=? WHERE id=?", (utcnow(), rebuild_id))
                con.commit()
            except Exception:
                pass
        raise
    finally:
        con.close()


def recovery_queue(db_path: Path, *, priority: str | None = None, limit: int | None = None) -> dict[str, Any]:
    db_path = db_path.expanduser().resolve()
    con = open_ledger(db_path, readonly=True)
    try:
        where = ["task_type='SCOUT_REFRESH'", "status='PENDING'"]
        params: list[Any] = []
        if priority:
            where.append("priority=?")
            params.append(priority.upper())
        sql = (
            "SELECT id, rebuild_id, document_id, reason, priority, missing_facts_json, created_at "
            f"FROM recovery_tasks WHERE {' AND '.join(where)} "
            "ORDER BY CASE priority WHEN 'HIGH' THEN 0 WHEN 'NORMAL' THEN 1 ELSE 2 END, document_id"
        )
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        rows = con.execute(sql, params).fetchall()
        tasks: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["missing_facts"] = json.loads(item.pop("missing_facts_json"))
            except Exception:
                item["missing_facts"] = []
            tasks.append(item)
        return {"database": str(db_path), "pending": len(tasks), "tasks": tasks}
    finally:
        con.close()
