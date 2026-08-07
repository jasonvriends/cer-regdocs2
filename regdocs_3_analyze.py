#!/usr/bin/env python3
"""
REGDOCS Stage 3 - ANALYZE

Reads downloaded files from the REGDOCS SQLite database, sends them to
Azure AI Content Understanding using prebuilt-layout, preserves the raw
Azure result JSON + Markdown, and records analysis state in SQLite.

Primary manifest: files table (is_current = 1)
Filesystem: validation / optional fallback only

Azure connection settings may be supplied as command-line parameters or environment variables.
Command-line parameters take precedence. The API key is never written to SQLite.

Environment fallbacks:
    CONTENTUNDERSTANDING_ENDPOINT=https://<resource>.services.ai.azure.com
    CONTENTUNDERSTANDING_KEY=<optional; DefaultAzureCredential is used otherwise>
    CONTENTUNDERSTANDING_API_VERSION=2025-11-01
    CONTENTUNDERSTANDING_ANALYZER_ID=prebuilt-layout

Install:
    python -m pip install azure-ai-contentunderstanding azure-identity

Example:
    python regdocs_3_analyze_azure_v2.py \
        --db ./regdocs.db \
        --download-dir ./downloads \
        --output-dir ./analysis \
        --endpoint "https://<resource>.services.ai.azure.com" \
        --key "<key>" \
        --api-version 2025-11-01 \
        --analyzer-id prebuilt-layout \
        --limit 10

Start with a small pilot. Remove --limit only after validating varied documents.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import sqlite3
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from azure.ai.contentunderstanding import ContentUnderstandingClient
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import HttpResponseError, ServiceRequestError
from azure.identity import DefaultAzureCredential

SCRIPT_VERSION = "3.1.0"
DEFAULT_API_VERSION = "2025-11-01"
PARSER_VERSION = f"azure-content-understanding-{DEFAULT_API_VERSION}"
DEFAULT_ANALYZER_ID = "prebuilt-layout"
DEFAULT_POLLING_INTERVAL = 3
DEFAULT_MAX_ATTEMPTS = 4
DEFAULT_RETRY_BASE_DELAY = 2.0
DEFAULT_RETRY_MAX_DELAY = 30.0

SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".heif",
    ".docx", ".xlsx", ".pptx",
    ".txt", ".html", ".htm", ".md", ".rtf",
    ".xml", ".json", ".csv", ".tsv",
    ".eml", ".msg",
}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def safe_path_component(value: str) -> str:
    """Return a filesystem-safe component while keeping analyzer/API labels readable."""
    cleaned = "".join(c if c.isalnum() or c in "-_." else "_" for c in value.strip())
    return cleaned or "unknown"


def atomic_write_text(path: Path, text: str) -> None:
    """Write to <name>.partial, fsync it, then atomically replace the final artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    with partial.open("w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(partial, path)


def is_retryable_error_code(code: Optional[str]) -> bool:
    return str(code or "").upper() in {
        "408", "429", "500", "502", "503", "504", "SERVICE_REQUEST_ERROR"
    }


def retry_delay_seconds(exc: Exception, attempt: int, base_delay: float, max_delay: float) -> float:
    """Honor a numeric Retry-After header when present, otherwise exponential backoff."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) or {}
    retry_after = headers.get("Retry-After") or headers.get("retry-after")
    if retry_after is not None:
        try:
            return min(max_delay, max(0.0, float(retry_after)))
        except (TypeError, ValueError):
            pass
    return min(max_delay, base_delay * (2 ** max(0, attempt - 1)))


@dataclass(frozen=True)
class Candidate:
    document_id: str
    file_id: int
    db_path: str
    sha256: str
    extension: Optional[str]
    mime_type: Optional[str]
    size_bytes: Optional[int]


@dataclass
class AnalysisOutcome:
    document_id: str
    file_id: int
    status: str
    operation_id: Optional[str] = None
    api_version: Optional[str] = None
    raw_json_path: Optional[str] = None
    markdown_path: Optional[str] = None
    page_count: Optional[int] = None
    table_count: Optional[int] = None
    section_count: Optional[int] = None
    warning_count: Optional[int] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    elapsed_seconds: Optional[float] = None
    attempt_count: int = 0


def open_db(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA synchronous = NORMAL")
    return con


def _create_analyses_table(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER,
            document_id TEXT NOT NULL,
            file_id INTEGER NOT NULL,
            file_sha256 TEXT NOT NULL,
            analyzer_id TEXT NOT NULL,
            api_version TEXT NOT NULL,
            operation_id TEXT,
            status TEXT NOT NULL DEFAULT 'PENDING',
            started_at TEXT,
            finished_at TEXT,
            raw_json_path TEXT,
            markdown_path TEXT,
            page_count INTEGER,
            table_count INTEGER,
            section_count INTEGER,
            warning_count INTEGER,
            elapsed_seconds REAL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            error_code TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(file_id, file_sha256, analyzer_id, api_version),
            FOREIGN KEY (run_id) REFERENCES runs(id),
            FOREIGN KEY (document_id) REFERENCES documents(id),
            FOREIGN KEY (file_id) REFERENCES files(id)
        )
        """
    )


def _create_analyses_indexes(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_analyses_document
            ON analyses(document_id);

        CREATE INDEX IF NOT EXISTS idx_analyses_status
            ON analyses(status);

        CREATE INDEX IF NOT EXISTS idx_analyses_file_analyzer
            ON analyses(file_id, analyzer_id, api_version);
        """
    )


def _has_analysis_identity_index(con: sqlite3.Connection) -> bool:
    wanted = ["file_id", "file_sha256", "analyzer_id", "api_version"]
    for row in con.execute("PRAGMA index_list(analyses)").fetchall():
        # PRAGMA index_list columns: seq, name, unique, origin, partial
        if not bool(row[2]):
            continue
        index_name = str(row[1]).replace("'", "''")
        cols = [r[2] for r in con.execute(f"PRAGMA index_info('{index_name}')").fetchall()]
        if cols == wanted:
            return True
    return False


def _rebuild_analyses_table(con: sqlite3.Connection) -> None:
    """Migrate the known pre-3.1 analyses schema without losing completed work."""
    columns = {str(r[1]) for r in con.execute("PRAGMA table_info(analyses)").fetchall()}
    required_legacy = {
        "id", "run_id", "document_id", "file_id", "file_sha256", "analyzer_id",
        "operation_id", "status", "started_at", "finished_at", "raw_json_path",
        "markdown_path", "page_count", "table_count", "section_count", "warning_count",
        "error_code", "error_message", "created_at", "updated_at",
    }
    missing = sorted(required_legacy - columns)
    if missing:
        raise RuntimeError(
            "Existing analyses table has an unknown/incomplete schema; "
            f"cannot safely migrate automatically. Missing columns: {', '.join(missing)}"
        )

    legacy_name = "analyses__legacy_3_1"
    if con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (legacy_name,)
    ).fetchone():
        raise RuntimeError(
            f"Refusing schema migration because temporary table {legacy_name!r} already exists."
        )

    con.execute("BEGIN IMMEDIATE")
    try:
        con.execute(f"ALTER TABLE analyses RENAME TO {legacy_name}")
        _create_analyses_table(con)

        api_expr = (
            f"COALESCE(api_version, '{DEFAULT_API_VERSION}')"
            if "api_version" in columns
            else f"'{DEFAULT_API_VERSION}'"
        )
        elapsed_expr = "elapsed_seconds" if "elapsed_seconds" in columns else "NULL"
        attempts_expr = "attempt_count" if "attempt_count" in columns else "0"

        con.execute(
            f"""
            INSERT OR IGNORE INTO analyses (
                id, run_id, document_id, file_id, file_sha256, analyzer_id, api_version,
                operation_id, status, started_at, finished_at, raw_json_path, markdown_path,
                page_count, table_count, section_count, warning_count, elapsed_seconds,
                attempt_count, error_code, error_message, created_at, updated_at
            )
            SELECT
                id, run_id, document_id, file_id, file_sha256, analyzer_id, {api_expr},
                operation_id, status, started_at, finished_at, raw_json_path, markdown_path,
                page_count, table_count, section_count, warning_count, {elapsed_expr},
                {attempts_expr}, error_code, error_message, created_at, updated_at
            FROM {legacy_name}
            """
        )
        con.execute(f"DROP TABLE {legacy_name}")
        _create_analyses_indexes(con)
        con.commit()
    except Exception:
        con.rollback()
        raise


def ensure_schema(con: sqlite3.Connection) -> None:
    exists = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='analyses'"
    ).fetchone()

    if not exists:
        _create_analyses_table(con)
        _create_analyses_indexes(con)
        con.commit()
        return

    columns = {str(r[1]) for r in con.execute("PRAGMA table_info(analyses)").fetchall()}
    if "api_version" not in columns or not _has_analysis_identity_index(con):
        _rebuild_analyses_table(con)
        return

    # Additive 3.1 fields can be migrated in place.
    if "elapsed_seconds" not in columns:
        con.execute("ALTER TABLE analyses ADD COLUMN elapsed_seconds REAL")
    if "attempt_count" not in columns:
        con.execute("ALTER TABLE analyses ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0")

    _create_analyses_indexes(con)
    con.commit()


def create_run(con: sqlite3.Connection, args: argparse.Namespace) -> int:
    now = utcnow()
    params = {
        "db": str(args.db),
        "download_dir": str(args.download_dir) if args.download_dir else None,
        "output_dir": str(args.output_dir),
        "endpoint": args.endpoint,
        "auth_mode": "key" if args.key else "default_azure_credential",
        "api_version": args.api_version,
        "polling_interval": args.polling_interval,
        "max_attempts": args.max_attempts,
        "retry_base_delay": args.retry_base_delay,
        "retry_max_delay": args.retry_max_delay,
        "analyzer_id": args.analyzer_id,
        "limit": args.limit,
        "document_id": args.document_id,
        "force": args.force,
    }
    cur = con.execute(
        """
        INSERT INTO runs (
            stage, status, started_at, parameters_json, summary_json,
            script_version, parser_version, current_phase, heartbeat_at,
            completed_units, total_units, progress_message
        ) VALUES (?, ?, ?, ?, '{}', ?, ?, ?, ?, 0, NULL, ?)
        """,
        (
            "analyze",
            "RUNNING",
            now,
            json.dumps(params),
            SCRIPT_VERSION,
            f"azure-content-understanding-{args.api_version}",
            "selecting_candidates",
            now,
            "Selecting downloaded files for Azure Content Understanding",
        ),
    )
    con.commit()
    return int(cur.lastrowid)


def finish_run(
    con: sqlite3.Connection,
    run_id: int,
    status: str,
    completed: int,
    total: int,
    succeeded: int,
    failed: int,
    skipped: int,
    pages_succeeded: int,
    analysis_attempts: int,
    elapsed: float,
) -> None:
    now = utcnow()
    summary = {
        "status": status,
        "documents_total": total,
        "documents_completed": completed,
        "succeeded": succeeded,
        "failed": failed,
        "skipped": skipped,
        "pages_succeeded": pages_succeeded,
        "analysis_attempts": analysis_attempts,
        "elapsed_seconds": round(elapsed, 2),
    }
    con.execute(
        """
        UPDATE runs
        SET status=?, finished_at=?, summary_json=?, current_phase=?, heartbeat_at=?,
            completed_units=?, total_units=?, progress_message=?,
            successful_requests=?, failed_requests=?
        WHERE id=?
        """,
        (
            status,
            now,
            json.dumps(summary),
            status.lower(),
            now,
            completed,
            total,
            (
                f"Analyze {status}: {succeeded} succeeded, {failed} failed, "
                f"{skipped} skipped, {pages_succeeded} pages"
            ),
            succeeded,
            failed,
            run_id,
        ),
    )
    con.commit()


def update_run_progress(
    con: sqlite3.Connection,
    run_id: int,
    completed: int,
    total: int,
    succeeded: int,
    failed: int,
    skipped: int,
    pages_succeeded: int,
    analysis_attempts: int,
    message: str,
) -> None:
    live_summary = {
        "documents_total": total,
        "documents_completed": completed,
        "succeeded": succeeded,
        "failed": failed,
        "skipped": skipped,
        "pages_succeeded": pages_succeeded,
        "analysis_attempts": analysis_attempts,
    }
    con.execute(
        """
        UPDATE runs
        SET heartbeat_at=?, current_phase='analyzing', completed_units=?, total_units=?,
            progress_message=?, successful_requests=?, failed_requests=?, summary_json=?
        WHERE id=?
        """,
        (
            utcnow(), completed, total, message, succeeded, failed,
            json.dumps(live_summary), run_id,
        ),
    )
    con.commit()


def select_candidates(
    con: sqlite3.Connection,
    analyzer_id: str,
    api_version: str,
    limit: Optional[int],
    document_id: Optional[str],
    force: bool,
) -> list[Candidate]:
    where = ["f.is_current = 1"]
    params: list[Any] = []

    if document_id:
        where.append("d.id = ?")
        params.append(document_id)

    if not force:
        where.append(
            """
            NOT EXISTS (
                SELECT 1
                FROM analyses a
                WHERE a.file_id = f.id
                  AND a.file_sha256 = f.sha256
                  AND a.analyzer_id = ?
                  AND a.api_version = ?
                  AND a.status = 'SUCCEEDED'
            )
            """
        )
        params.extend([analyzer_id, api_version])

    sql = f"""
        SELECT
            d.id AS document_id,
            f.id AS file_id,
            f.path AS db_path,
            f.sha256,
            f.extension,
            f.mime_type,
            f.size_bytes
        FROM files f
        JOIN documents d ON d.id = f.document_id
        WHERE {' AND '.join(where)}
        ORDER BY CAST(d.id AS INTEGER), d.id
    """

    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)

    rows = con.execute(sql, params).fetchall()
    return [
        Candidate(
            document_id=str(r["document_id"]),
            file_id=int(r["file_id"]),
            db_path=str(r["db_path"]),
            sha256=str(r["sha256"]),
            extension=r["extension"],
            mime_type=r["mime_type"],
            size_bytes=r["size_bytes"],
        )
        for r in rows
    ]


def resolve_file_path(candidate: Candidate, db_path: Path, download_dir: Optional[Path]) -> Path:
    p = Path(candidate.db_path)
    attempts: list[Path] = []

    if p.is_absolute():
        attempts.append(p)
    else:
        if download_dir:
            attempts.append(download_dir / p)
        attempts.append(db_path.parent / p)
        attempts.append(p)

    # Common downloader layout fallback: <download-dir>/<document_id>.<ext>
    if download_dir:
        ext = candidate.extension or Path(candidate.db_path).suffix
        if ext:
            if not str(ext).startswith("."):
                ext = "." + str(ext)
            attempts.append(download_dir / f"{candidate.document_id}{ext}")
        attempts.append(download_dir / f"{candidate.document_id}.pdf")

    seen = set()
    for attempt in attempts:
        resolved = attempt.expanduser().resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_file():
            return resolved

    raise FileNotFoundError(
        f"Downloaded file not found for document {candidate.document_id}; "
        f"DB path={candidate.db_path!r}; tried: " + ", ".join(str(x) for x in attempts)
    )


def detect_mime(path: Path, candidate: Candidate) -> str:
    if candidate.mime_type and candidate.mime_type != "application/octet-stream":
        return candidate.mime_type.split(";", 1)[0].strip()
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def upsert_analysis_start(
    con: sqlite3.Connection,
    run_id: int,
    candidate: Candidate,
    analyzer_id: str,
    api_version: str,
) -> None:
    now = utcnow()
    con.execute(
        """
        INSERT INTO analyses (
            run_id, document_id, file_id, file_sha256, analyzer_id, api_version,
            status, started_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'RUNNING', ?, ?, ?)
        ON CONFLICT(file_id, file_sha256, analyzer_id, api_version) DO UPDATE SET
            run_id=excluded.run_id,
            status='RUNNING',
            started_at=excluded.started_at,
            finished_at=NULL,
            operation_id=NULL,
            raw_json_path=NULL,
            markdown_path=NULL,
            page_count=NULL,
            table_count=NULL,
            section_count=NULL,
            warning_count=NULL,
            elapsed_seconds=NULL,
            attempt_count=0,
            error_code=NULL,
            error_message=NULL,
            updated_at=excluded.updated_at
        """,
        (
            run_id,
            candidate.document_id,
            candidate.file_id,
            candidate.sha256,
            analyzer_id,
            api_version,
            now,
            now,
            now,
        ),
    )
    con.commit()


def store_outcome(
    con: sqlite3.Connection,
    candidate: Candidate,
    analyzer_id: str,
    api_version: str,
    outcome: AnalysisOutcome,
) -> None:
    con.execute(
        """
        UPDATE analyses
        SET status=?, operation_id=?, finished_at=?,
            raw_json_path=?, markdown_path=?, page_count=?, table_count=?,
            section_count=?, warning_count=?, elapsed_seconds=?, attempt_count=?,
            error_code=?, error_message=?, updated_at=?
        WHERE file_id=? AND file_sha256=? AND analyzer_id=? AND api_version=?
        """,
        (
            outcome.status,
            outcome.operation_id,
            utcnow(),
            outcome.raw_json_path,
            outcome.markdown_path,
            outcome.page_count,
            outcome.table_count,
            outcome.section_count,
            outcome.warning_count,
            outcome.elapsed_seconds,
            outcome.attempt_count,
            outcome.error_code,
            outcome.error_message,
            utcnow(),
            candidate.file_id,
            candidate.sha256,
            analyzer_id,
            api_version,
        ),
    )
    con.commit()


def record_error(
    con: sqlite3.Connection,
    run_id: int,
    document_id: str,
    code: str,
    message: str,
    retryable: bool,
    context: dict[str, Any],
) -> None:
    con.execute(
        """
        INSERT INTO errors (
            run_id, document_id, stage, code, severity, message,
            retryable, context_json, created_at
        ) VALUES (?, ?, 'analyze', ?, 'ERROR', ?, ?, ?, ?)
        """,
        (
            run_id,
            document_id,
            code,
            message[:4000],
            1 if retryable else 0,
            json.dumps(context, default=str),
            utcnow(),
        ),
    )
    con.commit()


def make_client(args: argparse.Namespace) -> tuple[ContentUnderstandingClient, Any]:
    if not args.endpoint:
        raise RuntimeError(
            "Azure Content Understanding endpoint is required. "
            "Pass --endpoint or set CONTENTUNDERSTANDING_ENDPOINT."
        )

    if args.key:
        credential = AzureKeyCredential(args.key)
    else:
        credential = DefaultAzureCredential()

    client = ContentUnderstandingClient(
        endpoint=args.endpoint,
        credential=credential,
        api_version=args.api_version,
        polling_interval=args.polling_interval,
    )
    return client, credential


def analyze_one(
    client: ContentUnderstandingClient,
    candidate: Candidate,
    file_path: Path,
    output_dir: Path,
    analyzer_id: str,
    api_version: str,
    verify_hash: bool,
    max_attempts: int,
    retry_base_delay: float,
    retry_max_delay: float,
) -> AnalysisOutcome:
    start = time.monotonic()
    operation_id: Optional[str] = None

    if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        return AnalysisOutcome(
            document_id=candidate.document_id,
            file_id=candidate.file_id,
            status="SKIPPED_UNSUPPORTED",
            error_code="UNSUPPORTED_EXTENSION",
            error_message=f"Unsupported extension: {file_path.suffix}",
            elapsed_seconds=time.monotonic() - start,
            attempt_count=0,
        )

    if verify_hash:
        actual_hash = sha256_file(file_path)
        if actual_hash.lower() != candidate.sha256.lower():
            return AnalysisOutcome(
                document_id=candidate.document_id,
                file_id=candidate.file_id,
                status="FAILED",
                error_code="HASH_MISMATCH",
                error_message=(
                    f"DB sha256={candidate.sha256}, disk sha256={actual_hash}, path={file_path}"
                ),
                elapsed_seconds=time.monotonic() - start,
                attempt_count=0,
            )

    mime_type = detect_mime(file_path, candidate)
    data = file_path.read_bytes()

    for attempt in range(1, max_attempts + 1):
        poller = None
        try:
            poller = client.begin_analyze_binary(
                analyzer_id=analyzer_id,
                binary_input=data,
                content_type=mime_type,
            )
            operation_id = getattr(poller, "operation_id", None)
            result = poller.result()

            result_dict = result.as_dict()
            result_api_version = getattr(result, "api_version", None)

            analyzer_component = safe_path_component(analyzer_id)
            api_component = safe_path_component(api_version)
            raw_dir = output_dir / "raw" / analyzer_component / api_component / candidate.document_id
            md_dir = output_dir / "markdown" / analyzer_component / api_component / candidate.document_id

            identity = candidate.sha256.lower()
            raw_path = raw_dir / f"{identity}.json"
            md_path = md_dir / f"{identity}.md"

            markdown_parts: list[str] = []
            page_count = 0
            table_count = 0
            section_count = 0

            for content in getattr(result, "contents", []) or []:
                markdown = getattr(content, "markdown", None)
                if markdown:
                    markdown_parts.append(markdown)
                pages = getattr(content, "pages", None) or []
                tables = getattr(content, "tables", None) or []
                sections = getattr(content, "sections", None) or []
                page_count += len(pages)
                table_count += len(tables)
                section_count += len(sections)

            # Artifacts are committed only after the complete content is available.
            atomic_write_text(raw_path, json_dumps(result_dict))
            atomic_write_text(md_path, "\n\n".join(markdown_parts))

            warnings = getattr(result, "warnings", None) or []

            return AnalysisOutcome(
                document_id=candidate.document_id,
                file_id=candidate.file_id,
                status="SUCCEEDED",
                operation_id=operation_id,
                api_version=result_api_version,
                raw_json_path=str(raw_path),
                markdown_path=str(md_path),
                page_count=page_count,
                table_count=table_count,
                section_count=section_count,
                warning_count=len(warnings),
                elapsed_seconds=time.monotonic() - start,
                attempt_count=attempt,
            )

        except HttpResponseError as exc:
            error_obj = getattr(exc, "error", None)
            code = getattr(error_obj, "code", None) or getattr(exc, "status_code", None) or "HTTP_ERROR"
            code = str(code)
            can_resubmit = poller is None and is_retryable_error_code(code) and attempt < max_attempts
            if can_resubmit:
                delay = retry_delay_seconds(exc, attempt, retry_base_delay, retry_max_delay)
                print(
                    f"          RETRY {attempt}/{max_attempts - 1} after {delay:g}s "
                    f"({code}: {str(exc)[:180]})"
                )
                time.sleep(delay)
                continue
            return AnalysisOutcome(
                document_id=candidate.document_id,
                file_id=candidate.file_id,
                status="FAILED",
                operation_id=operation_id,
                error_code=code,
                error_message=str(exc),
                elapsed_seconds=time.monotonic() - start,
                attempt_count=attempt,
            )

        except ServiceRequestError as exc:
            can_resubmit = poller is None and attempt < max_attempts
            if can_resubmit:
                delay = retry_delay_seconds(exc, attempt, retry_base_delay, retry_max_delay)
                print(
                    f"          RETRY {attempt}/{max_attempts - 1} after {delay:g}s "
                    f"(SERVICE_REQUEST_ERROR: {str(exc)[:180]})"
                )
                time.sleep(delay)
                continue
            return AnalysisOutcome(
                document_id=candidate.document_id,
                file_id=candidate.file_id,
                status="FAILED",
                operation_id=operation_id,
                error_code="SERVICE_REQUEST_ERROR",
                error_message=str(exc),
                elapsed_seconds=time.monotonic() - start,
                attempt_count=attempt,
            )

        except Exception as exc:
            return AnalysisOutcome(
                document_id=candidate.document_id,
                file_id=candidate.file_id,
                status="FAILED",
                operation_id=operation_id,
                error_code=type(exc).__name__,
                error_message=str(exc),
                elapsed_seconds=time.monotonic() - start,
                attempt_count=attempt,
            )

    raise AssertionError("unreachable")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="REGDOCS Stage 3: Azure Content Understanding analysis")
    p.add_argument("--db", type=Path, required=True, help="Path to regdocs.db")
    p.add_argument(
        "--endpoint",
        default=os.environ.get("CONTENTUNDERSTANDING_ENDPOINT"),
        help=(
            "Azure Content Understanding endpoint. "
            "Defaults to CONTENTUNDERSTANDING_ENDPOINT."
        ),
    )
    p.add_argument(
        "--key",
        default=os.environ.get("CONTENTUNDERSTANDING_KEY"),
        help=(
            "Azure Content Understanding API key. "
            "Defaults to CONTENTUNDERSTANDING_KEY. If omitted, DefaultAzureCredential is used. "
            "The key is not stored in SQLite."
        ),
    )
    p.add_argument(
        "--api-version",
        default=os.environ.get("CONTENTUNDERSTANDING_API_VERSION", DEFAULT_API_VERSION),
        help=(
            f"Azure Content Understanding API version (default: {DEFAULT_API_VERSION}; "
            "env: CONTENTUNDERSTANDING_API_VERSION)"
        ),
    )
    p.add_argument(
        "--polling-interval",
        type=float,
        default=float(os.environ.get("CONTENTUNDERSTANDING_POLLING_INTERVAL", DEFAULT_POLLING_INTERVAL)),
        help=(
            f"Seconds between long-running-operation polls when Azure does not provide Retry-After "
            f"(default: {DEFAULT_POLLING_INTERVAL})"
        ),
    )
    p.add_argument(
        "--max-attempts",
        type=int,
        default=int(os.environ.get("CONTENTUNDERSTANDING_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS)),
        help=(
            "Maximum Azure submission attempts for retryable failures that occur before an "
            f"operation is accepted (default: {DEFAULT_MAX_ATTEMPTS})"
        ),
    )
    p.add_argument(
        "--retry-base-delay",
        type=float,
        default=float(os.environ.get("CONTENTUNDERSTANDING_RETRY_BASE_DELAY", DEFAULT_RETRY_BASE_DELAY)),
        help=f"Initial exponential retry delay in seconds (default: {DEFAULT_RETRY_BASE_DELAY:g})",
    )
    p.add_argument(
        "--retry-max-delay",
        type=float,
        default=float(os.environ.get("CONTENTUNDERSTANDING_RETRY_MAX_DELAY", DEFAULT_RETRY_MAX_DELAY)),
        help=f"Maximum retry delay in seconds (default: {DEFAULT_RETRY_MAX_DELAY:g})",
    )
    p.add_argument(
        "--download-dir",
        type=Path,
        help="Root download directory used to resolve relative file paths / filename fallback",
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        default=Path("analysis/content-understanding"),
        help="Directory for immutable raw JSON and Markdown artifacts",
    )
    p.add_argument(
        "--analyzer-id",
        default=os.environ.get("CONTENTUNDERSTANDING_ANALYZER_ID", DEFAULT_ANALYZER_ID),
        help=(
            f"Azure Content Understanding analyzer ID (default: {DEFAULT_ANALYZER_ID}; "
            "env: CONTENTUNDERSTANDING_ANALYZER_ID)"
        ),
    )
    p.add_argument("--limit", type=int, help="Process at most N candidates")
    p.add_argument("--document-id", help="Analyze one REGDOCS document ID")
    p.add_argument("--force", action="store_true", help="Re-analyze even if this file hash already succeeded")
    p.add_argument(
        "--no-verify-hash",
        action="store_true",
        help="Skip SHA-256 verification against files.sha256",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Show candidates and resolved files without calling Azure",
    )
    return p


def main() -> int:
    args = build_parser().parse_args()
    args.db = args.db.expanduser().resolve()
    args.output_dir = args.output_dir.expanduser().resolve()
    if args.download_dir:
        args.download_dir = args.download_dir.expanduser().resolve()

    if not args.db.is_file():
        print(f"ERROR: database not found: {args.db}", file=sys.stderr)
        return 2

    if not args.dry_run and not args.endpoint:
        print(
            "ERROR: --endpoint is required unless CONTENTUNDERSTANDING_ENDPOINT is set",
            file=sys.stderr,
        )
        return 2

    if args.polling_interval <= 0:
        print("ERROR: --polling-interval must be greater than 0", file=sys.stderr)
        return 2
    if args.max_attempts < 1:
        print("ERROR: --max-attempts must be at least 1", file=sys.stderr)
        return 2
    if args.retry_base_delay < 0 or args.retry_max_delay < 0:
        print("ERROR: retry delays cannot be negative", file=sys.stderr)
        return 2
    if args.retry_max_delay < args.retry_base_delay:
        print("ERROR: --retry-max-delay must be >= --retry-base-delay", file=sys.stderr)
        return 2

    args.output_dir.mkdir(parents=True, exist_ok=True)

    con = open_db(args.db)
    ensure_schema(con)
    run_id = create_run(con, args)
    started = time.monotonic()

    client = None
    credential = None

    try:
        candidates = select_candidates(
            con,
            analyzer_id=args.analyzer_id,
            api_version=args.api_version,
            limit=args.limit,
            document_id=args.document_id,
            force=args.force,
        )
        total = len(candidates)

        con.execute(
            "UPDATE runs SET total_units=?, current_phase='analyzing', heartbeat_at=? WHERE id=?",
            (total, utcnow(), run_id),
        )
        con.commit()

        print(f"Run {run_id}: {total} candidate file(s)")
        print(f"Endpoint: {args.endpoint or '[not configured]'}")
        print(f"Auth:     {'API key' if args.key else 'DefaultAzureCredential'}")
        print(f"API:      {args.api_version}")
        print(f"Analyzer: {args.analyzer_id}")
        print(f"Polling:  {args.polling_interval:g}s")
        print(f"Retries:  {args.max_attempts} max attempts; {args.retry_base_delay:g}-{args.retry_max_delay:g}s backoff")
        print(f"Output:   {args.output_dir}")

        if total == 0:
            finish_run(con, run_id, "SUCCEEDED", 0, 0, 0, 0, 0, 0, 0, time.monotonic() - started)
            return 0

        if not args.dry_run:
            client, credential = make_client(args)

        succeeded = 0
        failed = 0
        skipped = 0
        pages_succeeded = 0
        analysis_attempts = 0

        for i, candidate in enumerate(candidates, start=1):
            try:
                file_path = resolve_file_path(candidate, args.db, args.download_dir)
            except FileNotFoundError as exc:
                outcome = AnalysisOutcome(
                    document_id=candidate.document_id,
                    file_id=candidate.file_id,
                    status="FAILED",
                    error_code="FILE_NOT_FOUND",
                    error_message=str(exc),
                )
                upsert_analysis_start(con, run_id, candidate, args.analyzer_id, args.api_version)
                store_outcome(con, candidate, args.analyzer_id, args.api_version, outcome)
                record_error(
                    con, run_id, candidate.document_id,
                    "FILE_NOT_FOUND", str(exc), False,
                    {"file_id": candidate.file_id, "db_path": candidate.db_path},
                )
                failed += 1
                print(f"[{i}/{total}] FAILED {candidate.document_id}: file not found")
                update_run_progress(
                    con, run_id, i, total, succeeded, failed, skipped,
                    pages_succeeded, analysis_attempts, str(exc)
                )
                continue

            print(f"[{i}/{total}] {candidate.document_id}  {file_path}")

            if args.dry_run:
                skipped += 1
                update_run_progress(
                    con, run_id, i, total, succeeded, failed, skipped,
                    pages_succeeded, analysis_attempts, f"Dry run: {candidate.document_id}"
                )
                continue

            upsert_analysis_start(con, run_id, candidate, args.analyzer_id, args.api_version)
            outcome = analyze_one(
                client=client,
                candidate=candidate,
                file_path=file_path,
                output_dir=args.output_dir,
                analyzer_id=args.analyzer_id,
                api_version=args.api_version,
                verify_hash=not args.no_verify_hash,
                max_attempts=args.max_attempts,
                retry_base_delay=args.retry_base_delay,
                retry_max_delay=args.retry_max_delay,
            )
            store_outcome(con, candidate, args.analyzer_id, args.api_version, outcome)
            analysis_attempts += outcome.attempt_count

            if outcome.status == "SUCCEEDED":
                succeeded += 1
                pages_succeeded += outcome.page_count or 0
                print(
                    f"          SUCCEEDED pages={outcome.page_count} tables={outcome.table_count} "
                    f"sections={outcome.section_count} elapsed={outcome.elapsed_seconds:.1f}s "
                    f"attempts={outcome.attempt_count}"
                )
                print(f"          JSON: {outcome.raw_json_path}")
                print(f"          MD:   {outcome.markdown_path}")
            elif outcome.status.startswith("SKIPPED"):
                skipped += 1
                print(f"          {outcome.status}: {outcome.error_message}")
            else:
                failed += 1
                retryable = is_retryable_error_code(outcome.error_code)
                record_error(
                    con,
                    run_id,
                    candidate.document_id,
                    outcome.error_code or "ANALYZE_FAILED",
                    outcome.error_message or "Azure analysis failed",
                    retryable,
                    {
                        "file_id": candidate.file_id,
                        "file_path": str(file_path),
                        "operation_id": outcome.operation_id,
                        "analyzer_id": args.analyzer_id,
                        "api_version": args.api_version,
                        "attempt_count": outcome.attempt_count,
                    },
                )
                print(f"          FAILED {outcome.error_code}: {outcome.error_message}")

            update_run_progress(
                con,
                run_id,
                i,
                total,
                succeeded,
                failed,
                skipped,
                pages_succeeded,
                analysis_attempts,
                f"Analyzed {candidate.document_id}: {outcome.status}; pages={pages_succeeded}",
            )

        final_status = "SUCCEEDED" if failed == 0 else "COMPLETED_WITH_ERRORS"
        finish_run(
            con,
            run_id,
            final_status,
            total,
            total,
            succeeded,
            failed,
            skipped,
            pages_succeeded,
            analysis_attempts,
            time.monotonic() - started,
        )

        print()
        print(
            f"Run {run_id} {final_status}: "
            f"{succeeded} succeeded, {failed} failed, {skipped} skipped, "
            f"{pages_succeeded} pages, {analysis_attempts} analysis attempt(s)"
        )
        return 0 if failed == 0 else 1

    except KeyboardInterrupt:
        elapsed = time.monotonic() - started
        con.execute(
            "UPDATE runs SET status='INTERRUPTED', finished_at=?, heartbeat_at=?, progress_message=? WHERE id=?",
            (utcnow(), utcnow(), "Interrupted by user", run_id),
        )
        con.commit()
        print("\nInterrupted. Completed analyses remain committed and will be skipped on restart.")
        return 130

    except Exception as exc:
        con.execute(
            "UPDATE runs SET status='FAILED', finished_at=?, heartbeat_at=?, progress_message=? WHERE id=?",
            (utcnow(), utcnow(), str(exc)[:1000], run_id),
        )
        con.commit()
        print(f"FATAL: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1

    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        if credential is not None and hasattr(credential, "close"):
            try:
                credential.close()
            except Exception:
                pass
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
