"""Durable Scout manifests for SQLite-independent recovery.

Stage 1 raw HTML is content-addressed, but the gzip filename alone cannot
reconstruct request URL, document association, timestamps, headers, or DB-local
snapshot references. These small manifests make Scout evidence self-describing.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .db import open_ledger
from .db.connection import table_exists
from .paths import (
    DATABASE_PATH,
    SCOUT_DOCUMENT_MANIFEST_DIR,
    SCOUT_MANIFEST_DIR,
    SCOUT_SNAPSHOT_MANIFEST_DIR,
    resolve_stored_path,
)
from .runtime.atomic import atomic_write_json
from .version import release_version

DOCUMENT_SCHEMA = "cer-regdocs-scout-document"
SNAPSHOT_SCHEMA = "cer-regdocs-scout-snapshot"
SCHEMA_VERSION = 1

DOCUMENT_FIELDS = (
    "id", "name", "url", "item_kind", "is_file", "filing_date", "submitter",
    "company", "project", "filing_number", "snippet", "metadata", "status",
    "scout_status", "detail_status", "detail_last_attempt_at", "detail_succeeded_at",
    "detail_snapshot_id", "first_seen_at", "last_seen_at", "created_at", "updated_at",
)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json_value(raw: Any, fallback: Any) -> Any:
    if raw in (None, ""):
        return fallback
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(str(raw))
    except (TypeError, json.JSONDecodeError):
        return fallback


def _document_manifest_path(document_id: str) -> Path:
    digest = hashlib.sha256(document_id.encode("utf-8")).hexdigest()
    return SCOUT_DOCUMENT_MANIFEST_DIR / digest[:2] / f"{digest}.json"


def _snapshot_identity(source_kind: str, source_url: str, content_sha256: str) -> str:
    raw = f"{source_kind}\0{source_url}\0{content_sha256}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _snapshot_manifest_path(source_kind: str, source_url: str, content_sha256: str) -> Path:
    digest = _snapshot_identity(source_kind, source_url, content_sha256)
    return SCOUT_SNAPSHOT_MANIFEST_DIR / digest[:2] / f"{digest}.json"


def _verify_raw_snapshot(row: Any, *, db_path: Path) -> tuple[bool, str | None]:
    raw_path = resolve_stored_path(str(row["relative_path"]), legacy_base=db_path.parent)
    try:
        if not raw_path.is_file():
            return False, f"missing raw file: {raw_path}"
        if raw_path.stat().st_size != int(row["compressed_size_bytes"]):
            return False, "compressed size mismatch"
        with gzip.open(raw_path, "rb") as stream:
            payload = stream.read()
        if len(payload) != int(row["size_bytes"]):
            return False, "uncompressed size mismatch"
        if hashlib.sha256(payload).hexdigest() != str(row["content_sha256"]):
            return False, "content SHA-256 mismatch"
        return True, None
    except (OSError, EOFError, ValueError) as exc:
        return False, f"{type(exc).__name__}: {exc}"


def export_scout_manifests(
    db_path: Path = DATABASE_PATH,
    *,
    verify_raw: bool = True,
) -> dict[str, Any]:
    """Export current Scout document and raw-snapshot provenance to disk."""
    db_path = db_path.expanduser().resolve()
    con = open_ledger(db_path, readonly=True)
    try:
        if not table_exists(con, "documents") or not table_exists(con, "raw_snapshots"):
            raise RuntimeError("Scout manifest export requires documents and raw_snapshots tables")

        summary: dict[str, Any] = {
            "database": str(db_path),
            "release_version": release_version(),
            "verify_raw": verify_raw,
            "documents_selected": 0,
            "document_manifests_written": 0,
            "snapshot_rows_selected": 0,
            "snapshot_manifests_written": 0,
            "raw_verified": 0,
            "raw_invalid": 0,
            "errors": [],
        }

        document_rows = con.execute(
            f"SELECT {', '.join(DOCUMENT_FIELDS)} FROM documents ORDER BY id"
        ).fetchall()
        summary["documents_selected"] = len(document_rows)
        for row in document_rows:
            document = {field: row[field] for field in DOCUMENT_FIELDS}
            document["id"] = str(document["id"])
            document["is_file"] = bool(document["is_file"])
            document["metadata"] = _json_value(document.get("metadata"), {})
            payload = {
                "schema": DOCUMENT_SCHEMA,
                "schema_version": SCHEMA_VERSION,
                "document_id": document["id"],
                "document": document,
                "exported_at": utcnow(),
                "release_version": release_version(),
            }
            atomic_write_json(_document_manifest_path(document["id"]), payload)
            summary["document_manifests_written"] += 1

        snapshot_rows = con.execute(
            """
            SELECT id, run_id, document_id, source_kind, source_url, final_url,
                   fetched_at, http_status, content_type, content_sha256, size_bytes,
                   compressed_size_bytes, relative_path, response_headers_json,
                   parser_version
            FROM raw_snapshots
            ORDER BY id
            """
        ).fetchall()
        summary["snapshot_rows_selected"] = len(snapshot_rows)
        for row in snapshot_rows:
            if verify_raw:
                ok, error = _verify_raw_snapshot(row, db_path=db_path)
                if not ok:
                    summary["raw_invalid"] += 1
                    if len(summary["errors"]) < 50:
                        summary["errors"].append({"snapshot_id": int(row["id"]), "error": error})
                    continue
                summary["raw_verified"] += 1

            payload = {
                "schema": SNAPSHOT_SCHEMA,
                "schema_version": SCHEMA_VERSION,
                "original_snapshot_id": int(row["id"]),
                "original_run_id": int(row["run_id"]) if row["run_id"] is not None else None,
                "document_id": str(row["document_id"]) if row["document_id"] is not None else None,
                "source_kind": str(row["source_kind"]),
                "source_url": str(row["source_url"]),
                "final_url": str(row["final_url"]) if row["final_url"] is not None else None,
                "fetched_at": str(row["fetched_at"]),
                "http_status": int(row["http_status"]) if row["http_status"] is not None else None,
                "content_type": str(row["content_type"]) if row["content_type"] is not None else None,
                "content_sha256": str(row["content_sha256"]),
                "size_bytes": int(row["size_bytes"]),
                "compressed_size_bytes": int(row["compressed_size_bytes"]),
                "relative_path": str(row["relative_path"]),
                "response_headers": _json_value(row["response_headers_json"], {}),
                "parser_version": str(row["parser_version"]),
                "exported_at": utcnow(),
                "release_version": release_version(),
            }
            atomic_write_json(
                _snapshot_manifest_path(
                    payload["source_kind"], payload["source_url"], payload["content_sha256"]
                ),
                payload,
            )
            summary["snapshot_manifests_written"] += 1

        summary["ok"] = summary["raw_invalid"] == 0
        summary["finished_at"] = utcnow()
        atomic_write_json(SCOUT_MANIFEST_DIR / "export-summary.json", summary)
        return summary
    finally:
        con.close()


def _iter_json(root: Path) -> Iterable[dict[str, Any]]:
    if not root.is_dir():
        return []
    values: list[dict[str, Any]] = []
    for path in root.rglob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            payload["_manifest_path"] = str(path.resolve())
            values.append(payload)
    return values


def document_manifests() -> Iterable[dict[str, Any]]:
    return (
        item for item in _iter_json(SCOUT_DOCUMENT_MANIFEST_DIR)
        if item.get("schema") == DOCUMENT_SCHEMA and item.get("schema_version") == SCHEMA_VERSION
    )


def snapshot_manifests() -> Iterable[dict[str, Any]]:
    return (
        item for item in _iter_json(SCOUT_SNAPSHOT_MANIFEST_DIR)
        if item.get("schema") == SNAPSHOT_SCHEMA and item.get("schema_version") == SCHEMA_VERSION
    )


def manifest_inventory() -> dict[str, int]:
    return {
        "scout_document_manifests": sum(1 for _ in document_manifests()),
        "scout_snapshot_manifests": sum(1 for _ in snapshot_manifests()),
    }
