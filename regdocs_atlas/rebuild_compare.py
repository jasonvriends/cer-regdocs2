"""Compare an artifact-rebuilt ledger with a reference SQLite ledger."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .db import open_ledger
from .db.connection import table_exists
from .db.safety import integrity_report

CORE_DOCUMENT_FIELDS = (
    "name", "url", "item_kind", "is_file", "filing_date", "submitter",
    "company", "project", "filing_number", "snippet",
)


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if value in (None, ""):
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _count(con: Any, table: str, where: str = "") -> int:
    if not table_exists(con, table):
        return 0
    return int(con.execute(f"SELECT COUNT(*) FROM {table} {where}").fetchone()[0])


def _document_rows(con: Any) -> dict[str, Any]:
    if not table_exists(con, "documents"):
        return {}
    rows = con.execute(
        f"SELECT id, {', '.join(CORE_DOCUMENT_FIELDS)}, metadata FROM documents"
    ).fetchall()
    return {str(row["id"]): row for row in rows}


def _file_identities(con: Any) -> set[tuple[str, str]]:
    if not table_exists(con, "files"):
        return set()
    return {
        (str(row[0]), str(row[1]).lower())
        for row in con.execute(
            "SELECT document_id, sha256 FROM files WHERE is_current=1"
        ).fetchall()
    }


def _snapshot_identities(con: Any) -> set[tuple[str, str, str]]:
    if not table_exists(con, "raw_snapshots"):
        return set()
    return {
        (str(row[0]), str(row[1]), str(row[2]).lower())
        for row in con.execute(
            "SELECT source_kind, source_url, content_sha256 FROM raw_snapshots"
        ).fetchall()
    }


def _analysis_identities(con: Any) -> set[tuple[str, str, str, str]]:
    if not table_exists(con, "analyses"):
        return set()
    return {
        (str(row[0]), str(row[1]).lower(), str(row[2]), str(row[3]))
        for row in con.execute(
            """
            SELECT document_id, file_sha256, analyzer_id, api_version
            FROM analyses
            WHERE status='SUCCEEDED'
            """
        ).fetchall()
    }


def _container_relationships(rows: dict[str, Any]) -> set[tuple[str, str]]:
    relationships: set[tuple[str, str]] = set()
    for document_id, row in rows.items():
        metadata = _json_object(row["metadata"])
        container = _json_object(metadata.get("container"))
        if not container:
            container = _json_object(metadata.get("compound"))
        members = container.get("member_ids") if isinstance(container, dict) else None
        if isinstance(members, list):
            for member_id in members:
                relationships.add((document_id, str(member_id)))
    return relationships


def _set_delta(reference: set[Any], rebuilt: set[Any], *, examples: int = 20) -> dict[str, Any]:
    missing = sorted(reference - rebuilt, key=str)
    extra = sorted(rebuilt - reference, key=str)
    return {
        "reference": len(reference),
        "rebuilt": len(rebuilt),
        "missing": len(missing),
        "extra": len(extra),
        "missing_examples": missing[:examples],
        "extra_examples": extra[:examples],
        "exact": not missing and not extra,
    }


def compare_ledgers(reference_db: Path, rebuilt_db: Path) -> dict[str, Any]:
    reference_db = reference_db.expanduser().resolve()
    rebuilt_db = rebuilt_db.expanduser().resolve()
    if not reference_db.is_file():
        raise FileNotFoundError(reference_db)
    if not rebuilt_db.is_file():
        raise FileNotFoundError(rebuilt_db)

    source = open_ledger(reference_db, readonly=True)
    rebuilt = open_ledger(rebuilt_db, readonly=True)
    try:
        source_docs = _document_rows(source)
        rebuilt_docs = _document_rows(rebuilt)
        document_delta = _set_delta(set(source_docs), set(rebuilt_docs))
        file_delta = _set_delta(_file_identities(source), _file_identities(rebuilt))
        snapshot_delta = _set_delta(_snapshot_identities(source), _snapshot_identities(rebuilt))
        analysis_delta = _set_delta(_analysis_identities(source), _analysis_identities(rebuilt))
        relationship_delta = _set_delta(
            _container_relationships(source_docs),
            _container_relationships(rebuilt_docs),
        )

        common = sorted(set(source_docs) & set(rebuilt_docs))
        field_mismatches: list[dict[str, Any]] = []
        mismatch_count = 0
        for document_id in common:
            left = source_docs[document_id]
            right = rebuilt_docs[document_id]
            fields: dict[str, dict[str, Any]] = {}
            for field in CORE_DOCUMENT_FIELDS:
                left_value = left[field]
                right_value = right[field]
                if left_value != right_value:
                    fields[field] = {"reference": left_value, "rebuilt": right_value}
            if fields:
                mismatch_count += 1
                if len(field_mismatches) < 20:
                    field_mismatches.append({"document_id": document_id, "fields": fields})

        reference_integrity = integrity_report(source)
        rebuilt_integrity = integrity_report(rebuilt)
        source_and_stage3_equivalent = all(
            item["exact"]
            for item in (
                document_delta,
                file_delta,
                snapshot_delta,
                analysis_delta,
                relationship_delta,
            )
        ) and mismatch_count == 0 and bool(rebuilt_integrity["ok"])

        return {
            "reference_database": str(reference_db),
            "rebuilt_database": str(rebuilt_db),
            "counts": {
                "documents": {
                    "reference": _count(source, "documents"),
                    "rebuilt": _count(rebuilt, "documents"),
                },
                "current_files": {
                    "reference": _count(source, "files", "WHERE is_current=1"),
                    "rebuilt": _count(rebuilt, "files", "WHERE is_current=1"),
                },
                "raw_snapshots": {
                    "reference": _count(source, "raw_snapshots"),
                    "rebuilt": _count(rebuilt, "raw_snapshots"),
                },
                "successful_analyses": {
                    "reference": _count(source, "analyses", "WHERE status='SUCCEEDED'"),
                    "rebuilt": _count(rebuilt, "analyses", "WHERE status='SUCCEEDED'"),
                },
                "normalizations": {
                    "reference": _count(source, "normalizations"),
                    "rebuilt": _count(rebuilt, "normalizations"),
                },
            },
            "document_ids": document_delta,
            "current_file_identities": file_delta,
            "raw_snapshot_identities": snapshot_delta,
            "successful_analysis_identities": analysis_delta,
            "container_relationships": relationship_delta,
            "core_document_field_mismatches": {
                "count": mismatch_count,
                "examples": field_mismatches,
                "exact": mismatch_count == 0,
            },
            "reference_integrity": reference_integrity,
            "rebuilt_integrity": rebuilt_integrity,
            "source_and_stage3_equivalent": source_and_stage3_equivalent,
            "normalization_recovery_expected_gap": _count(source, "normalizations") > 0,
        }
    finally:
        source.close()
        rebuilt.close()
