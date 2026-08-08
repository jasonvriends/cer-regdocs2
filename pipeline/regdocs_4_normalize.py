#!/usr/bin/env python3
"""Stage 4: deterministically normalize analysis artifacts into JSONL.

Syntax::

    python pipeline/regdocs_4_normalize.py [options]
    python pipeline/regdocs_4_normalize.py --help

Output contracts, corpus replacement semantics, and examples are documented in
``pipeline/regdocs_4_normalize.md``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from regdocs_paths import (
    ANALYZE_DIR,
    DATABASE_PATH,
    NORMALIZE_DIR,
    resolve_stored_path,
    stored_path as portable_path,
)

SCRIPT_VERSION = "4.1.0"
PARSER_VERSION = "regdocs-normalizer-2026-08-08-v2"
DEFAULT_ANALYZER_ID = "prebuilt-layout"
DEFAULT_API_VERSION = "2025-11-01"
DEFAULT_TARGET_WORDS = 800
DEFAULT_MAX_WORDS = 1200

EXCLUDED_PARAGRAPH_ROLES = {"pageHeader", "pageFooter", "pageNumber"}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def stable_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def pretty_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value in (None, ""):
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def word_count(text: str) -> int:
    return len(re.findall(r"\S+", text or ""))


def natural_document_sort_key(value: str) -> tuple[int, Any, str]:
    s = str(value)
    if s.isdigit():
        return (0, int(s), s)
    return (1, s.casefold(), s)


def atomic_open(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    return partial, partial.open("w", encoding="utf-8", newline="\n")


def finalize_atomic(partial: Path, final: Path) -> None:
    with partial.open("rb") as f:
        os.fsync(f.fileno())
    os.replace(partial, final)


def safe_unlink(path: Optional[Path]) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass


def open_db(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA synchronous = NORMAL")
    return con


def ensure_schema(con: sqlite3.Connection) -> None:
    required = {"documents", "files", "analyses", "runs", "errors"}
    existing = {
        str(r[0])
        for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    missing = required - existing
    if missing:
        raise RuntimeError(f"Database is missing required table(s): {', '.join(sorted(missing))}")

    con.execute(
        """
        CREATE TABLE IF NOT EXISTS normalizations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER,
            document_id TEXT NOT NULL,
            file_id INTEGER NOT NULL,
            file_sha256 TEXT NOT NULL,
            analysis_id INTEGER NOT NULL,
            normalizer_version TEXT NOT NULL,
            config_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            output_sha256 TEXT,
            page_count INTEGER,
            chunk_count INTEGER,
            table_count INTEGER,
            provenance_count INTEGER,
            started_at TEXT,
            finished_at TEXT,
            error_code TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(analysis_id, normalizer_version, config_hash),
            FOREIGN KEY (run_id) REFERENCES runs(id),
            FOREIGN KEY (document_id) REFERENCES documents(id),
            FOREIGN KEY (file_id) REFERENCES files(id),
            FOREIGN KEY (analysis_id) REFERENCES analyses(id)
        )
        """
    )
    con.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_normalizations_document
            ON normalizations(document_id);
        CREATE INDEX IF NOT EXISTS idx_normalizations_status
            ON normalizations(status);
        CREATE INDEX IF NOT EXISTS idx_normalizations_analysis
            ON normalizations(analysis_id);
        """
    )
    con.commit()


def create_run(con: sqlite3.Connection, args: argparse.Namespace, config_hash: str) -> int:
    now = utcnow()
    params = {
        "db": portable_path(args.db),
        "analysis_dir": portable_path(args.analysis_dir),
        "output_dir": portable_path(args.output_dir),
        "analyzer_id": args.analyzer_id,
        "api_version": args.api_version,
        "document_id": args.document_id,
        "limit": args.limit,
        "target_words": args.target_words,
        "max_words": args.max_words,
        "skip_errors": args.skip_errors,
        "config_hash": config_hash,
    }
    cur = con.execute(
        """
        INSERT INTO runs (
            stage, status, started_at, parameters_json, summary_json,
            script_version, parser_version, current_phase, heartbeat_at,
            completed_units, total_units, progress_message,
            logical_requests, http_attempts, successful_requests, failed_requests, retries
        ) VALUES (?, ?, ?, ?, '{}', ?, ?, ?, ?, 0, NULL, ?, 0, 0, 0, 0, 0)
        """,
        (
            "normalize",
            "RUNNING",
            now,
            json.dumps(params, ensure_ascii=False, sort_keys=True),
            SCRIPT_VERSION,
            PARSER_VERSION,
            "selecting_candidates",
            now,
            "Selecting successful Content Understanding analyses",
        ),
    )
    con.commit()
    return int(cur.lastrowid)


def update_run_progress(
    con: sqlite3.Connection,
    run_id: int,
    completed: int,
    total: int,
    succeeded: int,
    failed: int,
    pages: int,
    chunks: int,
    tables: int,
    message: str,
) -> None:
    summary = {
        "documents_total": total,
        "documents_completed": completed,
        "succeeded": succeeded,
        "failed": failed,
        "pages": pages,
        "chunks": chunks,
        "tables": tables,
    }
    con.execute(
        """
        UPDATE runs
        SET heartbeat_at=?, current_phase='normalizing', completed_units=?, total_units=?,
            progress_message=?, summary_json=?
        WHERE id=?
        """,
        (
            utcnow(),
            completed,
            total,
            message,
            json.dumps(summary, ensure_ascii=False, sort_keys=True),
            run_id,
        ),
    )
    con.commit()


def finish_run(
    con: sqlite3.Connection,
    run_id: int,
    status: str,
    total: int,
    succeeded: int,
    failed: int,
    pages: int,
    chunks: int,
    tables: int,
    provenance: int,
    elapsed: float,
    output_hashes: dict[str, str],
) -> None:
    now = utcnow()
    summary = {
        "status": status,
        "documents_total": total,
        "succeeded": succeeded,
        "failed": failed,
        "pages": pages,
        "chunks": chunks,
        "tables": tables,
        "provenance": provenance,
        "elapsed_seconds": round(elapsed, 3),
        "output_sha256": output_hashes,
    }
    con.execute(
        """
        UPDATE runs
        SET status=?, finished_at=?, summary_json=?, current_phase=?, heartbeat_at=?,
            completed_units=?, total_units=?, progress_message=?
        WHERE id=?
        """,
        (
            status,
            now,
            json.dumps(summary, ensure_ascii=False, sort_keys=True),
            status.lower(),
            now,
            total,
            total,
            (
                f"Normalize {status}: {succeeded} succeeded, {failed} failed, "
                f"{pages} pages, {chunks} chunks, {tables} tables"
            ),
            run_id,
        ),
    )
    con.commit()


def record_error(
    con: sqlite3.Connection,
    run_id: int,
    document_id: Optional[str],
    code: str,
    message: str,
    context: Optional[dict[str, Any]] = None,
) -> None:
    con.execute(
        """
        INSERT INTO errors (
            run_id, document_id, stage, code, severity, message,
            retryable, context_json, created_at
        ) VALUES (?, ?, 'normalize', ?, 'ERROR', ?, 0, ?, ?)
        """,
        (
            run_id,
            document_id,
            code,
            message[:4000],
            json.dumps(context or {}, ensure_ascii=False, sort_keys=True),
            utcnow(),
        ),
    )
    con.commit()


def mark_normalization_started(
    con: sqlite3.Connection,
    run_id: int,
    candidate: "Candidate",
    config_hash: str,
) -> None:
    now = utcnow()
    con.execute(
        """
        INSERT INTO normalizations (
            run_id, document_id, file_id, file_sha256, analysis_id,
            normalizer_version, config_hash, status, started_at,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'RUNNING', ?, ?, ?)
        ON CONFLICT(analysis_id, normalizer_version, config_hash) DO UPDATE SET
            run_id=excluded.run_id,
            document_id=excluded.document_id,
            file_id=excluded.file_id,
            file_sha256=excluded.file_sha256,
            status='RUNNING',
            output_sha256=NULL,
            page_count=NULL,
            chunk_count=NULL,
            table_count=NULL,
            provenance_count=NULL,
            started_at=excluded.started_at,
            finished_at=NULL,
            error_code=NULL,
            error_message=NULL,
            updated_at=excluded.updated_at
        """,
        (
            run_id,
            candidate.document_id,
            candidate.file_id,
            candidate.file_sha256,
            candidate.analysis_id,
            SCRIPT_VERSION,
            config_hash,
            now,
            now,
            now,
        ),
    )
    con.commit()


def mark_normalization_finished(
    con: sqlite3.Connection,
    candidate: "Candidate",
    config_hash: str,
    status: str,
    output_sha256: Optional[str],
    page_count: Optional[int],
    chunk_count: Optional[int],
    table_count: Optional[int],
    provenance_count: Optional[int],
    error_code: Optional[str] = None,
    error_message: Optional[str] = None,
) -> None:
    now = utcnow()
    con.execute(
        """
        UPDATE normalizations
        SET status=?, output_sha256=?, page_count=?, chunk_count=?, table_count=?,
            provenance_count=?, finished_at=?, error_code=?, error_message=?, updated_at=?
        WHERE analysis_id=? AND normalizer_version=? AND config_hash=?
        """,
        (
            status,
            output_sha256,
            page_count,
            chunk_count,
            table_count,
            provenance_count,
            now,
            error_code,
            error_message[:4000] if error_message else None,
            now,
            candidate.analysis_id,
            SCRIPT_VERSION,
            config_hash,
        ),
    )
    con.commit()


@dataclass(frozen=True)
class Candidate:
    document_id: str
    title: str
    source_url: str
    item_kind: Optional[str]
    filing_date: Optional[str]
    submitter: Optional[str]
    company: Optional[str]
    project: Optional[str]
    filing_number: Optional[str]
    snippet: Optional[str]
    metadata_json: str
    file_id: int
    file_path: str
    original_filename: Optional[str]
    mime_type: Optional[str]
    extension: Optional[str]
    size_bytes: Optional[int]
    file_sha256: str
    analysis_id: int
    analyzer_id: str
    api_version: str
    raw_json_path: str
    markdown_path: Optional[str]
    db_page_count: Optional[int]
    db_table_count: Optional[int]
    db_section_count: Optional[int]
    db_warning_count: Optional[int]


def select_candidates(
    con: sqlite3.Connection,
    analyzer_id: str,
    api_version: str,
    document_ids: Optional[Sequence[str]],
    limit: Optional[int],
) -> list[Candidate]:
    where = [
        "a.status='SUCCEEDED'",
        "a.analyzer_id=?",
        "a.api_version=?",
        "f.is_current=1",
        "f.sha256=a.file_sha256",
    ]
    params: list[Any] = [analyzer_id, api_version]
    if document_ids:
        placeholders = ",".join("?" for _ in document_ids)
        where.append(f"d.id IN ({placeholders})")
        params.extend(str(x) for x in document_ids)
    sql = f"""
        SELECT
            d.id AS document_id, d.name AS title, d.url AS source_url, d.item_kind,
            d.filing_date, d.submitter, d.company, d.project, d.filing_number,
            d.snippet, d.metadata AS metadata_json, f.id AS file_id,
            f.path AS file_path, f.original_filename, f.mime_type, f.extension,
            f.size_bytes, f.sha256 AS file_sha256, a.id AS analysis_id,
            a.analyzer_id, a.api_version, a.raw_json_path, a.markdown_path,
            a.page_count AS db_page_count, a.table_count AS db_table_count,
            a.section_count AS db_section_count, a.warning_count AS db_warning_count
        FROM analyses a
        JOIN files f ON f.id=a.file_id
        JOIN documents d ON d.id=a.document_id
        WHERE {' AND '.join(where)}
    """
    rows = con.execute(sql, params).fetchall()
    rows = sorted(rows, key=lambda r: natural_document_sort_key(str(r["document_id"])))
    if limit is not None:
        rows = rows[:limit]
    return [Candidate(**dict(r)) for r in rows]


def _tail_after_component(path: Path, component: str) -> Optional[Path]:
    parts = list(path.parts)
    positions = [i for i, p in enumerate(parts) if p == component]
    if not positions:
        return None
    idx = positions[-1]
    if idx + 1 >= len(parts):
        return Path()
    return Path(*parts[idx + 1 :])


def resolve_artifact(
    stored_path: Optional[str],
    analysis_dir: Path,
    kind: str,
    candidate: Candidate,
    extension: str,
) -> Optional[Path]:
    possibilities: list[Path] = []
    if stored_path:
        p = Path(stored_path).expanduser()
        possibilities.append(resolve_stored_path(p))
        possibilities.append(p)
        tail = _tail_after_component(p, "analysis")
        if tail is not None:
            possibilities.append(analysis_dir / tail)
        workspace_tail = _tail_after_component(p, "3_analyze")
        if workspace_tail is not None:
            possibilities.append(analysis_dir / workspace_tail)
    possibilities.append(
        analysis_dir / "content-understanding" / kind / candidate.analyzer_id /
        candidate.api_version / candidate.document_id / f"{candidate.file_sha256}.{extension}"
    )
    seen: set[str] = set()
    for p in possibilities:
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        if p.is_file():
            return p
    return None


_SOURCE_REGION_RE = re.compile(r"D\((\d+),([^)]*)\)")
_POINTER_RE = re.compile(r"^/(paragraphs|sections|tables|figures|hyperlinks)/(\d+)$")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\[])")


def parse_source_regions(source: Optional[str]) -> list[dict[str, Any]]:
    if not source:
        return []
    regions: list[dict[str, Any]] = []
    for match in _SOURCE_REGION_RE.finditer(source):
        page = int(match.group(1))
        raw_numbers = [x.strip() for x in match.group(2).split(",") if x.strip()]
        coords: list[float] = []
        valid = True
        for raw in raw_numbers:
            try:
                coords.append(float(raw))
            except ValueError:
                valid = False
                break
        item: dict[str, Any] = {"page": page}
        if valid and coords:
            item["polygon"] = coords
        item["source"] = match.group(0)
        regions.append(item)
    return regions


def pages_from_regions(regions: Sequence[dict[str, Any]]) -> list[int]:
    return sorted({int(r["page"]) for r in regions if r.get("page") is not None})


def page_range_from_regions(regions: Sequence[dict[str, Any]]) -> tuple[Optional[int], Optional[int]]:
    pages = pages_from_regions(regions)
    return (pages[0], pages[-1]) if pages else (None, None)


def element_pointer(pointer: str) -> tuple[Optional[str], Optional[int]]:
    m = _POINTER_RE.match(pointer or "")
    if not m:
        return None, None
    return m.group(1), int(m.group(2))


def merge_regions(region_lists: Iterable[Sequence[dict[str, Any]]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for regions in region_lists:
        for region in regions:
            key = stable_json(region)
            if key not in seen:
                seen.add(key)
                merged.append(region)
    merged.sort(key=lambda r: (int(r.get("page", 0)), stable_json(r)))
    return merged


def span_bounds(span: Optional[dict[str, Any]]) -> tuple[Optional[int], Optional[int]]:
    if not isinstance(span, dict):
        return None, None
    try:
        offset = int(span.get("offset"))
        length = int(span.get("length"))
    except (TypeError, ValueError):
        return None, None
    return offset, offset + length


def span_intersects(a: Optional[dict[str, Any]], b: Optional[dict[str, Any]]) -> bool:
    a0, a1 = span_bounds(a)
    b0, b1 = span_bounds(b)
    if None in (a0, a1, b0, b1):
        return False
    return bool(a0 < b1 and b0 < a1)


def list_of_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if item is None:
            continue
        if isinstance(item, (str, int, float)):
            text = str(item).strip()
            if text:
                out.append(text)
    return out


def clean_identifiers(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, list[str]] = {}
    for key in sorted(value):
        vals = value[key]
        if isinstance(vals, list):
            cleaned = list_of_strings(vals)
        elif vals is None:
            cleaned = []
        else:
            cleaned = [str(vals).strip()] if str(vals).strip() else []
        if cleaned:
            out[str(key)] = cleaned
    return out


def clean_container_memberships(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    keep_keys = (
        "container_id", "container_kind", "container_title",
        "filing_number", "membership_source",
    )
    out: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        cleaned = {k: item.get(k) for k in keep_keys if item.get(k) not in (None, "")}
        if cleaned:
            out.append(cleaned)
    out.sort(key=lambda x: (str(x.get("container_id", "")), str(x.get("container_title", ""))))
    return out


def logical_artifact_path(kind: str, candidate: Candidate, extension: str) -> str:
    return portable_path(
        ANALYZE_DIR / "content-understanding" / kind / candidate.analyzer_id /
        candidate.api_version / candidate.document_id / f"{candidate.file_sha256}.{extension}"
    ).replace("\\", "/")


def metadata_projection(candidate: Candidate) -> dict[str, Any]:
    meta = parse_json_object(candidate.metadata_json)
    facets_obj = meta.get("facets") if isinstance(meta.get("facets"), dict) else {}
    generic_facets: dict[str, list[str]] = {}
    for key in sorted(facets_obj):
        vals = list_of_strings(facets_obj.get(key))
        if vals:
            generic_facets[str(key)] = vals
    detail_fields = meta.get("regdocs_detail_fields")
    if not isinstance(detail_fields, dict):
        detail_fields = {}
    return {
        "application_types": list_of_strings(meta.get("application_types")),
        "commodities": list_of_strings(meta.get("commodities")),
        "document_types": list_of_strings(meta.get("document_types")),
        "file_types": list_of_strings(meta.get("file_types")),
        "roles": list_of_strings(meta.get("roles")),
        "facets": generic_facets,
        "identifiers": clean_identifiers(meta.get("identifiers")),
        "container_memberships": clean_container_memberships(meta.get("container_memberships")),
        "filing_id": meta.get("filing_id"),
        "company_id": meta.get("company_id"),
        "resolved_url": meta.get("resolved_url"),
        "language": (
            (detail_fields.get("dcterms.language") or [None])[0]
            if isinstance(detail_fields.get("dcterms.language"), list)
            else detail_fields.get("dcterms.language")
        ),
    }


def inherited_chunk_metadata(candidate: Candidate, projected: dict[str, Any]) -> dict[str, Any]:
    return {
        "document_id": candidate.document_id,
        "title": candidate.title,
        "filing_date": candidate.filing_date,
        "submitter": candidate.submitter,
        "company": candidate.company,
        "project": candidate.project,
        "filing_number": candidate.filing_number,
        "filing_id": projected.get("filing_id"),
        "application_types": projected.get("application_types", []),
        "commodities": projected.get("commodities", []),
        "document_types": projected.get("document_types", []),
        "file_types": projected.get("file_types", []),
        "roles": projected.get("roles", []),
        "identifiers": projected.get("identifiers", {}),
        "source_url": candidate.source_url,
        "resolved_url": projected.get("resolved_url"),
        "file_path": candidate.file_path,
        "file_sha256": candidate.file_sha256,
        "analyzer_id": candidate.analyzer_id,
        "api_version": candidate.api_version,
        "normalizer_version": SCRIPT_VERSION,
    }


@dataclass
class TextUnit:
    text: str
    evidence: list[dict[str, Any]]
    regions: list[dict[str, Any]]
    span: Optional[dict[str, Any]]


def paragraph_evidence(index: int, paragraph: dict[str, Any], fragment_index: Optional[int] = None) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "element": f"/paragraphs/{index}",
        "element_type": "paragraph",
        "element_index": index,
        "span": paragraph.get("span"),
        "source": paragraph.get("source"),
        "regions": parse_source_regions(paragraph.get("source")),
    }
    if fragment_index is not None:
        evidence["fragment_index"] = fragment_index
    return evidence


def qualify_evidence(evidence: dict[str, Any], content_index: int) -> dict[str, Any]:
    """Make a local Azure element pointer unambiguous across contents[]."""
    item = dict(evidence)
    local_element = item.get("element")
    item["content_index"] = content_index
    if isinstance(local_element, str) and local_element.startswith("/"):
        item["local_element"] = local_element
        item["element"] = f"/contents/{content_index}{local_element}"
    children = item.get("child_elements")
    if isinstance(children, list):
        item["child_elements"] = [
            qualify_evidence(child, content_index) if isinstance(child, dict) else child
            for child in children
        ]
    return item


def split_oversized_text_unit(unit: TextUnit, target_words: int, max_words: int) -> list[TextUnit]:
    if word_count(unit.text) <= max_words:
        return [unit]
    sentences = [x.strip() for x in _SENTENCE_SPLIT_RE.split(unit.text.strip()) if x.strip()]
    if len(sentences) <= 1:
        words = unit.text.split()
        parts = [" ".join(words[i : i + max_words]) for i in range(0, len(words), max_words)]
    else:
        parts: list[str] = []
        current: list[str] = []
        current_words = 0
        for sentence in sentences:
            sw = word_count(sentence)
            if sw > max_words:
                if current:
                    parts.append(" ".join(current))
                    current, current_words = [], 0
                words = sentence.split()
                parts.extend(" ".join(words[i : i + max_words]) for i in range(0, len(words), max_words))
                continue
            if current and current_words + sw > max_words:
                parts.append(" ".join(current))
                current, current_words = [], 0
            current.append(sentence)
            current_words += sw
            if current_words >= target_words:
                parts.append(" ".join(current))
                current, current_words = [], 0
        if current:
            parts.append(" ".join(current))
    out: list[TextUnit] = []
    for i, text in enumerate(parts, start=1):
        ev = [dict(x) for x in unit.evidence]
        for item in ev:
            item["fragment_index"] = i
        out.append(TextUnit(text=text, evidence=ev, regions=list(unit.regions), span=unit.span))
    return out


def group_text_units(units: Sequence[TextUnit], target_words: int, max_words: int) -> list[list[TextUnit]]:
    expanded: list[TextUnit] = []
    for unit in units:
        expanded.extend(split_oversized_text_unit(unit, target_words, max_words))
    groups: list[list[TextUnit]] = []
    current: list[TextUnit] = []
    current_words = 0
    for unit in expanded:
        uw = word_count(unit.text)
        if current and current_words + uw > max_words:
            groups.append(current)
            current, current_words = [], 0
        current.append(unit)
        current_words += uw
        if current_words >= target_words:
            groups.append(current)
            current, current_words = [], 0
    if current:
        groups.append(current)
    return groups


def table_rows(table: dict[str, Any]) -> tuple[list[str], list[int]]:
    rows = int(table.get("rowCount") or 0)
    cols = int(table.get("columnCount") or 0)
    matrix = [["" for _ in range(cols)] for _ in range(rows)]
    header_rows: set[int] = set()
    for cell in table.get("cells") or []:
        try:
            r = int(cell.get("rowIndex") or 0)
            col = int(cell.get("columnIndex") or 0)
        except (TypeError, ValueError):
            continue
        if 0 <= r < rows and 0 <= col < cols:
            matrix[r][col] = str(cell.get("content") or "").strip()
        if cell.get("kind") in {"columnHeader", "stubHead"}:
            header_rows.add(r)
    return ["\t".join(row).rstrip() for row in matrix], sorted(header_rows)


def split_table_for_search(table: dict[str, Any], target_words: int, max_words: int) -> list[dict[str, Any]]:
    lines, header_rows = table_rows(table)
    if not lines:
        return []
    header_lines = [lines[i] for i in header_rows if 0 <= i < len(lines) and lines[i].strip()]
    header_prefix = "\n".join(header_lines)
    groups: list[dict[str, Any]] = []
    current: list[tuple[int, str]] = []
    current_words = 0
    for row_idx, line in enumerate(lines):
        if not line.strip():
            continue
        lw = word_count(line)
        prefix_words = word_count(header_prefix) if groups and header_prefix else 0
        if current and current_words + lw + prefix_words > max_words:
            groups.append({"rows": current})
            current, current_words = [], 0
        current.append((row_idx, line))
        current_words += lw
        if current_words >= target_words:
            groups.append({"rows": current})
            current, current_words = [], 0
    if current:
        groups.append({"rows": current})
    for i, group in enumerate(groups):
        rows_here = group["rows"]
        body = "\n".join(line for _, line in rows_here)
        if i > 0 and header_prefix:
            body = header_prefix + "\n" + body
        group["text"] = body
        group["row_start"] = min(r for r, _ in rows_here)
        group["row_end"] = max(r for r, _ in rows_here)
        del group["rows"]
    return groups


def section_maps(content: dict[str, Any]) -> tuple[dict[int, Optional[int]], dict[int, str], dict[int, list[str]]]:
    sections = content.get("sections") or []
    paragraphs = content.get("paragraphs") or []
    parent: dict[int, Optional[int]] = {i: None for i in range(len(sections))}
    headings: dict[int, str] = {}
    for parent_idx, section in enumerate(sections):
        for pointer in section.get("elements") or []:
            typ, idx = element_pointer(pointer)
            if typ == "sections" and idx is not None and idx in parent:
                parent[idx] = parent_idx
        heading = ""
        for pointer in section.get("elements") or []:
            typ, idx = element_pointer(pointer)
            if typ != "paragraphs" or idx is None or idx >= len(paragraphs):
                continue
            p = paragraphs[idx]
            if p.get("role") == "sectionHeading":
                heading = str(p.get("content") or "").strip()
                break
        headings[parent_idx] = heading
    cache: dict[int, list[str]] = {}
    def path_for(idx: int) -> list[str]:
        if idx in cache:
            return cache[idx]
        chain: list[int] = []
        seen: set[int] = set()
        cur: Optional[int] = idx
        while cur is not None and cur not in seen:
            seen.add(cur)
            chain.append(cur)
            cur = parent.get(cur)
        chain.reverse()
        path = [headings[x] for x in chain if headings.get(x)]
        cache[idx] = path
        return path
    for i in range(len(sections)):
        path_for(i)
    return parent, headings, cache


def containing_section_map(content: dict[str, Any], element_type: str) -> dict[int, int]:
    result: dict[int, int] = {}
    for section_idx, section in enumerate(content.get("sections") or []):
        for pointer in section.get("elements") or []:
            typ, idx = element_pointer(pointer)
            if typ == element_type and idx is not None and idx not in result:
                result[idx] = section_idx
    return result


def make_text_chunk(
    chunk_id: str,
    chunk_index: int,
    candidate: Candidate,
    projected: dict[str, Any],
    section_path: list[str],
    units: Sequence[TextUnit],
    chunk_type: str = "text",
    extra: Optional[dict[str, Any]] = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not extra or "content_index" not in extra:
        raise ValueError(f"Chunk {chunk_id} is missing content_index provenance")
    content_index = int(extra["content_index"])
    content_text = "\n\n".join(u.text.strip() for u in units if u.text.strip()).strip()
    regions = merge_regions(u.regions for u in units)
    page_start, page_end = page_range_from_regions(regions)
    evidence = [ev for u in units for ev in u.evidence]
    record = inherited_chunk_metadata(candidate, projected)
    record.update(
        {
            "id": chunk_id,
            "chunk_index": chunk_index,
            "chunk_type": chunk_type,
            "heading": section_path[-1] if section_path else None,
            "section_path": section_path,
            "page_start": page_start,
            "page_end": page_end,
            "word_count": word_count(content_text),
            "content": content_text,
        }
    )
    record.update(extra)
    provenance = {
        "chunk_id": chunk_id,
        "document_id": candidate.document_id,
        "file_sha256": candidate.file_sha256,
        "content_index": content_index,
        "page_start": page_start,
        "page_end": page_end,
        "regions": regions,
        "elements": evidence,
    }
    return record, provenance


def normalize_table_record(
    candidate: Candidate,
    projected: dict[str, Any],
    table_index: int,
    table: dict[str, Any],
    section_path: list[str],
) -> dict[str, Any]:
    table_id = f"{candidate.document_id}:table:{table_index + 1:04d}"
    regions = parse_source_regions(table.get("source"))
    page_start, page_end = page_range_from_regions(regions)
    lines, _ = table_rows(table)
    caption = table.get("caption") or {}
    cells_out: list[dict[str, Any]] = []
    for cell in table.get("cells") or []:
        cells_out.append(
            {
                "row_index": cell.get("rowIndex"),
                "column_index": cell.get("columnIndex"),
                "row_span": cell.get("rowSpan", 1),
                "column_span": cell.get("columnSpan", 1),
                "kind": cell.get("kind"),
                "content": cell.get("content") or "",
                "span": cell.get("span"),
                "source": cell.get("source"),
                "regions": parse_source_regions(cell.get("source")),
            }
        )
    return {
        "id": table_id,
        "document_id": candidate.document_id,
        "table_index": table_index,
        "caption": caption.get("content") if isinstance(caption, dict) else None,
        "section_path": section_path,
        "page_start": page_start,
        "page_end": page_end,
        "row_count": table.get("rowCount"),
        "column_count": table.get("columnCount"),
        "readable_text": "\n".join(lines).strip(),
        "span": table.get("span"),
        "source": table.get("source"),
        "regions": regions,
        "cells": cells_out,
        "source_url": candidate.source_url,
        "resolved_url": projected.get("resolved_url"),
        "file_sha256": candidate.file_sha256,
        "analyzer_id": candidate.analyzer_id,
        "api_version": candidate.api_version,
        "normalizer_version": SCRIPT_VERSION,
    }


@dataclass
class NormalizedDocument:
    document: dict[str, Any]
    pages: list[dict[str, Any]]
    chunks: list[dict[str, Any]]
    tables: list[dict[str, Any]]
    provenance: list[dict[str, Any]]
    output_sha256: str


def load_and_validate_artifacts(
    candidate: Candidate,
    analysis_dir: Path,
) -> tuple[dict[str, Any], str, Path, Optional[Path], list[str]]:
    raw_path = resolve_artifact(candidate.raw_json_path, analysis_dir, "raw", candidate, "json")
    if raw_path is None:
        raise FileNotFoundError(
            f"Raw Content Understanding JSON not found for document {candidate.document_id}. "
            f"Stored path: {candidate.raw_json_path}"
        )
    with raw_path.open("r", encoding="utf-8") as f:
        result = json.load(f)
    if not isinstance(result, dict):
        raise ValueError("Content Understanding result is not a JSON object")
    top_analyzer = result.get("analyzerId")
    top_api = result.get("apiVersion")
    if top_analyzer != candidate.analyzer_id:
        raise ValueError(f"Analyzer mismatch: DB={candidate.analyzer_id!r}, JSON={top_analyzer!r}")
    if top_api != candidate.api_version:
        raise ValueError(f"API version mismatch: DB={candidate.api_version!r}, JSON={top_api!r}")
    contents = result.get("contents")
    if not isinstance(contents, list) or not contents:
        raise ValueError("Content Understanding result has no contents[]")
    json_markdowns: list[str] = []
    for i, content in enumerate(contents):
        if not isinstance(content, dict):
            raise ValueError(f"contents[{i}] is not an object")
        markdown = content.get("markdown")
        if not isinstance(markdown, str):
            raise ValueError(f"contents[{i}].markdown is missing or not a string")
        json_markdowns.append(markdown)
    markdown_from_json = "\n\n".join(json_markdowns)
    md_path = resolve_artifact(candidate.markdown_path, analysis_dir, "markdown", candidate, "md")
    warnings: list[str] = []
    if md_path is not None:
        markdown_file = md_path.read_text(encoding="utf-8")
        if markdown_file != markdown_from_json:
            raise ValueError(f"Markdown artifact differs from markdown embedded in raw JSON: {md_path}")
        markdown = markdown_file
    else:
        markdown = markdown_from_json
        warnings.append("markdown_file_missing_used_json_markdown")
    actual_pages = sum(len(x.get("pages") or []) for x in contents)
    actual_tables = sum(len(x.get("tables") or []) for x in contents)
    actual_sections = sum(len(x.get("sections") or []) for x in contents)
    if candidate.db_page_count is not None and actual_pages != int(candidate.db_page_count):
        warnings.append(f"db_page_count={candidate.db_page_count};json_page_count={actual_pages}")
    if candidate.db_table_count is not None and actual_tables != int(candidate.db_table_count):
        warnings.append(f"db_table_count={candidate.db_table_count};json_table_count={actual_tables}")
    if candidate.db_section_count is not None and actual_sections != int(candidate.db_section_count):
        warnings.append(f"db_section_count={candidate.db_section_count};json_section_count={actual_sections}")
    return result, markdown, raw_path, md_path, warnings


def build_pages(candidate: Candidate, projected: dict[str, Any], result: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for content_index, content in enumerate(result.get("contents") or []):
        markdown = content.get("markdown") or ""
        paragraphs = content.get("paragraphs") or []
        para_by_page: dict[int, list[tuple[int, dict[str, Any]]]] = {}
        for p_idx, p in enumerate(paragraphs):
            regions = parse_source_regions(p.get("source"))
            for page in pages_from_regions(regions):
                para_by_page.setdefault(page, []).append((p_idx, p))
        for page in content.get("pages") or []:
            page_number = int(page.get("pageNumber"))
            spans = page.get("spans") or []
            page_markdown_parts: list[str] = []
            for span in spans:
                start, end = span_bounds(span)
                if start is None or end is None:
                    continue
                page_markdown_parts.append(markdown[start:end])
            body: list[tuple[int, str]] = []
            headers: list[str] = []
            footers: list[str] = []
            printed_labels: list[str] = []
            for p_idx, p in sorted(
                para_by_page.get(page_number, []),
                key=lambda x: int((x[1].get("span") or {}).get("offset") or 0),
            ):
                text = str(p.get("content") or "").strip()
                if not text:
                    continue
                role = p.get("role")
                if role == "pageHeader":
                    headers.append(text)
                elif role == "pageFooter":
                    footers.append(text)
                elif role == "pageNumber":
                    printed_labels.append(text)
                else:
                    body.append((p_idx, text))
            records.append(
                {
                    "id": f"{candidate.document_id}:page:{page_number:04d}",
                    "document_id": candidate.document_id,
                    "content_index": content_index,
                    "page_number": page_number,
                    "printed_page_labels": printed_labels,
                    "width": page.get("width"),
                    "height": page.get("height"),
                    "unit": content.get("unit"),
                    "angle": page.get("angle"),
                    "headers": headers,
                    "footers": footers,
                    "content": "\n\n".join(text for _, text in body),
                    "markdown": "".join(page_markdown_parts),
                    "span": spans,
                    "source_url": candidate.source_url,
                    "resolved_url": projected.get("resolved_url"),
                    "file_path": candidate.file_path,
                    "file_sha256": candidate.file_sha256,
                    "analyzer_id": candidate.analyzer_id,
                    "api_version": candidate.api_version,
                    "normalizer_version": SCRIPT_VERSION,
                }
            )
    records.sort(key=lambda x: (int(x.get("page_number") or 0), int(x.get("content_index") or 0)))
    return records


def build_chunks_tables_provenance(
    candidate: Candidate,
    projected: dict[str, Any],
    result: dict[str, Any],
    target_words: int,
    max_words: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    chunks: list[dict[str, Any]] = []
    tables_out: list[dict[str, Any]] = []
    provenance: list[dict[str, Any]] = []
    chunk_counter = 0
    global_table_offset = 0
    global_figure_offset = 0
    for content_index, content in enumerate(result.get("contents") or []):
        paragraphs = content.get("paragraphs") or []
        sections = content.get("sections") or []
        tables = content.get("tables") or []
        figures = content.get("figures") or []
        _, _, paths = section_maps(content)
        table_section = containing_section_map(content, "tables")
        figure_section = containing_section_map(content, "figures")
        for table_idx, table in enumerate(tables):
            section_idx = table_section.get(table_idx)
            section_path = paths.get(section_idx, []) if section_idx is not None else []
            record = normalize_table_record(
                candidate, projected, global_table_offset + table_idx, table, section_path
            )
            record["content_index"] = content_index
            record["content_table_index"] = table_idx
            tables_out.append(record)
        visited_sections: set[int] = set()

        def emit_text_units(section_path: list[str], units: list[TextUnit]) -> None:
            nonlocal chunk_counter
            if not units:
                return
            for group in group_text_units(units, target_words, max_words):
                chunk_counter += 1
                chunk_id = f"{candidate.document_id}:chunk:{chunk_counter:04d}"
                chunk, prov = make_text_chunk(
                    chunk_id, chunk_counter, candidate, projected, section_path,
                    group, "text", {"content_index": content_index},
                )
                if chunk["content"]:
                    chunks.append(chunk)
                    provenance.append(prov)

        def emit_table(table_idx: int, section_path: list[str]) -> None:
            nonlocal chunk_counter
            table = tables[table_idx]
            table_id = f"{candidate.document_id}:table:{global_table_offset + table_idx + 1:04d}"
            caption_obj = table.get("caption") or {}
            caption = caption_obj.get("content") if isinstance(caption_obj, dict) else None
            regions = parse_source_regions(table.get("source"))
            evidence = qualify_evidence(
                {
                    "element": f"/tables/{table_idx}",
                    "element_type": "table",
                    "element_index": table_idx,
                    "span": table.get("span"),
                    "source": table.get("source"),
                    "regions": regions,
                },
                content_index,
            )
            for group_index, group in enumerate(split_table_for_search(table, target_words, max_words), start=1):
                text = group["text"]
                if caption:
                    text = f"{caption}\n{text}".strip()
                unit = TextUnit(text=text, evidence=[evidence], regions=regions, span=table.get("span"))
                chunk_counter += 1
                chunk_id = f"{candidate.document_id}:chunk:{chunk_counter:04d}"
                chunk, prov = make_text_chunk(
                    chunk_id, chunk_counter, candidate, projected, section_path, [unit], "table",
                    {
                        "content_index": content_index,
                        "table_id": table_id,
                        "table_part": group_index,
                        "table_row_start": group["row_start"],
                        "table_row_end": group["row_end"],
                    },
                )
                chunks.append(chunk)
                provenance.append(prov)

        def emit_figure(figure_idx: int, section_path: list[str]) -> None:
            nonlocal chunk_counter
            figure = figures[figure_idx]
            if figure.get("role") in {"pageHeader", "pageFooter"}:
                return
            texts: list[str] = []
            evidence_elements: list[dict[str, Any]] = []
            for pointer in figure.get("elements") or []:
                typ, idx = element_pointer(pointer)
                if typ != "paragraphs" or idx is None or idx >= len(paragraphs):
                    continue
                p = paragraphs[idx]
                if p.get("role") in EXCLUDED_PARAGRAPH_ROLES:
                    continue
                text = str(p.get("content") or "").strip()
                if text:
                    texts.append(text)
                    evidence_elements.append(paragraph_evidence(idx, p))
            figure_text = "\n".join(texts).strip()
            if word_count(figure_text) < 3:
                return
            regions = parse_source_regions(figure.get("source"))
            evidence = qualify_evidence(
                {
                    "element": f"/figures/{figure_idx}",
                    "element_type": "figure",
                    "element_index": figure_idx,
                    "figure_id": figure.get("id"),
                    "span": figure.get("span"),
                    "source": figure.get("source"),
                    "regions": regions,
                    "child_elements": evidence_elements,
                },
                content_index,
            )
            units = [TextUnit(figure_text, [evidence], regions, figure.get("span"))]
            for group in group_text_units(units, target_words, max_words):
                chunk_counter += 1
                chunk_id = f"{candidate.document_id}:chunk:{chunk_counter:04d}"
                chunk, prov = make_text_chunk(
                    chunk_id, chunk_counter, candidate, projected, section_path, group, "figure",
                    {
                        "content_index": content_index,
                        "figure_id": f"{candidate.document_id}:figure:{global_figure_offset + figure_idx + 1:04d}",
                        "azure_figure_id": figure.get("id"),
                    },
                )
                chunks.append(chunk)
                provenance.append(prov)

        def walk_section(section_idx: int) -> None:
            if section_idx in visited_sections or section_idx < 0 or section_idx >= len(sections):
                return
            visited_sections.add(section_idx)
            section = sections[section_idx]
            path = paths.get(section_idx, [])
            pending: list[TextUnit] = []
            def flush() -> None:
                nonlocal pending
                if pending:
                    emit_text_units(path, pending)
                    pending = []
            for pointer in section.get("elements") or []:
                typ, idx = element_pointer(pointer)
                if typ == "paragraphs" and idx is not None and idx < len(paragraphs):
                    p = paragraphs[idx]
                    role = p.get("role")
                    if role == "sectionHeading" or role in EXCLUDED_PARAGRAPH_ROLES:
                        continue
                    text = str(p.get("content") or "").strip()
                    if not text:
                        continue
                    regions = parse_source_regions(p.get("source"))
                    pending.append(
                        TextUnit(
                            text=text,
                            evidence=[qualify_evidence(paragraph_evidence(idx, p), content_index)],
                            regions=regions,
                            span=p.get("span"),
                        )
                    )
                elif typ == "sections" and idx is not None:
                    flush()
                    walk_section(idx)
                elif typ == "tables" and idx is not None and idx < len(tables):
                    flush()
                    emit_table(idx, path)
                elif typ == "figures" and idx is not None and idx < len(figures):
                    flush()
                    emit_figure(idx, path)
            flush()

        parent_map, _, _ = section_maps(content)
        roots = [i for i in range(len(sections)) if parent_map.get(i) is None]
        roots.sort(key=lambda i: int((sections[i].get("span") or {}).get("offset") or 0))
        for root in roots:
            walk_section(root)
        orphans = [i for i in range(len(sections)) if i not in visited_sections]
        orphans.sort(key=lambda i: int((sections[i].get("span") or {}).get("offset") or 0))
        for orphan in orphans:
            walk_section(orphan)
        emitted_table_ids = {x.get("table_id") for x in chunks if x.get("chunk_type") == "table"}
        for table_idx in range(len(tables)):
            table_id = f"{candidate.document_id}:table:{global_table_offset + table_idx + 1:04d}"
            if table_id not in emitted_table_ids:
                section_idx = table_section.get(table_idx)
                emit_table(table_idx, paths.get(section_idx, []) if section_idx is not None else [])
        emitted_figure_ids = {x.get("figure_id") for x in chunks if x.get("chunk_type") == "figure"}
        for figure_idx in range(len(figures)):
            figure_id = f"{candidate.document_id}:figure:{global_figure_offset + figure_idx + 1:04d}"
            if figure_id not in emitted_figure_ids:
                section_idx = figure_section.get(figure_idx)
                emit_figure(figure_idx, paths.get(section_idx, []) if section_idx is not None else [])
        global_table_offset += len(tables)
        global_figure_offset += len(figures)
    return chunks, tables_out, provenance


def normalize_document(
    candidate: Candidate,
    analysis_dir: Path,
    target_words: int,
    max_words: int,
    config_hash: str,
) -> NormalizedDocument:
    result, markdown, raw_path, md_path, warnings = load_and_validate_artifacts(candidate, analysis_dir)
    projected = metadata_projection(candidate)
    contents = result.get("contents") or []
    json_page_count = sum(len(x.get("pages") or []) for x in contents)
    json_table_count = sum(len(x.get("tables") or []) for x in contents)
    json_section_count = sum(len(x.get("sections") or []) for x in contents)
    json_figure_count = sum(len(x.get("figures") or []) for x in contents)
    document_record = {
        "document_id": candidate.document_id,
        "title": candidate.title,
        "source_url": candidate.source_url,
        "resolved_url": projected.get("resolved_url"),
        "item_kind": candidate.item_kind,
        "filing_date": candidate.filing_date,
        "submitter": candidate.submitter,
        "company": candidate.company,
        "company_id": projected.get("company_id"),
        "project": candidate.project,
        "filing_number": candidate.filing_number,
        "filing_id": projected.get("filing_id"),
        "snippet": candidate.snippet,
        "application_types": projected.get("application_types", []),
        "commodities": projected.get("commodities", []),
        "document_types": projected.get("document_types", []),
        "file_types": projected.get("file_types", []),
        "roles": projected.get("roles", []),
        "facets": projected.get("facets", {}),
        "identifiers": projected.get("identifiers", {}),
        "container_memberships": projected.get("container_memberships", []),
        "language": projected.get("language"),
        "file_id": candidate.file_id,
        "file_path": candidate.file_path,
        "original_filename": candidate.original_filename,
        "mime_type": candidate.mime_type,
        "extension": candidate.extension,
        "size_bytes": candidate.size_bytes,
        "file_sha256": candidate.file_sha256,
        "analysis_id": candidate.analysis_id,
        "analyzer_id": candidate.analyzer_id,
        "api_version": candidate.api_version,
        "page_count": json_page_count,
        "table_count": json_table_count,
        "section_count": json_section_count,
        "figure_count": json_figure_count,
        "warning_count": len(result.get("warnings") or []),
        "analysis_warnings": result.get("warnings") or [],
        "normalization_warnings": warnings,
        "raw_json_path": logical_artifact_path("raw", candidate, "json"),
        "markdown_path": logical_artifact_path("markdown", candidate, "md") if md_path else None,
        "markdown_source": "artifact" if md_path else "raw_json",
        "markdown_sha256": sha256_text(markdown),
        "normalizer_version": SCRIPT_VERSION,
        "normalizer_parser_version": PARSER_VERSION,
        "normalizer_config_hash": config_hash,
    }
    pages = build_pages(candidate, projected, result)
    chunks, tables, provenance = build_chunks_tables_provenance(
        candidate, projected, result, target_words, max_words
    )
    h = hashlib.sha256()
    for record in [document_record, *pages, *chunks, *tables, *provenance]:
        h.update(stable_json(record).encode("utf-8"))
        h.update(b"\n")
    return NormalizedDocument(
        document=document_record,
        pages=pages,
        chunks=chunks,
        tables=tables,
        provenance=provenance,
        output_sha256=h.hexdigest(),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="REGDOCS Stage 4: local deterministic normalization for search and provenance",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--db", default=portable_path(DATABASE_PATH), help="REGDOCS SQLite ledger")
    parser.add_argument("--analysis-dir", default=portable_path(ANALYZE_DIR), help="Stage 3 analysis root")
    parser.add_argument("--output-dir", default=portable_path(NORMALIZE_DIR), help="Normalized JSONL output directory")
    parser.add_argument("--analyzer-id", default=DEFAULT_ANALYZER_ID)
    parser.add_argument("--api-version", default=DEFAULT_API_VERSION)
    parser.add_argument("--document-id", action="append", help="Normalize only this document ID; may be supplied more than once")
    parser.add_argument("--limit", type=int, help="Normalize at most N selected documents")
    parser.add_argument("--target-words", type=int, default=DEFAULT_TARGET_WORDS, help="Preferred search chunk size in words")
    parser.add_argument("--max-words", type=int, default=DEFAULT_MAX_WORDS, help="Maximum search chunk size before structural splitting")
    parser.add_argument("--skip-errors", action="store_true", help="Continue and emit a partial corpus if a document fails")
    parser.add_argument("--dry-run", action="store_true", help="Select and resolve candidates without writing JSONL or ledger state")
    parser.add_argument("--status", action="store_true", help="Show latest normalize run")
    parser.add_argument("--version", action="store_true", help="Print script version and exit")
    return parser


def validate_args(args: argparse.Namespace) -> None:
    args.db = resolve_stored_path(args.db)
    args.analysis_dir = resolve_stored_path(args.analysis_dir)
    args.output_dir = resolve_stored_path(args.output_dir)
    if args.limit is not None and args.limit < 1:
        raise ValueError("--limit must be >= 1")
    if args.target_words < 50:
        raise ValueError("--target-words must be >= 50")
    if args.max_words < args.target_words:
        raise ValueError("--max-words must be >= --target-words")
    if not args.db.is_file():
        raise FileNotFoundError(f"Database not found: {args.db}")


def config_hash_for(args: argparse.Namespace) -> str:
    config = {
        "normalizer_version": SCRIPT_VERSION,
        "parser_version": PARSER_VERSION,
        "analyzer_id": args.analyzer_id,
        "api_version": args.api_version,
        "target_words": args.target_words,
        "max_words": args.max_words,
    }
    return sha256_text(stable_json(config))[:16]


def show_status(con: sqlite3.Connection) -> int:
    row = con.execute(
        """
        SELECT id, status, started_at, finished_at, parameters_json, summary_json,
               script_version, parser_version, progress_message
        FROM runs
        WHERE lower(stage)='normalize'
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        print("No normalize runs recorded.")
        return 0
    print(pretty_json(dict(row)))
    return 0


def write_jsonl_line(handle, hasher: hashlib._hashlib.HASH, record: dict[str, Any]) -> None:  # type: ignore[attr-defined]
    line = stable_json(record) + "\n"
    handle.write(line)
    hasher.update(line.encode("utf-8"))


def run(args: argparse.Namespace) -> int:
    validate_args(args)
    config_hash = config_hash_for(args)
    con = open_db(args.db)
    ensure_schema(con)
    if args.status:
        try:
            return show_status(con)
        finally:
            con.close()
    candidates = select_candidates(con, args.analyzer_id, args.api_version, args.document_id, args.limit)
    print(f"Selected {len(candidates)} successful current analysis record(s) for {args.analyzer_id} / {args.api_version}.")
    if not candidates:
        con.close()
        return 0
    if args.dry_run:
        missing = 0
        for c in candidates:
            raw = resolve_artifact(c.raw_json_path, args.analysis_dir, "raw", c, "json")
            md = resolve_artifact(c.markdown_path, args.analysis_dir, "markdown", c, "md")
            status = "OK" if raw else "MISSING_JSON"
            if not raw:
                missing += 1
            print(f"{c.document_id}: {status}; json={raw}; markdown={md or 'JSON fallback'}")
        con.close()
        return 1 if missing else 0
    run_id = create_run(con, args, config_hash)
    started = time.monotonic()
    final_paths = {
        "documents": args.output_dir / "documents.jsonl",
        "pages": args.output_dir / "pages.jsonl",
        "chunks": args.output_dir / "chunks.jsonl",
        "tables": args.output_dir / "tables.jsonl",
        "provenance": args.output_dir / "provenance.jsonl",
    }
    partials: dict[str, Path] = {}
    handles: dict[str, Any] = {}
    hashers = {k: hashlib.sha256() for k in final_paths}
    total_pages = total_chunks = total_tables = total_provenance = 0
    succeeded = failed = 0
    try:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for key, path in final_paths.items():
            partial, handle = atomic_open(path)
            partials[key] = partial
            handles[key] = handle
        total = len(candidates)
        con.execute(
            "UPDATE runs SET total_units=?, current_phase='normalizing', heartbeat_at=? WHERE id=?",
            (total, utcnow(), run_id),
        )
        con.commit()
        for i, candidate in enumerate(candidates, start=1):
            print(f"[{i}/{total}] {candidate.document_id} {candidate.title}")
            mark_normalization_started(con, run_id, candidate, config_hash)
            try:
                normalized = normalize_document(
                    candidate, args.analysis_dir, args.target_words, args.max_words, config_hash
                )
                write_jsonl_line(handles["documents"], hashers["documents"], normalized.document)
                for record in normalized.pages:
                    write_jsonl_line(handles["pages"], hashers["pages"], record)
                for record in normalized.chunks:
                    write_jsonl_line(handles["chunks"], hashers["chunks"], record)
                for record in normalized.tables:
                    write_jsonl_line(handles["tables"], hashers["tables"], record)
                for record in normalized.provenance:
                    write_jsonl_line(handles["provenance"], hashers["provenance"], record)
                p_count = len(normalized.pages)
                c_count = len(normalized.chunks)
                t_count = len(normalized.tables)
                pr_count = len(normalized.provenance)
                total_pages += p_count
                total_chunks += c_count
                total_tables += t_count
                total_provenance += pr_count
                succeeded += 1
                mark_normalization_finished(
                    con, candidate, config_hash, "SUCCEEDED", normalized.output_sha256,
                    p_count, c_count, t_count, pr_count,
                )
                print(f"          OK pages={p_count} chunks={c_count} tables={t_count} hash={normalized.output_sha256[:12]}")
            except Exception as exc:
                failed += 1
                code = type(exc).__name__
                message = str(exc)
                mark_normalization_finished(
                    con, candidate, config_hash, "FAILED", None, None, None, None, None, code, message
                )
                record_error(
                    con, run_id, candidate.document_id, code, message,
                    {
                        "analysis_id": candidate.analysis_id,
                        "raw_json_path": candidate.raw_json_path,
                        "markdown_path": candidate.markdown_path,
                    },
                )
                print(f"          FAILED {code}: {message}", file=sys.stderr)
                if not args.skip_errors:
                    raise
            update_run_progress(
                con, run_id, i, total, succeeded, failed, total_pages, total_chunks,
                total_tables, f"Normalized {candidate.document_id}; {succeeded} succeeded, {failed} failed",
            )
        for handle in handles.values():
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
        handles.clear()
        for key, final_path in final_paths.items():
            os.replace(partials[key], final_path)
        output_hashes = {key: hasher.hexdigest() for key, hasher in hashers.items()}
        status = "SUCCEEDED" if failed == 0 else "COMPLETED_WITH_ERRORS"
        finish_run(
            con, run_id, status, len(candidates), succeeded, failed, total_pages,
            total_chunks, total_tables, total_provenance, time.monotonic() - started, output_hashes,
        )
        print()
        print(
            f"Run {run_id} {status}: {succeeded} document(s), {total_pages} pages, "
            f"{total_chunks} chunks, {total_tables} tables, {total_provenance} provenance records."
        )
        for key, path in final_paths.items():
            print(f"  {key:10s} {path} sha256={output_hashes[key]}")
        return 0 if failed == 0 else 1
    except KeyboardInterrupt:
        for handle in handles.values():
            try:
                handle.close()
            except Exception:
                pass
        for p in partials.values():
            safe_unlink(p)
        con.execute(
            "UPDATE runs SET status='INTERRUPTED', finished_at=?, heartbeat_at=?, progress_message=? WHERE id=?",
            (utcnow(), utcnow(), "Interrupted by user; partial JSONL removed", run_id),
        )
        con.commit()
        print("\nInterrupted. Partial JSONL files were removed.")
        return 130
    except Exception as exc:
        for handle in handles.values():
            try:
                handle.close()
            except Exception:
                pass
        for p in partials.values():
            safe_unlink(p)
        con.execute(
            "UPDATE runs SET status='FAILED', finished_at=?, heartbeat_at=?, progress_message=? WHERE id=?",
            (utcnow(), utcnow(), str(exc)[:1000], run_id),
        )
        con.commit()
        print(f"FATAL: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1
    finally:
        con.close()


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.version:
        print(SCRIPT_VERSION)
        return 0
    try:
        return run(args)
    except Exception as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
