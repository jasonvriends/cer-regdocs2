#!/usr/bin/env python3
"""Experimental Stage 3b: analyze current REGDOCS files locally with Docling.

The canonical raw artifact preserves the native DoclingDocument export under
``regdocsDocling.native`` and also contains a conservative REGDOCS compatibility
projection in ``contents[]`` so the existing Stage 4 normalizer can consume the
result without pretending that the native Docling schema is Azure's schema.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

from regdocs_paths import ANALYZE_DIR, DATABASE_PATH, DOWNLOAD_DIR, resolve_stored_path, stored_path

SCRIPT_VERSION = "3d.0.1"
PARSER_VERSION = "regdocs-docling-projection-2026-08-08-v1"
DEFAULT_ANALYZER_ID = "docling-standard"
DEFAULT_OUTPUT_DIR = ANALYZE_DIR / "docling"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    with partial.open("w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(partial, path)


def open_db(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA synchronous = NORMAL")
    return con


def ensure_schema(con: sqlite3.Connection) -> None:
    required = {"documents", "files", "analyses", "runs", "errors"}
    existing = {str(r[0]) for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    missing = required - existing
    if missing:
        raise RuntimeError(f"Database is missing required table(s): {', '.join(sorted(missing))}")


@dataclass(frozen=True)
class Candidate:
    document_id: str
    file_id: int
    db_path: str
    sha256: str
    extension: Optional[str]
    mime_type: Optional[str]
    size_bytes: Optional[int]


def select_candidates(
    con: sqlite3.Connection,
    analyzer_id: str,
    docling_version: str,
    document_ids: Optional[Sequence[str]],
    limit: Optional[int],
    force: bool,
) -> list[Candidate]:
    where = ["f.is_current=1"]
    params: list[Any] = []
    if not force:
        where.append(
            "NOT EXISTS (SELECT 1 FROM analyses a WHERE a.file_id=f.id AND a.file_sha256=f.sha256 "
            "AND a.analyzer_id=? AND a.api_version=? AND a.status='SUCCEEDED')"
        )
        params.extend([analyzer_id, docling_version])
    if document_ids:
        placeholders = ",".join("?" for _ in document_ids)
        where.append(f"d.id IN ({placeholders})")
        params.extend(str(x) for x in document_ids)
    rows = con.execute(
        f"""
        SELECT d.id AS document_id, f.id AS file_id, f.path AS db_path,
               f.sha256, f.extension, f.mime_type, f.size_bytes
        FROM files f JOIN documents d ON d.id=f.document_id
        WHERE {' AND '.join(where)}
        """,
        params,
    ).fetchall()
    rows = sorted(rows, key=lambda r: (0, int(r["document_id"])) if str(r["document_id"]).isdigit() else (1, str(r["document_id"])))
    if limit is not None:
        rows = rows[:limit]
    return [Candidate(**dict(r)) for r in rows]


def resolve_source(candidate: Candidate, download_dir: Path) -> Path:
    stored = Path(candidate.db_path).expanduser()
    possibilities = [resolve_stored_path(stored), stored]
    if candidate.extension:
        possibilities.append(download_dir / f"{candidate.document_id}.{candidate.extension.lstrip('.')}")
    for path in possibilities:
        if path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != candidate.sha256:
                raise ValueError(f"Source SHA-256 mismatch for {candidate.document_id}: {path}")
            return path
    raise FileNotFoundError(f"Current source file not found for {candidate.document_id}: {candidate.db_path}")


def create_run(con: sqlite3.Connection, args: argparse.Namespace, version: str, total: int) -> int:
    now = utcnow()
    params = {
        "db": stored_path(args.db),
        "download_dir": stored_path(args.download_dir),
        "output_dir": stored_path(args.output_dir),
        "analyzer_id": args.analyzer_id,
        "docling_version": version,
        "document_id": args.document_id,
        "limit": args.limit,
        "all": args.all_candidates,
        "force": args.force,
    }
    cur = con.execute(
        """
        INSERT INTO runs (
            stage,status,started_at,parameters_json,summary_json,script_version,parser_version,
            current_phase,heartbeat_at,completed_units,total_units,progress_message
        ) VALUES ('analyze_docling','RUNNING',?,?,'{}',?,?, 'converting',?,0,?,?)
        """,
        (now, json.dumps(params, sort_keys=True), SCRIPT_VERSION, PARSER_VERSION, now, total, "Converting with Docling"),
    )
    con.commit()
    return int(cur.lastrowid)


def upsert_analysis(
    con: sqlite3.Connection,
    run_id: int,
    c: Candidate,
    analyzer_id: str,
    version: str,
    status: str,
    started_at: str,
    elapsed: float,
    raw_path: Optional[Path] = None,
    md_path: Optional[Path] = None,
    page_count: Optional[int] = None,
    table_count: Optional[int] = None,
    section_count: Optional[int] = None,
    warning_count: Optional[int] = None,
    error_code: Optional[str] = None,
    error_message: Optional[str] = None,
) -> None:
    now = utcnow()
    con.execute(
        """
        INSERT INTO analyses (
            run_id,document_id,file_id,file_sha256,analyzer_id,api_version,status,
            started_at,finished_at,raw_json_path,markdown_path,page_count,table_count,
            section_count,warning_count,elapsed_seconds,attempt_count,artifact_source,
            error_code,error_message,created_at,updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,'docling',?,?,?,?)
        ON CONFLICT(file_id,file_sha256,analyzer_id,api_version) DO UPDATE SET
            run_id=excluded.run_id,status=excluded.status,started_at=excluded.started_at,
            finished_at=excluded.finished_at,raw_json_path=excluded.raw_json_path,
            markdown_path=excluded.markdown_path,page_count=excluded.page_count,
            table_count=excluded.table_count,section_count=excluded.section_count,
            warning_count=excluded.warning_count,elapsed_seconds=excluded.elapsed_seconds,
            attempt_count=analyses.attempt_count+1,artifact_source='docling',
            error_code=excluded.error_code,error_message=excluded.error_message,
            updated_at=excluded.updated_at
        """,
        (
            run_id,c.document_id,c.file_id,c.sha256,analyzer_id,version,status,
            started_at,now,stored_path(raw_path) if raw_path else None,
            stored_path(md_path) if md_path else None,page_count,table_count,section_count,
            warning_count,elapsed,error_code,error_message[:4000] if error_message else None,now,now,
        ),
    )
    con.commit()


def _bbox_values(bbox: Any) -> list[float]:
    if not isinstance(bbox, dict):
        return []
    vals = []
    for key in ("l", "t", "r", "b"):
        try:
            vals.append(float(bbox[key]))
        except (KeyError, TypeError, ValueError):
            return []
    return vals


def _source_from_prov(prov: Any) -> Optional[str]:
    if not isinstance(prov, list) or not prov:
        return None
    p = prov[0]
    if not isinstance(p, dict):
        return None
    try:
        page = int(p.get("page_no"))
    except (TypeError, ValueError):
        return None
    coords = _bbox_values(p.get("bbox"))
    inside = ",".join([str(page), *[f"{x:.4f}" for x in coords]])
    return f"D({inside})"


def _page_no(item: dict[str, Any]) -> Optional[int]:
    prov = item.get("prov")
    if isinstance(prov, list) and prov and isinstance(prov[0], dict):
        try:
            return int(prov[0].get("page_no"))
        except (TypeError, ValueError):
            return None
    return None


def _page_dimensions(native: dict[str, Any], page_no: int) -> tuple[Optional[float], Optional[float]]:
    pages = native.get("pages") or {}
    page = pages.get(str(page_no)) if isinstance(pages, dict) else None
    if page is None and isinstance(pages, dict):
        page = pages.get(page_no)
    if not isinstance(page, dict):
        return None, None
    size = page.get("size") if isinstance(page.get("size"), dict) else page
    try:
        return float(size.get("width")), float(size.get("height"))
    except (TypeError, ValueError, AttributeError):
        return None, None


def _table_projection(table: dict[str, Any]) -> dict[str, Any]:
    data = table.get("data") if isinstance(table.get("data"), dict) else {}
    grid = data.get("grid") if isinstance(data.get("grid"), list) else []
    cells: list[dict[str, Any]] = []
    for r_idx, row in enumerate(grid):
        if not isinstance(row, list):
            continue
        for c_idx, cell in enumerate(row):
            if not isinstance(cell, dict):
                continue
            text = str(cell.get("text") or "")
            cells.append({
                "rowIndex": int(cell.get("start_row_offset_idx", r_idx) or r_idx),
                "columnIndex": int(cell.get("start_col_offset_idx", c_idx) or c_idx),
                "rowSpan": int(cell.get("row_span", 1) or 1),
                "columnSpan": int(cell.get("col_span", cell.get("column_span", 1)) or 1),
                "kind": "columnHeader" if cell.get("column_header") else "rowHeader" if cell.get("row_header") else "content",
                "content": text,
                "source": _source_from_prov(cell.get("prov")) or _source_from_prov(table.get("prov")),
            })
    return {
        "rowCount": int(data.get("num_rows", len(grid)) or len(grid)),
        "columnCount": int(data.get("num_cols", max((len(r) for r in grid if isinstance(r, list)), default=0)) or 0),
        "cells": cells,
        "source": _source_from_prov(table.get("prov")),
    }


def project_docling(doc: Any, native: dict[str, Any], markdown: str) -> dict[str, Any]:
    texts = native.get("texts") if isinstance(native.get("texts"), list) else []
    tables_native = native.get("tables") if isinstance(native.get("tables"), list) else []

    paragraphs: list[dict[str, Any]] = []
    element_order: list[tuple[int, str]] = []
    running = 0
    role_map = {
        "section_header": "sectionHeading",
        "title": "title",
        "page_header": "pageHeader",
        "page_footer": "pageFooter",
    }
    for item in texts:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or item.get("orig") or "").strip()
        if not text:
            continue
        idx = len(paragraphs)
        label = str(item.get("label") or "text")
        paragraph = {
            "content": text,
            "role": role_map.get(label),
            "source": _source_from_prov(item.get("prov")),
            "span": {"offset": running, "length": len(text)},
        }
        paragraphs.append({k: v for k, v in paragraph.items() if v is not None})
        page = _page_no(item) or 0
        element_order.append((page * 1_000_000 + running, f"/paragraphs/{idx}"))
        running += len(text) + 2

    tables = []
    for item in tables_native:
        if not isinstance(item, dict):
            continue
        idx = len(tables)
        projected = _table_projection(item)
        tables.append(projected)
        page = _page_no(item) or 0
        element_order.append((page * 1_000_000 + running + idx, f"/tables/{idx}"))

    page_numbers = sorted({p for p in [_page_no(x) for x in texts + tables_native] if p is not None})
    if not page_numbers and isinstance(native.get("pages"), dict):
        for key in native["pages"]:
            try:
                page_numbers.append(int(key))
            except (TypeError, ValueError):
                pass
        page_numbers = sorted(set(page_numbers))

    pages = []
    md_offset = 0
    for page_no in page_numbers:
        try:
            page_md = doc.export_to_markdown(page_no=page_no)
        except TypeError:
            page_md = ""
        if not isinstance(page_md, str):
            page_md = ""
        width, height = _page_dimensions(native, page_no)
        pages.append({
            "pageNumber": page_no,
            "width": width,
            "height": height,
            "angle": 0,
            "spans": [{"offset": md_offset, "length": len(page_md)}],
        })
        md_offset += len(page_md)
        if page_md:
            md_offset += 2

    element_order.sort(key=lambda x: x[0])
    sections = [{"elements": [ptr for _, ptr in element_order]}] if element_order else []
    return {
        "markdown": markdown,
        "unit": "pt",
        "pages": pages,
        "paragraphs": paragraphs,
        "sections": sections,
        "tables": tables,
        "figures": [],
    }


def analyze_one(con: sqlite3.Connection, run_id: int, args: argparse.Namespace, c: Candidate, version: str, converter: Any) -> bool:
    started_at = utcnow()
    t0 = time.monotonic()
    try:
        source = resolve_source(c, args.download_dir)
        result = converter.convert(source)
        doc = result.document
        native = doc.export_to_dict()
        markdown = doc.export_to_markdown()
        if not isinstance(native, dict) or not isinstance(markdown, str):
            raise ValueError("Docling returned an unexpected document export")
        content = project_docling(doc, native, markdown)
        envelope = {
            "analyzerId": args.analyzer_id,
            "apiVersion": version,
            "contents": [content],
            "warnings": [],
            "regdocsDocling": {
                "provider": "docling",
                "pipeline": "standard",
                "doclingVersion": version,
                "projectionVersion": PARSER_VERSION,
                "native": native,
            },
        }
        raw_path = args.output_dir / "raw" / args.analyzer_id / version / c.document_id / f"{c.sha256}.json"
        md_path = args.output_dir / "markdown" / args.analyzer_id / version / c.document_id / f"{c.sha256}.md"
        atomic_write_text(raw_path, json.dumps(envelope, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        atomic_write_text(md_path, markdown)
        elapsed = time.monotonic() - t0
        upsert_analysis(
            con,run_id,c,args.analyzer_id,version,"SUCCEEDED",started_at,elapsed,raw_path,md_path,
            len(content["pages"]),len(content["tables"]),len(content["sections"]),0,
        )
        print(f"{c.document_id}: SUCCEEDED ({len(content['pages'])} pages, {len(content['tables'])} tables, {elapsed:.1f}s)")
        return True
    except Exception as exc:
        elapsed = time.monotonic() - t0
        upsert_analysis(con,run_id,c,args.analyzer_id,version,"FAILED",started_at,elapsed,error_code=type(exc).__name__,error_message=str(exc))
        print(f"{c.document_id}: FAILED: {exc}", file=sys.stderr)
        return False


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="REGDOCS Stage 3b: local Docling analysis", formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("--db", default=stored_path(DATABASE_PATH))
    p.add_argument("--download-dir", default=stored_path(DOWNLOAD_DIR))
    p.add_argument("--output-dir", default=stored_path(DEFAULT_OUTPUT_DIR))
    p.add_argument("--analyzer-id", default=DEFAULT_ANALYZER_ID)
    scope = p.add_mutually_exclusive_group(required=False)
    scope.add_argument("--document-id", action="append")
    scope.add_argument("--limit", type=int)
    scope.add_argument("--all", dest="all_candidates", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--version", action="store_true")
    return p


def main() -> int:
    args = build_parser().parse_args()
    if args.version:
        print(SCRIPT_VERSION)
        return 0
    if not (args.document_id or args.limit or args.all_candidates):
        raise SystemExit("Choose exactly one selection scope: --document-id, --limit, or --all")
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be >= 1")
    args.db = resolve_stored_path(args.db)
    args.download_dir = resolve_stored_path(args.download_dir)
    args.output_dir = resolve_stored_path(args.output_dir)
    try:
        version = importlib.metadata.version("docling")
    except importlib.metadata.PackageNotFoundError:
        raise SystemExit("Docling is not installed. Run: pip install -r pipeline/requirements-docling.txt")
    con = open_db(args.db)
    try:
        ensure_schema(con)
        candidates = select_candidates(con,args.analyzer_id,version,args.document_id,args.limit,args.force)
        print(f"Selected {len(candidates)} current file(s) for {args.analyzer_id} / Docling {version}.")
        if args.dry_run or not candidates:
            for c in candidates:
                print(f"{c.document_id}: file_id={c.file_id} sha256={c.sha256}")
            return 0
        from docling.document_converter import DocumentConverter
        converter = DocumentConverter()
        run_id = create_run(con,args,version,len(candidates))
        succeeded = failed = 0
        started = time.monotonic()
        for i,c in enumerate(candidates,1):
            if analyze_one(con,run_id,args,c,version,converter): succeeded += 1
            else: failed += 1
            summary = {"documents_total":len(candidates),"completed":i,"succeeded":succeeded,"failed":failed}
            con.execute("UPDATE runs SET heartbeat_at=?,completed_units=?,summary_json=?,progress_message=? WHERE id=?",(utcnow(),i,json.dumps(summary,sort_keys=True),f"Docling {i}/{len(candidates)}",run_id))
            con.commit()
        status = "SUCCEEDED" if failed == 0 else "PARTIAL"
        summary = {"documents_total":len(candidates),"succeeded":succeeded,"failed":failed,"elapsed_seconds":round(time.monotonic()-started,3),"docling_version":version}
        con.execute("UPDATE runs SET status=?,finished_at=?,heartbeat_at=?,current_phase='finished',summary_json=?,progress_message=? WHERE id=?",(status,utcnow(),utcnow(),json.dumps(summary,sort_keys=True),f"Docling {status}: {succeeded} succeeded, {failed} failed",run_id))
        con.commit()
        return 0 if failed == 0 else 1
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
