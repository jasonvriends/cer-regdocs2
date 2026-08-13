#!/usr/bin/env python3
"""Stage 2: download, validate, hash, and version REGDOCS source files.

Syntax::

    python pipeline/regdocs_2_download.py [options]
    python pipeline/regdocs_2_download.py --help

Operational policy, schemas, recovery, and examples are documented in
``pipeline/regdocs_2_download.md``.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import email.utils
import hashlib
import json
import logging
import mimetypes
import os
import random
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import unquote, urlparse

import regdocs_paths

try:
    import httpx
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Missing dependency 'httpx'. Install with: "
        "python -m pip install -r regdocs_atlas/requirements.txt"
    ) from exc

try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover
    class tqdm:  # type: ignore[no-redef]
        def __init__(self, iterable=None, total=None, **_: Any):
            self.iterable = iterable
            self.total = total

        def __iter__(self):
            return iter(self.iterable or [])

        def __enter__(self):
            return self

        def __exit__(self, *_: Any):
            return None

        def update(self, _: int = 1) -> None:
            return None

        def set_postfix(self, **_: Any) -> None:
            return None


SCRIPT_VERSION = "1.1.2"
PARSER_VERSION = "document-ledger-download-2026-08-07-v1.1.1-sidecars-docs"
REQUIRED_ACQUISITION_TABLES = {
    "documents",
    "runs",
    "errors",
    "raw_snapshots",
    "files",
}
RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
HTML_KINDS = {"html document", "html", "web page"}
HTML_EXTENSIONS = {".html", ".htm", ".xhtml"}
PDF_MIME = "application/pdf"
SIDECAR_SCHEMA = "cer-regdocs-document-sidecar"
SIDECAR_SCHEMA_VERSION = 1
SIDECAR_SUFFIX = ".metadata.json"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-CA,en;q=0.9,fr-CA;q=0.7,fr;q=0.6",
}
CONTENT_DISPOSITION_FILENAME_RE = re.compile(
    r"filename\*?=(?:UTF-8''|\")?([^\";]+)", re.IGNORECASE
)
SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


# -----------------------------------------------------------------------------
# Generic helpers
# -----------------------------------------------------------------------------


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def json_dumps(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def parse_json_object(raw: Any) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        return dict(raw)
    if raw in (None, ""):
        return {}
    try:
        parsed = json.loads(str(raw))
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def first_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, (list, tuple, set)):
        for item in value:
            text = first_text(item)
            if text:
                return text
        return default
    if isinstance(value, Mapping):
        return default
    text = str(value).strip()
    return text or default


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def sanitize_filename(value: str) -> str:
    name = Path(unquote(value.strip())).name
    name = SAFE_FILENAME_RE.sub("_", name).strip("._")
    return name[:240]


def parse_content_disposition_filename(value: str | None) -> str:
    if not value:
        return ""
    match = CONTENT_DISPOSITION_FILENAME_RE.search(value)
    return sanitize_filename(match.group(1)) if match else ""


def header_subset(headers: Mapping[str, str]) -> dict[str, str]:
    wanted = {
        "content-type",
        "content-length",
        "content-disposition",
        "etag",
        "last-modified",
        "cache-control",
        "retry-after",
        "server",
    }
    return {key.lower(): value for key, value in headers.items() if key.lower() in wanted}


def is_known_html(kind: str, metadata: Mapping[str, Any]) -> bool:
    extension = first_text(metadata.get("extension")).lower()
    mime = first_text(metadata.get("content_type")).split(";", 1)[0].lower()
    download = parse_json_object(metadata.get("download"))
    extension = extension or first_text(download.get("extension")).lower()
    mime = mime or first_text(download.get("content_type")).split(";", 1)[0].lower()
    return kind.casefold() in HTML_KINDS or extension in HTML_EXTENSIONS or mime in {
        "text/html",
        "application/xhtml+xml",
    }


def user_tables(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def configure_logging(verbose: bool, log_path: Path | None = None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
        force=True,
    )
    if not verbose:
        logging.getLogger("httpx").setLevel(logging.WARNING)


def atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def deterministic_json_bytes(value: Mapping[str, Any]) -> bytes:
    """Serialize a sidecar deterministically so unchanged exports are not rewritten."""
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=str,
        )
        + "\n"
    ).encode("utf-8")


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    with temporary.open("wb") as stream:
        stream.write(payload)
        stream.flush()
        with contextlib.suppress(OSError):
            os.fsync(stream.fileno())
    os.replace(temporary, path)


def is_metadata_sidecar(path: Path) -> bool:
    return path.name.endswith(SIDECAR_SUFFIX)


# -----------------------------------------------------------------------------
# Lock and pacing
# -----------------------------------------------------------------------------


class StageLock:
    def __init__(self, path: Path, *, force: bool = False):
        self.path = path
        self.force = force
        self.owned = False

    def __enter__(self) -> "StageLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.force and self.path.exists():
            self.path.unlink()
        payload = {
            "pid": os.getpid(),
            "created_at": utc_now(),
            "command": " ".join(sys.argv),
        }
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            detail = ""
            with contextlib.suppress(OSError):
                detail = self.path.read_text(encoding="utf-8")
            raise RuntimeError(
                f"Download lock already exists: {self.path}. Confirm no downloader is running "
                "before using --force-lock."
                + (f"\nLock contents: {detail}" if detail else "")
            ) from exc
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2)
            stream.write("\n")
        self.owned = True
        return self

    def __exit__(self, *_: Any) -> None:
        if self.owned:
            with contextlib.suppress(FileNotFoundError):
                self.path.unlink()
        self.owned = False


class RequestPacer:
    """Globally space request starts and honor Retry-After across workers."""

    def __init__(self, min_delay: float, max_delay: float):
        self.min_delay = min_delay
        self.max_delay = max_delay
        self._lock = asyncio.Lock()
        self._next_allowed = 0.0

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            if now < self._next_allowed:
                await asyncio.sleep(self._next_allowed - now)
            self._next_allowed = time.monotonic() + random.uniform(
                self.min_delay, self.max_delay
            )

    async def block_for(self, seconds: float) -> None:
        if seconds <= 0:
            return
        async with self._lock:
            self._next_allowed = max(self._next_allowed, time.monotonic() + seconds)


# -----------------------------------------------------------------------------
# File type detection
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class DetectedType:
    extension: str
    mime_type: str
    method: str
    confidence: float


def _zip_office_type(path: Path) -> DetectedType | None:
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
    except (OSError, zipfile.BadZipFile):
        return None
    if "word/document.xml" in names:
        return DetectedType(
            ".docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "zip-signature",
            1.0,
        )
    if "xl/workbook.xml" in names:
        return DetectedType(
            ".xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "zip-signature",
            1.0,
        )
    if "ppt/presentation.xml" in names:
        return DetectedType(
            ".pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "zip-signature",
            1.0,
        )
    return DetectedType(".zip", "application/zip", "zip-signature", 0.95)


def sniff_file_type(
    path: Path,
    *,
    first_bytes: bytes,
    content_type: str,
    server_filename: str,
    final_url: str,
    catalogue_kind: str,
) -> DetectedType:
    sample = first_bytes.lstrip()
    lowered = sample[:4096].lower()

    if first_bytes.startswith(b"%PDF-"):
        return DetectedType(".pdf", PDF_MIME, "magic", 1.0)
    if first_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return DetectedType(".png", "image/png", "magic", 1.0)
    if first_bytes.startswith(b"\xff\xd8\xff"):
        return DetectedType(".jpg", "image/jpeg", "magic", 1.0)
    if first_bytes.startswith((b"GIF87a", b"GIF89a")):
        return DetectedType(".gif", "image/gif", "magic", 1.0)
    if first_bytes.startswith((b"II*\x00", b"MM\x00*")):
        return DetectedType(".tif", "image/tiff", "magic", 1.0)
    if first_bytes.startswith(b"{\\rtf"):
        return DetectedType(".rtf", "application/rtf", "magic", 1.0)
    if first_bytes.startswith(b"PK\x03\x04"):
        detected = _zip_office_type(path)
        if detected:
            return detected
    if (
        lowered.startswith(b"<!doctype html")
        or lowered.startswith(b"<html")
        or b"<html" in lowered[:1024]
        or b"<body" in lowered[:1024]
    ):
        return DetectedType(".html", "text/html", "magic", 0.99)

    normalized_mime = content_type.split(";", 1)[0].strip().lower()
    mime_extension = mimetypes.guess_extension(normalized_mime) if normalized_mime else None
    if normalized_mime and normalized_mime not in {
        "application/octet-stream",
        "binary/octet-stream",
    }:
        if normalized_mime == "application/xhtml+xml":
            return DetectedType(".xhtml", normalized_mime, "content-type", 0.85)
        return DetectedType(mime_extension or ".bin", normalized_mime, "content-type", 0.80)

    for candidate in (server_filename, Path(urlparse(final_url).path).name):
        suffix = Path(candidate).suffix.lower()
        if suffix:
            guessed = mimetypes.guess_type(candidate)[0] or "application/octet-stream"
            return DetectedType(suffix, guessed, "filename", 0.65)

    kind = catalogue_kind.casefold()
    if "pdf" in kind:
        return DetectedType(".pdf", PDF_MIME, "catalogue-kind", 0.55)
    if "html" in kind:
        return DetectedType(".html", "text/html", "catalogue-kind", 0.55)
    return DetectedType(".bin", "application/octet-stream", "fallback", 0.0)


def validate_download(path: Path, detected: DetectedType) -> None:
    if not path.is_file():
        raise ValueError("Downloaded temporary file is missing")
    if path.stat().st_size <= 0:
        raise ValueError("Downloaded response body is empty")
    if detected.extension == ".pdf":
        with path.open("rb") as stream:
            if stream.read(5) != b"%PDF-":
                raise ValueError("Detected PDF does not begin with %PDF-")


# -----------------------------------------------------------------------------
# Ledger state
# -----------------------------------------------------------------------------


@dataclass
class DownloadCandidate:
    document_id: str
    title: str
    source_url: str
    catalogue_kind: str
    file_path: str
    stored_hash: str
    download_status: str
    retry_count: int
    metadata: dict[str, Any]


@dataclass
class DownloadedFile:
    document_id: str
    source_url: str
    resolved_url: str
    temporary_path: Path
    original_filename: str
    detected: DetectedType
    size_bytes: int
    sha256: str
    status_code: int
    response_headers: dict[str, str]


class LedgerDB:
    def __init__(self, path: Path, *, read_only: bool = False):
        if not path.exists():
            raise FileNotFoundError(f"Pipeline database not found: {path}")
        self.path = path.resolve()
        if read_only:
            uri = f"file:{self.path.as_posix()}?mode=ro"
            self.conn = sqlite3.connect(uri, uri=True)
        else:
            self.conn = sqlite3.connect(str(self.path), timeout=60)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout=60000")
        if not read_only:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.execute("PRAGMA foreign_keys=ON")
        actual = user_tables(self.conn)
        missing = REQUIRED_ACQUISITION_TABLES - actual
        if missing:
            raise RuntimeError(
                "Database is missing required acquisition tables: "
                + ", ".join(sorted(missing))
            )

    def close(self) -> None:
        with contextlib.suppress(sqlite3.Error):
            self.conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        self.conn.close()

    def start_run(self, parameters: Mapping[str, Any], total_units: int) -> int:
        now = utc_now()
        cursor = self.conn.execute(
            """
            INSERT INTO runs (
                stage, status, started_at, parameters_json, summary_json,
                script_version, parser_version, current_phase, heartbeat_at,
                completed_units, total_units, progress_message,
                logical_requests, http_attempts, successful_requests,
                failed_requests, retries
            ) VALUES (
                'download', 'RUNNING', ?, ?, '{}', ?, ?, 'reconcile', ?,
                0, ?, 'Starting downloader', ?, 0, 0, 0, 0
            )
            """,
            (
                now,
                json_dumps(parameters),
                SCRIPT_VERSION,
                PARSER_VERSION,
                now,
                total_units,
                total_units,
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def update_run(
        self,
        run_id: int,
        *,
        phase: str,
        completed: int,
        total: int,
        message: str,
        counters: Mapping[str, int],
        progress_path: Path | None = None,
    ) -> None:
        now = utc_now()
        self.conn.execute(
            """
            UPDATE runs SET
                current_phase=?, heartbeat_at=?, completed_units=?, total_units=?,
                progress_message=?, logical_requests=?, http_attempts=?,
                successful_requests=?, failed_requests=?, retries=?
            WHERE id=?
            """,
            (
                phase,
                now,
                completed,
                total,
                message,
                int(counters.get("selected", total)),
                int(counters.get("http_attempts", 0)),
                int(counters.get("successful_requests", 0)),
                int(counters.get("failed_requests", 0)),
                int(counters.get("retries", 0)),
                run_id,
            ),
        )
        self.conn.commit()
        if progress_path is not None:
            atomic_write_json(
                progress_path,
                {
                    "run_id": run_id,
                    "stage": "download",
                    "status": "RUNNING",
                    "phase": phase,
                    "completed_units": completed,
                    "total_units": total,
                    "progress_message": message,
                    "heartbeat_at": now,
                    "counters": dict(counters),
                },
            )

    def finish_run(
        self,
        run_id: int,
        *,
        status: str,
        summary: Mapping[str, Any],
        counters: Mapping[str, int],
        progress_path: Path | None = None,
    ) -> None:
        now = utc_now()
        self.conn.execute(
            """
            UPDATE runs SET
                status=?, finished_at=?, summary_json=?, current_phase='finished',
                heartbeat_at=?, completed_units=COALESCE(total_units, completed_units),
                progress_message=?, logical_requests=?, http_attempts=?,
                successful_requests=?, failed_requests=?, retries=?
            WHERE id=?
            """,
            (
                status,
                now,
                json_dumps(summary),
                now,
                f"Download run {status.lower()}",
                int(counters.get("selected", 0)),
                int(counters.get("http_attempts", 0)),
                int(counters.get("successful_requests", 0)),
                int(counters.get("failed_requests", 0)),
                int(counters.get("retries", 0)),
                run_id,
            ),
        )
        self.conn.commit()
        if progress_path is not None:
            atomic_write_json(
                progress_path,
                {
                    "run_id": run_id,
                    "stage": "download",
                    "status": status,
                    "finished_at": now,
                    "summary": dict(summary),
                    "counters": dict(counters),
                },
            )

    def add_error(
        self,
        *,
        run_id: int,
        document_id: str | None,
        code: str,
        message: str,
        severity: str = "ERROR",
        retryable: bool = False,
        context: Mapping[str, Any] | None = None,
    ) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO errors (
                run_id, document_id, stage, code, severity, message,
                retryable, context_json, created_at
            ) VALUES (?, ?, 'download', ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                document_id,
                code,
                severity,
                message,
                1 if retryable else 0,
                json_dumps(context or {}),
                utc_now(),
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def resolve_download_errors(self, document_id: str) -> None:
        self.conn.execute(
            """
            UPDATE errors SET resolved_at=?
            WHERE document_id=? AND stage='download' AND resolved_at IS NULL
            """,
            (utc_now(), document_id),
        )

    def normalize_non_files(self) -> int:
        now = utc_now()
        cursor = self.conn.execute(
            """
            UPDATE documents
            SET download_status='NOT_APPLICABLE', updated_at=?
            WHERE is_file=0
              AND download_status IN ('PENDING','RUNNING','SKIPPED_HTML')
            """,
            (now,),
        )
        self.conn.commit()
        return int(cursor.rowcount)

    def mark_html_skipped(self, *, document_ids: Sequence[str]) -> int:
        if not document_ids:
            return 0
        now = utc_now()
        placeholders = ",".join("?" for _ in document_ids)
        cursor = self.conn.execute(
            f"""
            UPDATE documents
            SET download_status='SKIPPED_HTML', updated_at=?
            WHERE id IN ({placeholders})
              AND download_status NOT IN ('SUCCEEDED','RUNNING')
            """,
            (now, *document_ids),
        )
        self.conn.commit()
        return int(cursor.rowcount)

    def all_file_rows(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT id, name, url, item_kind, is_file, metadata, status,
                   download_status, file_path, hash, last_error, retry_count
            FROM documents
            WHERE is_file=1
            ORDER BY CASE WHEN id GLOB '[0-9]*' THEN CAST(id AS INTEGER) END, id
            """
        ).fetchall()

    def sidecar_rows(
        self,
        *,
        document_ids: Sequence[str],
        limit: int | None,
    ) -> list[sqlite3.Row]:
        """Return one authoritative metadata row for each current downloaded file."""
        clauses = ["d.is_file=1", "d.download_status='SUCCEEDED'", "f.is_current=1"]
        params: list[Any] = []
        if document_ids:
            clauses.append(f"d.id IN ({','.join('?' for _ in document_ids)})")
            params.extend(document_ids)
        sql = f"""
            SELECT
                d.id, d.name, d.url, d.item_kind, d.filing_date, d.submitter,
                d.company, d.project, d.filing_number, d.snippet, d.metadata,
                d.status, d.scout_status, d.download_status, d.process_status,
                d.export_status, d.detail_status, d.file_path AS document_file_path,
                d.hash AS document_hash, d.first_seen_at, d.last_seen_at,
                d.created_at, d.updated_at,
                f.path AS current_file_path, f.original_filename, f.mime_type,
                f.extension, f.size_bytes, f.sha256, f.downloaded_at
            FROM documents AS d
            JOIN files AS f ON f.document_id=d.id AND f.is_current=1
            WHERE {' AND '.join(clauses)}
            ORDER BY CASE WHEN d.id GLOB '[0-9]*' THEN CAST(d.id AS INTEGER) END, d.id
        """
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return self.conn.execute(sql, params).fetchall()


    def select_candidates(
        self,
        *,
        include_html: bool,
        force: bool,
        retry_failed: bool,
        document_ids: Sequence[str],
        limit: int | None,
    ) -> tuple[list[DownloadCandidate], list[str]]:
        clauses = ["is_file=1", "trim(COALESCE(url,'')) != ''"]
        params: list[Any] = []
        if document_ids:
            clauses.append(f"id IN ({','.join('?' for _ in document_ids)})")
            params.extend(document_ids)
        rows = self.conn.execute(
            f"""
            SELECT id, name, url, item_kind, metadata, file_path, hash,
                   download_status, retry_count
            FROM documents
            WHERE {' AND '.join(clauses)}
            ORDER BY CASE WHEN id GLOB '[0-9]*' THEN CAST(id AS INTEGER) END, id
            """,
            params,
        ).fetchall()

        selected: list[DownloadCandidate] = []
        html_skipped: list[str] = []
        for row in rows:
            metadata = parse_json_object(row["metadata"])
            kind = first_text(row["item_kind"])
            status = first_text(row["download_status"], "PENDING").upper()
            if not include_html and is_known_html(kind, metadata):
                html_skipped.append(str(row["id"]))
                continue
            if not force:
                if status == "SUCCEEDED":
                    continue
                if status == "FAILED_FINAL" and not retry_failed:
                    continue
                if status == "NOT_APPLICABLE":
                    continue
            selected.append(
                DownloadCandidate(
                    document_id=str(row["id"]),
                    title=first_text(row["name"]),
                    source_url=first_text(row["url"]),
                    catalogue_kind=kind,
                    file_path=first_text(row["file_path"]),
                    stored_hash=first_text(row["hash"]),
                    download_status=status,
                    retry_count=int(row["retry_count"] or 0),
                    metadata=metadata,
                )
            )
            if limit is not None and len(selected) >= limit:
                break
        return selected, html_skipped

    def mark_running(self, candidate: DownloadCandidate) -> None:
        now = utc_now()
        metadata = dict(candidate.metadata)
        block = parse_json_object(metadata.get("download"))
        block.update(
            {
                "status": "RUNNING",
                "source_url": candidate.source_url,
                "last_attempt_started_at": now,
                "script_version": SCRIPT_VERSION,
                "parser_version": PARSER_VERSION,
            }
        )
        metadata["download"] = block
        self.conn.execute(
            """
            UPDATE documents
            SET download_status='RUNNING', metadata=?, last_error=NULL, updated_at=?
            WHERE id=?
            """,
            (json_dumps(metadata), now, candidate.document_id),
        )
        self.conn.commit()

    def relocate_current_file(self, document_id: str, new_path: Path) -> None:
        row = self.conn.execute(
            """
            SELECT id FROM files
            WHERE document_id=? AND is_current=1
            ORDER BY id DESC LIMIT 1
            """,
            (document_id,),
        ).fetchone()
        if row:
            self.conn.execute(
                "UPDATE files SET path=?, is_current=0 WHERE id=?",
                (regdocs_paths.stored_path(new_path), int(row["id"])),
            )
            self.conn.commit()

    def mark_success(
        self,
        *,
        candidate: DownloadCandidate,
        downloaded: DownloadedFile,
        final_path: Path,
        run_id: int,
        attempts: Sequence[Mapping[str, Any]],
        adopted: bool = False,
    ) -> None:
        now = utc_now()
        path_value = regdocs_paths.stored_path(final_path)
        self.conn.execute(
            "UPDATE files SET is_current=0 WHERE document_id=?",
            (candidate.document_id,),
        )
        self.conn.execute(
            """
            INSERT INTO files (
                document_id, path, original_filename, mime_type, extension,
                size_bytes, sha256, downloaded_at, is_current
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(document_id, sha256) DO UPDATE SET
                path=excluded.path,
                original_filename=excluded.original_filename,
                mime_type=excluded.mime_type,
                extension=excluded.extension,
                size_bytes=excluded.size_bytes,
                downloaded_at=excluded.downloaded_at,
                is_current=1
            """,
            (
                candidate.document_id,
                path_value,
                downloaded.original_filename or None,
                downloaded.detected.mime_type,
                downloaded.detected.extension,
                downloaded.size_bytes,
                downloaded.sha256,
                now,
            ),
        )
        metadata = dict(candidate.metadata)
        block = parse_json_object(metadata.get("download"))
        block.update(
            {
                "status": "SUCCEEDED",
                "source_url": candidate.source_url,
                "resolved_url": downloaded.resolved_url,
                "catalogue_kind": candidate.catalogue_kind or None,
                "original_filename": downloaded.original_filename or None,
                "local_filename": final_path.name,
                "local_path": path_value,
                "extension": downloaded.detected.extension,
                "content_type": downloaded.detected.mime_type,
                "size_bytes": downloaded.size_bytes,
                "sha256": downloaded.sha256,
                "etag": downloaded.response_headers.get("etag"),
                "last_modified": downloaded.response_headers.get("last-modified"),
                "content_disposition": downloaded.response_headers.get("content-disposition"),
                "detection_method": downloaded.detected.method,
                "detection_confidence": downloaded.detected.confidence,
                "downloaded_at": now,
                "adopted_existing": adopted,
                "run_id": run_id,
                "attempts_this_run": list(attempts),
                "script_version": SCRIPT_VERSION,
                "parser_version": PARSER_VERSION,
            }
        )
        metadata["download"] = {key: value for key, value in block.items() if value is not None}
        metadata.update(
            {
                "extension": downloaded.detected.extension,
                "content_type": downloaded.detected.mime_type,
                "size_bytes": downloaded.size_bytes,
                "server_filename": downloaded.original_filename or None,
                "resolved_url": downloaded.resolved_url,
                "downloaded_at": now,
            }
        )
        self.conn.execute(
            """
            UPDATE documents
            SET status='DOWNLOADED', download_status='SUCCEEDED', file_path=?,
                hash=?, metadata=?, last_error=NULL, retry_count=0, updated_at=?
            WHERE id=?
            """,
            (
                path_value,
                downloaded.sha256,
                json_dumps(metadata),
                now,
                candidate.document_id,
            ),
        )
        self.resolve_download_errors(candidate.document_id)
        self.conn.commit()

    def mark_failure(
        self,
        *,
        candidate: DownloadCandidate,
        run_id: int,
        code: str,
        message: str,
        retryable: bool,
        attempts: Sequence[Mapping[str, Any]],
        context: Mapping[str, Any],
    ) -> str:
        now = utc_now()
        final_status = "FAILED_RETRYABLE" if retryable else "FAILED_FINAL"
        retry_count = candidate.retry_count + len(attempts)
        metadata = dict(candidate.metadata)
        block = parse_json_object(metadata.get("download"))
        block.update(
            {
                "status": final_status,
                "source_url": candidate.source_url,
                "failed_at": now,
                "error_code": code,
                "error": message,
                "retryable": retryable,
                "run_id": run_id,
                "attempts_this_run": list(attempts),
                "script_version": SCRIPT_VERSION,
                "parser_version": PARSER_VERSION,
            }
        )
        metadata["download"] = block
        self.conn.execute(
            """
            UPDATE documents
            SET status=CASE
                    WHEN status IN ('NEW','FAILED','DOWNLOADED') THEN 'FAILED'
                    ELSE status
                END,
                download_status=?, metadata=?, last_error=?, retry_count=?, updated_at=?
            WHERE id=?
            """,
            (
                final_status,
                json_dumps(metadata),
                message,
                retry_count,
                now,
                candidate.document_id,
            ),
        )
        self.add_error(
            run_id=run_id,
            document_id=candidate.document_id,
            code=code,
            message=message,
            severity="ERROR",
            retryable=retryable,
            context={**dict(context), "attempts": list(attempts)},
        )
        self.conn.commit()
        return final_status

    def reset_missing_recorded_file(
        self,
        *,
        candidate: DownloadCandidate,
        run_id: int,
    ) -> None:
        message = f"Recorded current file is missing: {candidate.file_path or '(empty path)'}"
        self.conn.execute(
            """
            UPDATE files SET is_current=0
            WHERE document_id=? AND is_current=1
            """,
            (candidate.document_id,),
        )
        self.conn.execute(
            """
            UPDATE documents
            SET status=CASE WHEN status='DOWNLOADED' THEN 'NEW' ELSE status END,
                download_status='PENDING', file_path=NULL, hash=NULL,
                last_error=?, updated_at=?
            WHERE id=?
            """,
            (message, utc_now(), candidate.document_id),
        )
        self.add_error(
            run_id=run_id,
            document_id=candidate.document_id,
            code="RECORDED_FILE_MISSING",
            message=message,
            severity="WARNING",
            retryable=True,
            context={"recorded_path": candidate.file_path},
        )
        self.conn.commit()


# -----------------------------------------------------------------------------
# Existing-file reconciliation and filesystem commit
# -----------------------------------------------------------------------------


def scan_download_directory(downloads: Path) -> dict[str, Path]:
    output: dict[str, Path] = {}
    if not downloads.exists():
        return output
    for entry in os.scandir(downloads):
        if (
            entry.name.startswith(".")
            or entry.name == "_versions"
            or entry.name == "_metadata"
            or entry.name.endswith(SIDECAR_SUFFIX)
        ):
            continue
        try:
            if not entry.is_file(follow_symlinks=True):
                continue
        except OSError:
            continue
        path = Path(entry.path)
        doc_id = path.stem
        if doc_id:
            output[doc_id] = path.resolve()
    return output


def existing_candidate_path(
    candidate: DownloadCandidate,
    *,
    db_path: Path,
    indexed: Mapping[str, Path],
) -> Path | None:
    paths: list[Path] = []
    if candidate.file_path:
        paths.append(
            regdocs_paths.resolve_stored_path(
                candidate.file_path,
                legacy_base=db_path.parent,
            )
        )
    indexed_path = indexed.get(candidate.document_id)
    if indexed_path:
        paths.append(indexed_path)
    seen: set[str] = set()
    for path in paths:
        with contextlib.suppress(OSError):
            resolved = path.resolve()
            if str(resolved) in seen:
                continue
            seen.add(str(resolved))
            if resolved.is_file() and resolved.stat().st_size > 0:
                return resolved
    return None


def archive_replaced_file(path: Path, *, document_id: str, archive_root: Path) -> Path:
    digest = sha256_file(path)
    destination = archive_root / document_id / f"{digest}{path.suffix.lower()}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        os.replace(path, destination)
    else:
        path.unlink()
    return destination.resolve()


def commit_download(
    downloaded: DownloadedFile,
    *,
    downloads: Path,
    archive_replaced: bool,
) -> tuple[Path, list[tuple[Path, Path]]]:
    final_path = downloads / f"{downloaded.document_id}{downloaded.detected.extension}"
    archived: list[tuple[Path, Path]] = []
    for current in downloads.glob(f"{downloaded.document_id}.*"):
        if (
            current == downloaded.temporary_path
            or not current.is_file()
            or is_metadata_sidecar(current)
        ):
            continue
        existing_hash = sha256_file(current)
        if current.resolve() == final_path.resolve() and existing_hash == downloaded.sha256:
            downloaded.temporary_path.unlink(missing_ok=True)
            return final_path.resolve(), archived
        old_path = current.resolve()
        if archive_replaced:
            new_path = archive_replaced_file(
                current,
                document_id=downloaded.document_id,
                archive_root=downloads / "_versions",
            )
            archived.append((old_path, new_path))
        else:
            current.unlink()
    final_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(downloaded.temporary_path, final_path)
    except OSError:
        shutil.copyfile(downloaded.temporary_path, final_path)
        downloaded.temporary_path.unlink(missing_ok=True)
    return final_path.resolve(), archived


def clean_stale_partials(partial_dir: Path, max_age_hours: float) -> int:
    if not partial_dir.exists():
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    removed = 0
    for path in partial_dir.glob("*.part"):
        with contextlib.suppress(OSError):
            modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
            if modified < cutoff:
                path.unlink()
                removed += 1
    return removed


# -----------------------------------------------------------------------------
# Optional JSON sidecars
# -----------------------------------------------------------------------------


def sidecar_payload(row: sqlite3.Row) -> dict[str, Any]:
    """Project the authoritative SQLite record into a portable JSON document."""
    metadata = parse_json_object(row["metadata"])
    extension = first_text(row["extension"]).lower()
    content_type = first_text(row["mime_type"])
    return {
        "schema": SIDECAR_SCHEMA,
        "schema_version": SIDECAR_SCHEMA_VERSION,
        "document_id": str(row["id"]),
        "title": first_text(row["name"]),
        "source_url": first_text(row["url"]),
        "item_kind": first_text(row["item_kind"]),
        "filing_date": first_text(row["filing_date"]) or None,
        "submitter": first_text(row["submitter"]) or None,
        "company": first_text(row["company"]) or None,
        "project": first_text(row["project"]) or None,
        "filing_number": first_text(row["filing_number"]) or None,
        "snippet": first_text(row["snippet"]) or None,
        "content_type": content_type or None,
        "extension": extension or None,
        "sha256": first_text(row["sha256"]) or first_text(row["document_hash"]) or None,
        "file": {
            "path": first_text(row["current_file_path"])
            or first_text(row["document_file_path"]),
            "filename": Path(first_text(row["current_file_path"]) or first_text(row["document_file_path"])).name,
            "original_filename": first_text(row["original_filename"]) or None,
            "content_type": content_type or None,
            "extension": extension or None,
            "size_bytes": int(row["size_bytes"]) if row["size_bytes"] is not None else None,
            "sha256": first_text(row["sha256"]) or None,
            "downloaded_at": first_text(row["downloaded_at"]) or None,
        },
        "pipeline": {
            "status": first_text(row["status"]),
            "scout_status": first_text(row["scout_status"]),
            "download_status": first_text(row["download_status"]),
            "process_status": first_text(row["process_status"]),
            "export_status": first_text(row["export_status"]),
            "detail_status": first_text(row["detail_status"]),
            "first_seen_at": first_text(row["first_seen_at"]) or None,
            "last_seen_at": first_text(row["last_seen_at"]) or None,
            "created_at": first_text(row["created_at"]) or None,
            "updated_at": first_text(row["updated_at"]) or None,
        },
        "metadata": metadata,
    }


def sidecar_destination(
    row: sqlite3.Row,
    *,
    db_path: Path,
    sidecar_dir: Path | None,
) -> Path:
    if sidecar_dir is not None:
        return sidecar_dir / f"{row['id']}{SIDECAR_SUFFIX}"
    stored = first_text(row["current_file_path"]) or first_text(row["document_file_path"])
    source_path = regdocs_paths.resolve_stored_path(
        stored,
        legacy_base=db_path.parent,
    )
    return source_path.with_name(f"{row['id']}{SIDECAR_SUFFIX}")


def write_sidecars(
    *,
    rows: Sequence[sqlite3.Row],
    db_path: Path,
    sidecar_dir: Path | None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Write deterministic JSON sidecars and skip files whose bytes are unchanged."""
    summary: dict[str, Any] = {
        "selected": len(rows),
        "written": 0,
        "unchanged": 0,
        "failed": 0,
        "missing_source_files": 0,
        "paths": [],
        "errors": [],
    }
    for row in rows:
        document_id = str(row["id"])
        try:
            stored = first_text(row["current_file_path"]) or first_text(row["document_file_path"])
            source_path = regdocs_paths.resolve_stored_path(
                stored,
                legacy_base=db_path.parent,
            )
            if not source_path.is_file():
                summary["missing_source_files"] += 1
                summary["failed"] += 1
                summary["errors"].append(
                    {"document_id": document_id, "error": f"Current source file is missing: {source_path}"}
                )
                continue
            destination = sidecar_destination(
                row, db_path=db_path, sidecar_dir=sidecar_dir
            ).resolve()
            summary["paths"].append(str(destination))
            if dry_run:
                continue
            payload = deterministic_json_bytes(sidecar_payload(row))
            if destination.is_file() and destination.read_bytes() == payload:
                summary["unchanged"] += 1
                continue
            atomic_write_bytes(destination, payload)
            summary["written"] += 1
        except Exception as exc:
            summary["failed"] += 1
            summary["errors"].append(
                {"document_id": document_id, "error": f"{type(exc).__name__}: {exc}"}
            )
    return summary


# -----------------------------------------------------------------------------
# HTTP engine
# -----------------------------------------------------------------------------


class DownloadFailure(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        code: str,
        status_code: int | None = None,
        final_url: str | None = None,
        response_headers: Mapping[str, str] | None = None,
        bytes_received: int = 0,
        sha256: str | None = None,
        temporary_path: Path | None = None,
    ):
        super().__init__(message)
        self.retryable = retryable
        self.code = code
        self.status_code = status_code
        self.final_url = final_url
        self.response_headers = dict(response_headers or {})
        self.bytes_received = bytes_received
        self.sha256 = sha256
        self.temporary_path = temporary_path


def retry_after_seconds(headers: Mapping[str, str]) -> float | None:
    value = headers.get("retry-after") or headers.get("Retry-After")
    if not value:
        return None
    with contextlib.suppress(ValueError):
        return max(float(value), 0.0)
    with contextlib.suppress(Exception):
        parsed = email.utils.parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max((parsed - datetime.now(timezone.utc)).total_seconds(), 0.0)
    return None


async def download_attempt(
    *,
    client: httpx.AsyncClient,
    candidate: DownloadCandidate,
    partial_dir: Path,
    pacer: RequestPacer,
    max_size_bytes: int,
) -> DownloadedFile:
    await pacer.wait()
    partial_dir.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f"{candidate.document_id}-", suffix=".part", dir=partial_dir
    )
    os.close(fd)
    temp_path = Path(temp_name)
    digest = hashlib.sha256()
    first_bytes = bytearray()
    bytes_received = 0

    try:
        async with client.stream("GET", candidate.source_url) as response:
            headers = header_subset(response.headers)
            status = response.status_code
            final_url = str(response.url)
            if status < 200 or status >= 300:
                raise DownloadFailure(
                    f"HTTP {status} while downloading {candidate.source_url}",
                    retryable=status in RETRYABLE_STATUS_CODES,
                    code="HTTP_ERROR",
                    status_code=status,
                    final_url=final_url,
                    response_headers=headers,
                    temporary_path=temp_path,
                )
            content_length = response.headers.get("content-length")
            if content_length:
                try:
                    declared = int(content_length)
                except ValueError:
                    declared = 0
                if declared > max_size_bytes:
                    raise DownloadFailure(
                        f"Content-Length {declared:,} exceeds limit {max_size_bytes:,}",
                        retryable=False,
                        code="FILE_TOO_LARGE",
                        status_code=status,
                        final_url=final_url,
                        response_headers=headers,
                        temporary_path=temp_path,
                    )
            with temp_path.open("wb") as stream:
                async for chunk in response.aiter_bytes(1024 * 1024):
                    if not chunk:
                        continue
                    bytes_received += len(chunk)
                    if bytes_received > max_size_bytes:
                        raise DownloadFailure(
                            f"Response exceeded limit {max_size_bytes:,} bytes",
                            retryable=False,
                            code="FILE_TOO_LARGE",
                            status_code=status,
                            final_url=final_url,
                            response_headers=headers,
                            bytes_received=bytes_received,
                            temporary_path=temp_path,
                        )
                    digest.update(chunk)
                    if len(first_bytes) < 65536:
                        first_bytes.extend(chunk[: 65536 - len(first_bytes)])
                    stream.write(chunk)
                stream.flush()
                with contextlib.suppress(OSError):
                    os.fsync(stream.fileno())
            server_filename = parse_content_disposition_filename(
                response.headers.get("content-disposition")
            )
            detected = sniff_file_type(
                temp_path,
                first_bytes=bytes(first_bytes),
                content_type=response.headers.get("content-type", ""),
                server_filename=server_filename,
                final_url=final_url,
                catalogue_kind=candidate.catalogue_kind,
            )
            if "pdf" in candidate.catalogue_kind.casefold() and detected.extension != ".pdf":
                raise DownloadFailure(
                    f"Catalogue expected PDF but response detected as "
                    f"{detected.mime_type} ({detected.extension})",
                    retryable=detected.extension in HTML_EXTENSIONS,
                    code="CATALOGUE_TYPE_MISMATCH",
                    status_code=status,
                    final_url=final_url,
                    response_headers=headers,
                    bytes_received=bytes_received,
                    sha256=digest.hexdigest(),
                    temporary_path=temp_path,
                )
            validate_download(temp_path, detected)
            return DownloadedFile(
                document_id=candidate.document_id,
                source_url=candidate.source_url,
                resolved_url=final_url,
                temporary_path=temp_path,
                original_filename=server_filename,
                detected=detected,
                size_bytes=bytes_received,
                sha256=digest.hexdigest(),
                status_code=status,
                response_headers=headers,
            )
    except DownloadFailure:
        raise
    except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
        raise DownloadFailure(
            f"{type(exc).__name__}: {exc}",
            retryable=True,
            code="NETWORK_ERROR",
            bytes_received=bytes_received,
            sha256=digest.hexdigest() if bytes_received else None,
            temporary_path=temp_path,
        ) from exc
    except Exception as exc:
        raise DownloadFailure(
            f"{type(exc).__name__}: {exc}",
            retryable=False,
            code="VALIDATION_ERROR",
            bytes_received=bytes_received,
            sha256=digest.hexdigest() if bytes_received else None,
            temporary_path=temp_path,
        ) from exc


async def process_candidate(
    *,
    client: httpx.AsyncClient,
    db: LedgerDB,
    candidate: DownloadCandidate,
    run_id: int,
    downloads: Path,
    partial_dir: Path,
    pacer: RequestPacer,
    attempts_per_document: int,
    max_size_bytes: int,
    archive_replaced: bool,
    counters: dict[str, int],
) -> None:
    db.mark_running(candidate)
    attempts: list[dict[str, Any]] = []
    last_failure: DownloadFailure | None = None

    for attempt_number in range(1, attempts_per_document + 1):
        if attempt_number > 1:
            counters["retries"] += 1
        started_at = utc_now()
        started_mono = time.monotonic()
        counters["http_attempts"] += 1
        try:
            downloaded = await download_attempt(
                client=client,
                candidate=candidate,
                partial_dir=partial_dir,
                pacer=pacer,
                max_size_bytes=max_size_bytes,
            )
            counters["successful_requests"] += 1
            attempts.append(
                {
                    "attempt": attempt_number,
                    "started_at": started_at,
                    "finished_at": utc_now(),
                    "duration_ms": round((time.monotonic() - started_mono) * 1000, 2),
                    "status_code": downloaded.status_code,
                    "ok": True,
                    "bytes_received": downloaded.size_bytes,
                    "sha256": downloaded.sha256,
                    "resolved_url": downloaded.resolved_url,
                }
            )
            final_path, archived = commit_download(
                downloaded,
                downloads=downloads,
                archive_replaced=archive_replaced,
            )
            for _old_path, new_path in archived:
                db.relocate_current_file(candidate.document_id, new_path)
            db.mark_success(
                candidate=candidate,
                downloaded=downloaded,
                final_path=final_path,
                run_id=run_id,
                attempts=attempts,
            )
            counters["downloaded"] += 1
            if archived:
                counters["archived_versions"] += len(archived)
            logging.info(
                "Downloaded %s -> %s (%s, %s bytes)",
                candidate.document_id,
                final_path.name,
                downloaded.detected.mime_type,
                f"{downloaded.size_bytes:,}",
            )
            return
        except DownloadFailure as failure:
            last_failure = failure
            counters["failed_requests"] += 1
            retry_after = retry_after_seconds(failure.response_headers)
            attempts.append(
                {
                    "attempt": attempt_number,
                    "started_at": started_at,
                    "finished_at": utc_now(),
                    "duration_ms": round((time.monotonic() - started_mono) * 1000, 2),
                    "status_code": failure.status_code,
                    "ok": False,
                    "retryable": failure.retryable,
                    "code": failure.code,
                    "error": str(failure),
                    "bytes_received": failure.bytes_received,
                    "resolved_url": failure.final_url,
                    "retry_after_seconds": retry_after,
                }
            )
            if failure.temporary_path is not None:
                failure.temporary_path.unlink(missing_ok=True)
            if not failure.retryable or attempt_number >= attempts_per_document:
                break
            delay = retry_after if retry_after is not None else min(60.0, 2 ** attempt_number)
            await pacer.block_for(delay)
            logging.warning(
                "Retrying %s after %ss: %s",
                candidate.document_id,
                round(delay, 1),
                failure,
            )

    assert last_failure is not None
    final_retryable = last_failure.retryable
    status = db.mark_failure(
        candidate=candidate,
        run_id=run_id,
        code=last_failure.code,
        message=str(last_failure),
        retryable=final_retryable,
        attempts=attempts,
        context={
            "source_url": candidate.source_url,
            "status_code": last_failure.status_code,
            "resolved_url": last_failure.final_url,
            "response_headers": last_failure.response_headers,
            "bytes_received": last_failure.bytes_received,
            "sha256": last_failure.sha256,
        },
    )
    counters["failed"] += 1
    if status == "FAILED_RETRYABLE":
        counters["retryable_failed"] += 1
    else:
        counters["final_failed"] += 1
    logging.error("Failed %s: %s", candidate.document_id, last_failure)


async def run_worker_pool(
    *,
    candidates: Sequence[DownloadCandidate],
    db: LedgerDB,
    run_id: int,
    args: argparse.Namespace,
    downloads: Path,
    progress_path: Path,
    counters: dict[str, int],
) -> None:
    queue: asyncio.Queue[DownloadCandidate | None] = asyncio.Queue()
    for candidate in candidates:
        queue.put_nowait(candidate)
    for _ in range(args.concurrency):
        queue.put_nowait(None)

    pacer = RequestPacer(args.min_delay, args.max_delay)
    timeout = httpx.Timeout(
        connect=args.connect_timeout,
        read=args.read_timeout,
        write=args.read_timeout,
        pool=args.connect_timeout,
    )
    limits = httpx.Limits(
        max_connections=max(args.concurrency, 1),
        max_keepalive_connections=max(args.concurrency, 1),
    )
    partial_dir = downloads / ".partial"
    progress_lock = asyncio.Lock()

    async with httpx.AsyncClient(
        headers=HEADERS,
        follow_redirects=True,
        timeout=timeout,
        limits=limits,
    ) as client:
        with tqdm(total=len(candidates), desc="Downloading", unit=" file") as progress:
            async def worker() -> None:
                while True:
                    candidate = await queue.get()
                    try:
                        if candidate is None:
                            return
                        await process_candidate(
                            client=client,
                            db=db,
                            candidate=candidate,
                            run_id=run_id,
                            downloads=downloads,
                            partial_dir=partial_dir,
                            pacer=pacer,
                            attempts_per_document=args.attempts,
                            max_size_bytes=int(args.max_file_size_mb * 1024 * 1024),
                            archive_replaced=args.archive_replaced,
                            counters=counters,
                        )
                        async with progress_lock:
                            counters["completed"] += 1
                            progress.update(1)
                            progress.set_postfix(
                                downloaded=counters["downloaded"],
                                failed=counters["failed"],
                            )
                            db.update_run(
                                run_id,
                                phase="download",
                                completed=counters["completed"],
                                total=len(candidates),
                                message=(
                                    f"Processed {counters['completed']}/{len(candidates)}; "
                                    f"downloaded={counters['downloaded']} failed={counters['failed']}"
                                ),
                                counters=counters,
                                progress_path=progress_path,
                            )
                    finally:
                        queue.task_done()

            workers = [
                asyncio.create_task(worker(), name=f"regdocs-download-{index + 1}")
                for index in range(args.concurrency)
            ]
            await queue.join()
            await asyncio.gather(*workers)


# -----------------------------------------------------------------------------
# Reconciliation
# -----------------------------------------------------------------------------


def candidate_from_row(row: sqlite3.Row) -> DownloadCandidate:
    return DownloadCandidate(
        document_id=str(row["id"]),
        title=first_text(row["name"]),
        source_url=first_text(row["url"]),
        catalogue_kind=first_text(row["item_kind"]),
        file_path=first_text(row["file_path"]),
        stored_hash=first_text(row["hash"]),
        download_status=first_text(row["download_status"], "PENDING").upper(),
        retry_count=int(row["retry_count"] or 0),
        metadata=parse_json_object(row["metadata"]),
    )


def adopt_existing(
    *,
    db: LedgerDB,
    candidate: DownloadCandidate,
    path: Path,
    run_id: int,
    verify_hash: bool,
) -> None:
    with path.open("rb") as stream:
        first_bytes = stream.read(65536)
    detected = sniff_file_type(
        path,
        first_bytes=first_bytes,
        content_type=mimetypes.guess_type(path.name)[0] or "",
        server_filename=path.name,
        final_url=candidate.source_url,
        catalogue_kind=candidate.catalogue_kind,
    )
    validate_download(path, detected)
    file_hash = sha256_file(path) if verify_hash or not candidate.stored_hash else candidate.stored_hash
    downloaded = DownloadedFile(
        document_id=candidate.document_id,
        source_url=candidate.source_url,
        resolved_url=candidate.source_url,
        temporary_path=path,
        original_filename=path.name,
        detected=detected,
        size_bytes=path.stat().st_size,
        sha256=file_hash,
        status_code=200,
        response_headers={},
    )
    db.mark_success(
        candidate=candidate,
        downloaded=downloaded,
        final_path=path.resolve(),
        run_id=run_id,
        attempts=[],
        adopted=True,
    )


def reconcile_existing(
    *,
    db: LedgerDB,
    downloads: Path,
    run_id: int,
    include_html: bool,
    verify_existing: bool,
    progress_path: Path,
    counters: dict[str, int],
) -> dict[str, int]:
    result = {
        "adopted": 0,
        "valid": 0,
        "missing_reset": 0,
        "invalid": 0,
        "html_skipped": 0,
    }
    indexed = scan_download_directory(downloads)
    rows = db.all_file_rows()
    total = len(rows)
    for index, row in enumerate(tqdm(rows, total=total, desc="Reconciling", unit=" record"), 1):
        candidate = candidate_from_row(row)
        if not include_html and is_known_html(candidate.catalogue_kind, candidate.metadata):
            result["html_skipped"] += 1
            continue
        path = existing_candidate_path(candidate, db_path=db.path, indexed=indexed)
        if path is None:
            if candidate.file_path or candidate.download_status == "SUCCEEDED":
                result["missing_reset"] += 1
                db.reset_missing_recorded_file(candidate=candidate, run_id=run_id)
            continue
        try:
            with path.open("rb") as stream:
                first_bytes = stream.read(65536)
            detected = sniff_file_type(
                path,
                first_bytes=first_bytes,
                content_type=mimetypes.guess_type(path.name)[0] or "",
                server_filename=path.name,
                final_url=candidate.source_url,
                catalogue_kind=candidate.catalogue_kind,
            )
            validate_download(path, detected)
            if "pdf" in candidate.catalogue_kind.casefold() and detected.extension != ".pdf":
                raise ValueError(
                    f"Catalogue expected PDF but existing file detected as {detected.mime_type}"
                )
            actual_hash = sha256_file(path) if verify_existing or not candidate.stored_hash else candidate.stored_hash
            stored_resolved = (
                regdocs_paths.resolve_stored_path(
                    candidate.file_path,
                    legacy_base=db.path.parent,
                )
                if candidate.file_path
                else None
            )
            is_valid = (
                candidate.download_status == "SUCCEEDED"
                and stored_resolved is not None
                and stored_resolved == path.resolve()
                and candidate.stored_hash == actual_hash
            )
            if is_valid:
                result["valid"] += 1
            else:
                adopt_existing(
                    db=db,
                    candidate=candidate,
                    path=path,
                    run_id=run_id,
                    verify_hash=verify_existing,
                )
                result["adopted"] += 1
        except Exception as exc:
            result["invalid"] += 1
            db.add_error(
                run_id=run_id,
                document_id=candidate.document_id,
                code="EXISTING_FILE_INVALID",
                message=f"{type(exc).__name__}: {exc}",
                severity="ERROR",
                retryable=True,
                context={"path": str(path)},
            )
        if index % 25 == 0 or index == total:
            db.update_run(
                run_id,
                phase="reconcile",
                completed=0,
                total=int(counters.get("selected", 0)),
                message=f"Reconciled {index}/{total} file records",
                counters=counters,
                progress_path=progress_path,
            )
    return result


# -----------------------------------------------------------------------------
# CLI/status/self-test
# -----------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download and verify REGDOCS files using the pipeline ledger.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--db",
        default=regdocs_paths.stored_path(regdocs_paths.DATABASE_PATH),
        help="Pipeline SQLite database",
    )
    parser.add_argument(
        "--downloads",
        "--output-dir",
        dest="downloads",
        default=regdocs_paths.stored_path(regdocs_paths.DOWNLOAD_FILES_DIR),
        help="Current source-file directory",
    )
    parser.add_argument(
        "--document-id",
        action="append",
        default=[],
        help="Download only this ID; repeat for multiple IDs",
    )
    parser.add_argument("--limit", type=int, help="Process at most N selected records")
    parser.add_argument("--include-html", action="store_true", help="Download known HTML files")
    parser.add_argument("--force", action="store_true", help="Redownload successful/current records")
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Retry records marked FAILED_FINAL",
    )
    parser.add_argument(
        "--attempts",
        type=int,
        default=4,
        help="Maximum HTTP attempts per selected document in this run",
    )
    parser.add_argument("--concurrency", type=int, default=1, help="Maximum active downloads")
    parser.add_argument("--min-delay", type=float, default=3.0, help="Minimum global request-start delay")
    parser.add_argument("--max-delay", type=float, default=6.0, help="Maximum global request-start delay")
    parser.add_argument("--connect-timeout", type=float, default=30.0)
    parser.add_argument("--read-timeout", type=float, default=300.0)
    parser.add_argument("--max-file-size-mb", type=float, default=2048.0)
    parser.add_argument(
        "--reconcile",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Adopt existing files and reset missing recorded paths",
    )
    parser.add_argument(
        "--verify-existing",
        action="store_true",
        help="Re-hash existing files during reconciliation",
    )
    parser.add_argument(
        "--archive-replaced",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Archive replaced versions under the source-file directory's _versions folder",
    )
    parser.add_argument(
        "--sidecars",
        "--write-sidecars",
        dest="write_sidecars",
        action="store_true",
        help=(
            "Write or refresh deterministic <document-id>.metadata.json files "
            "for current downloads after reconciliation and downloading"
        ),
    )
    parser.add_argument(
        "--sidecars-only",
        action="store_true",
        help="Generate JSON sidecars from SQLite and current files without network downloads",
    )
    parser.add_argument(
        "--sidecar-dir",
        help="Optional sidecar directory; default is beside each current source file",
    )
    parser.add_argument(
        "--partial-max-age-hours",
        type=float,
        default=24.0,
        help="Delete stale .partial files older than this",
    )
    parser.add_argument(
        "--audit-dir",
        default=regdocs_paths.stored_path(regdocs_paths.DOWNLOAD_RUN_DIR),
        help="Progress and log directory",
    )
    parser.add_argument(
        "--lock-file",
        default=regdocs_paths.stored_path(regdocs_paths.DOWNLOAD_LOCK_PATH),
        help="Exclusive downloader lock file",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview selected records without writes")
    parser.add_argument("--status", action="store_true", help="Show latest download run and counts")
    parser.add_argument("--status-json", action="store_true", help="Show latest status as JSON")
    parser.add_argument("--version", action="store_true", help="Print script version and exit")
    parser.add_argument("--self-test", action="store_true", help="Run offline tests and exit")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--force-lock", action="store_true")
    return parser


def validate_args(args: argparse.Namespace) -> None:
    if args.concurrency < 1:
        raise ValueError("--concurrency must be at least 1")
    if args.min_delay < 0 or args.max_delay < 0 or args.max_delay < args.min_delay:
        raise ValueError("Require 0 <= --min-delay <= --max-delay")
    if args.attempts < 1:
        raise ValueError("--attempts must be at least 1")
    if args.limit is not None and args.limit < 0:
        raise ValueError("--limit cannot be negative")
    if args.max_file_size_mb <= 0:
        raise ValueError("--max-file-size-mb must be positive")
    if args.partial_max_age_hours < 0:
        raise ValueError("--partial-max-age-hours cannot be negative")
    if args.sidecars_only and args.force:
        raise ValueError("--sidecars-only does not perform downloads; omit --force")
    if args.sidecars_only and args.retry_failed:
        raise ValueError("--sidecars-only does not retry downloads; omit --retry-failed")


def status_payload(db_path: Path) -> dict[str, Any]:
    db = LedgerDB(db_path, read_only=True)
    try:
        run = db.conn.execute(
            """
            SELECT id, status, started_at, finished_at, current_phase,
                   heartbeat_at, completed_units, total_units, progress_message,
                   logical_requests, http_attempts, successful_requests,
                   failed_requests, retries, summary_json
            FROM runs
            WHERE stage='download'
            ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
        counts = {
            str(row["download_status"]): int(row["records"])
            for row in db.conn.execute(
                """
                SELECT download_status, COUNT(*) AS records
                FROM documents
                GROUP BY download_status
                ORDER BY download_status
                """
            )
        }
        current_files = db.conn.execute(
            "SELECT COUNT(*) FROM files WHERE is_current=1"
        ).fetchone()[0]
        unresolved = db.conn.execute(
            "SELECT COUNT(*) FROM errors WHERE stage='download' AND resolved_at IS NULL"
        ).fetchone()[0]
        return {
            "run": dict(run) if run else None,
            "download_status_counts": counts,
            "current_files": int(current_files),
            "unresolved_download_errors": int(unresolved),
        }
    finally:
        db.close()


def print_status(db_path: Path, *, as_json: bool) -> None:
    payload = status_payload(db_path)
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return
    run = payload["run"]
    if run:
        total = int(run.get("total_units") or 0)
        done = int(run.get("completed_units") or 0)
        percent = round(done * 100.0 / total, 1) if total else 0.0
        print(
            f"download run {run['id']}: {run['status']} | phase={run['current_phase']} | "
            f"{done}/{total} ({percent}%)"
        )
        print(f"heartbeat: {run['heartbeat_at']} | {run['progress_message'] or ''}")
        print(
            f"HTTP attempts={run['http_attempts']} success={run['successful_requests']} "
            f"failed={run['failed_requests']} retries={run['retries']}"
        )
    else:
        print("No download run found.")
    print("download statuses:")
    for status, count in payload["download_status_counts"].items():
        print(f"  {status:<18} {count}")
    print(f"current files: {payload['current_files']}")
    print(f"unresolved download errors: {payload['unresolved_download_errors']}")


TEST_SCHEMA_SQL = """
CREATE TABLE documents (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL, item_kind TEXT,
    is_file INTEGER NOT NULL DEFAULT 0, filing_date TEXT, submitter TEXT,
    company TEXT, project TEXT, filing_number TEXT, snippet TEXT,
    metadata JSON NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'NEW',
    scout_status TEXT NOT NULL DEFAULT 'PENDING',
    download_status TEXT NOT NULL DEFAULT 'PENDING',
    process_status TEXT NOT NULL DEFAULT 'PENDING',
    export_status TEXT NOT NULL DEFAULT 'PENDING',
    detail_status TEXT NOT NULL DEFAULT 'PENDING', detail_last_attempt_at TEXT,
    detail_succeeded_at TEXT, detail_snapshot_id INTEGER, file_path TEXT,
    hash TEXT, last_error TEXT, retry_count INTEGER NOT NULL DEFAULT 0,
    first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, stage TEXT NOT NULL, status TEXT NOT NULL,
    started_at TEXT NOT NULL, finished_at TEXT, parameters_json TEXT NOT NULL DEFAULT '{}',
    summary_json TEXT NOT NULL DEFAULT '{}', script_version TEXT NOT NULL,
    parser_version TEXT NOT NULL, current_phase TEXT, heartbeat_at TEXT,
    completed_units INTEGER NOT NULL DEFAULT 0, total_units INTEGER,
    progress_message TEXT, logical_requests INTEGER NOT NULL DEFAULT 0,
    http_attempts INTEGER NOT NULL DEFAULT 0, successful_requests INTEGER NOT NULL DEFAULT 0,
    failed_requests INTEGER NOT NULL DEFAULT 0, retries INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, document_id TEXT,
    stage TEXT NOT NULL, code TEXT NOT NULL, severity TEXT NOT NULL,
    message TEXT NOT NULL, retryable INTEGER NOT NULL DEFAULT 0,
    context_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
    resolved_at TEXT
);
CREATE TABLE raw_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, document_id TEXT,
    source_kind TEXT NOT NULL, source_url TEXT NOT NULL, final_url TEXT,
    fetched_at TEXT NOT NULL, http_status INTEGER, content_type TEXT,
    content_sha256 TEXT NOT NULL, size_bytes INTEGER NOT NULL,
    compressed_size_bytes INTEGER NOT NULL, relative_path TEXT NOT NULL,
    response_headers_json TEXT NOT NULL DEFAULT '{}', parser_version TEXT NOT NULL,
    UNIQUE(source_kind, source_url, content_sha256)
);
CREATE TABLE files (
    id INTEGER PRIMARY KEY AUTOINCREMENT, document_id TEXT NOT NULL, path TEXT NOT NULL,
    original_filename TEXT, mime_type TEXT, extension TEXT, size_bytes INTEGER,
    sha256 TEXT NOT NULL, downloaded_at TEXT NOT NULL, is_current INTEGER NOT NULL DEFAULT 1,
    UNIQUE(document_id, sha256)
);
"""


def run_self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="regdocs-download-test-") as temp_name:
        root = Path(temp_name)
        db_path = root / "regdocs.db"
        downloads = root / "downloads"
        downloads.mkdir()
        now = utc_now()
        conn = sqlite3.connect(db_path)
        conn.executescript(TEST_SCHEMA_SQL)
        conn.executescript(
            """
            CREATE TABLE analyses (id INTEGER PRIMARY KEY);
            CREATE TABLE normalizations (id INTEGER PRIMARY KEY);
            """
        )
        rows = [
            ("1", "PDF one", "https://apps.cer-rec.gc.ca/REGDOCS/File/Download/1", "PDF Document", 1),
            ("2", "HTML two", "https://apps.cer-rec.gc.ca/REGDOCS/File/Download/2", "Html Document", 1),
            ("3", "Folder", "https://apps.cer-rec.gc.ca/REGDOCS/Item/View/3", "Folder", 0),
            ("paper:4:A1", "Paper", "https://apps.cer-rec.gc.ca/REGDOCS/Item/View/40940", "Paper Only", 0),
        ]
        conn.executemany(
            """
            INSERT INTO documents (
                id,name,url,item_kind,is_file,first_seen_at,last_seen_at,created_at,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            [(a, b, c, d, e, now, now, now, now) for a, b, c, d, e in rows],
        )
        conn.commit()
        conn.close()

        db = LedgerDB(db_path)
        try:
            selected, skipped = db.select_candidates(
                include_html=False,
                force=False,
                retry_failed=False,
                document_ids=[],
                limit=None,
            )
            assert [item.document_id for item in selected] == ["1"]
            assert skipped == ["2"]
            selected_all, _ = db.select_candidates(
                include_html=True,
                force=False,
                retry_failed=False,
                document_ids=[],
                limit=None,
            )
            assert [item.document_id for item in selected_all] == ["1", "2"]
            assert db.normalize_non_files() == 2

            pdf_path = downloads / "1.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\n% test\n")
            candidate = selected[0]
            adopt_existing(
                db=db,
                candidate=candidate,
                path=pdf_path,
                run_id=db.start_run({"self_test": True}, 1),
                verify_hash=True,
            )
            row = db.conn.execute(
                "SELECT download_status, file_path, hash FROM documents WHERE id='1'"
            ).fetchone()
            assert row["download_status"] == "SUCCEEDED"
            assert row["hash"] == sha256_file(pdf_path)
            assert db.conn.execute(
                "SELECT COUNT(*) FROM files WHERE document_id='1' AND is_current=1"
            ).fetchone()[0] == 1

            html_path = root / "sample.part"
            html_path.write_bytes(b"<!doctype html><html><body>ok</body></html>")
            detected = sniff_file_type(
                html_path,
                first_bytes=html_path.read_bytes(),
                content_type="application/octet-stream",
                server_filename="",
                final_url="https://example.invalid/file",
                catalogue_kind="",
            )
            assert detected.extension == ".html"
            assert parse_content_disposition_filename(
                "attachment; filename*=UTF-8''sample%20file.pdf"
            ) == "sample_file.pdf"

            sidecar_rows = db.sidecar_rows(document_ids=["1"], limit=None)
            assert len(sidecar_rows) == 1
            sidecars = write_sidecars(
                rows=sidecar_rows, db_path=db.path, sidecar_dir=None
            )
            assert sidecars["written"] == 1 and sidecars["failed"] == 0
            sidecar_path = downloads / f"1{SIDECAR_SUFFIX}"
            payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
            assert payload["document_id"] == "1"
            assert payload["file"]["sha256"] == sha256_file(pdf_path)
            sidecars_again = write_sidecars(
                rows=sidecar_rows, db_path=db.path, sidecar_dir=None
            )
            assert sidecars_again["unchanged"] == 1

            replacement_part = downloads / ".partial" / "1-replacement.part"
            replacement_part.parent.mkdir(parents=True, exist_ok=True)
            replacement_part.write_bytes(b"%PDF-1.5\n% replacement\n")
            replacement = DownloadedFile(
                document_id="1",
                source_url=candidate.source_url,
                resolved_url=candidate.source_url,
                temporary_path=replacement_part,
                original_filename="replacement.pdf",
                detected=DetectedType(".pdf", PDF_MIME, "magic", 1.0),
                size_bytes=replacement_part.stat().st_size,
                sha256=sha256_file(replacement_part),
                status_code=200,
                response_headers={},
            )
            committed, _ = commit_download(
                replacement, downloads=downloads, archive_replaced=True
            )
            assert committed.is_file()
            assert sidecar_path.is_file()
            assert scan_download_directory(downloads) == {"1": committed.resolve()}
        finally:
            db.close()
    print("Self-test passed: schema, selection, reconciliation, file facts, sidecars, and type detection.")


def preview(candidates: Sequence[DownloadCandidate], html_skipped: Sequence[str]) -> None:
    print(f"Selected {len(candidates)} file(s). Known HTML skipped: {len(html_skipped)}")
    for candidate in candidates:
        print(
            f"{candidate.document_id}\t{candidate.catalogue_kind or 'UNKNOWN'}\t"
            f"{candidate.download_status}\t{candidate.title[:100]}"
        )


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        validate_args(args)
        if args.version:
            print(SCRIPT_VERSION)
            return 0
        if args.self_test:
            run_self_test()
            return 0

        db_path = regdocs_paths.resolve_stored_path(args.db)
        if args.status or args.status_json:
            print_status(db_path, as_json=args.status_json)
            return 0

        downloads = regdocs_paths.resolve_stored_path(args.downloads)
        audit_dir = regdocs_paths.resolve_stored_path(args.audit_dir)
        lock_path = regdocs_paths.resolve_stored_path(args.lock_file)
        sidecar_dir: Path | None = None
        if args.sidecar_dir:
            sidecar_dir = regdocs_paths.resolve_stored_path(args.sidecar_dir)
        progress_path = audit_dir / regdocs_paths.DOWNLOAD_PROGRESS_PATH.name
        log_path = audit_dir / regdocs_paths.DOWNLOAD_LOG_PATH.name
        configure_logging(args.verbose, None if args.dry_run else log_path)

        if args.sidecars_only:
            with StageLock(lock_path, force=args.force_lock):
                db = LedgerDB(db_path, read_only=True)
                try:
                    rows = db.sidecar_rows(
                        document_ids=args.document_id,
                        limit=args.limit,
                    )
                    sidecar_summary = write_sidecars(
                        rows=rows,
                        db_path=db.path,
                        sidecar_dir=sidecar_dir,
                        dry_run=args.dry_run,
                    )
                    print(json.dumps(sidecar_summary, ensure_ascii=False, indent=2))
                    return 0 if sidecar_summary["failed"] == 0 else 2
                finally:
                    db.close()

        if args.dry_run:
            db = LedgerDB(db_path, read_only=True)
            try:
                candidates, html_skipped = db.select_candidates(
                    include_html=args.include_html,
                    force=args.force,
                    retry_failed=args.retry_failed,
                    document_ids=args.document_id,
                    limit=args.limit,
                )
                preview(candidates, html_skipped)
            finally:
                db.close()
            return 0

        downloads.mkdir(parents=True, exist_ok=True)
        removed_partials = clean_stale_partials(
            downloads / ".partial", args.partial_max_age_hours
        )
        if removed_partials:
            logging.info("Removed %d stale partial file(s)", removed_partials)

        with StageLock(lock_path, force=args.force_lock):
            db = LedgerDB(db_path)
            run_id: int | None = None
            counters: dict[str, int] = {
                "selected": 0,
                "completed": 0,
                "downloaded": 0,
                "failed": 0,
                "retryable_failed": 0,
                "final_failed": 0,
                "http_attempts": 0,
                "successful_requests": 0,
                "failed_requests": 0,
                "retries": 0,
                "archived_versions": 0,
                "html_skipped": 0,
                "non_files_normalized": 0,
                "sidecars_written": 0,
                "sidecars_unchanged": 0,
                "sidecars_failed": 0,
            }
            try:
                counters["non_files_normalized"] = db.normalize_non_files()
                preselected, html_skipped = db.select_candidates(
                    include_html=args.include_html,
                    force=args.force,
                    retry_failed=args.retry_failed,
                    document_ids=args.document_id,
                    limit=args.limit,
                )
                counters["html_skipped"] = len(html_skipped)
                if html_skipped:
                    db.mark_html_skipped(document_ids=html_skipped)
                parameters = {
                    "db": regdocs_paths.stored_path(db_path),
                    "downloads": regdocs_paths.stored_path(downloads),
                    "run_dir": regdocs_paths.stored_path(audit_dir),
                    "lock_file": regdocs_paths.stored_path(lock_path),
                    "document_ids": list(args.document_id),
                    "limit": args.limit,
                    "include_html": args.include_html,
                    "force": args.force,
                    "retry_failed": args.retry_failed,
                    "attempts": args.attempts,
                    "concurrency": args.concurrency,
                    "min_delay": args.min_delay,
                    "max_delay": args.max_delay,
                    "max_file_size_mb": args.max_file_size_mb,
                    "reconcile": args.reconcile,
                    "verify_existing": args.verify_existing,
                    "archive_replaced": args.archive_replaced,
                    "write_sidecars": args.write_sidecars,
                    "sidecar_dir": (
                        regdocs_paths.stored_path(sidecar_dir) if sidecar_dir else None
                    ),
                }
                run_id = db.start_run(parameters, len(preselected))
                reconciliation = {
                    "adopted": 0,
                    "valid": 0,
                    "missing_reset": 0,
                    "invalid": 0,
                    "html_skipped": 0,
                }
                if args.reconcile:
                    reconciliation = reconcile_existing(
                        db=db,
                        downloads=downloads,
                        run_id=run_id,
                        include_html=args.include_html,
                        verify_existing=args.verify_existing,
                        progress_path=progress_path,
                        counters=counters,
                    )

                candidates, html_skipped_after = db.select_candidates(
                    include_html=args.include_html,
                    force=args.force,
                    retry_failed=args.retry_failed,
                    document_ids=args.document_id,
                    limit=args.limit,
                )
                counters["selected"] = len(candidates)
                counters["html_skipped"] = max(
                    counters["html_skipped"], len(html_skipped_after)
                )
                db.update_run(
                    run_id,
                    phase="download",
                    completed=0,
                    total=len(candidates),
                    message=f"Selected {len(candidates)} file(s)",
                    counters=counters,
                    progress_path=progress_path,
                )
                logging.info(
                    "Selected %d file(s); HTML skipped=%d; reconciled adopted=%d valid=%d",
                    len(candidates),
                    counters["html_skipped"],
                    reconciliation["adopted"],
                    reconciliation["valid"],
                )
                if candidates:
                    asyncio.run(
                        run_worker_pool(
                            candidates=candidates,
                            db=db,
                            run_id=run_id,
                            args=args,
                            downloads=downloads,
                            progress_path=progress_path,
                            counters=counters,
                        )
                    )
                sidecar_summary: dict[str, Any] = {
                    "selected": 0,
                    "written": 0,
                    "unchanged": 0,
                    "failed": 0,
                    "missing_source_files": 0,
                    "paths": [],
                    "errors": [],
                }
                if args.write_sidecars:
                    sidecar_rows = db.sidecar_rows(
                        document_ids=args.document_id,
                        limit=args.limit,
                    )
                    sidecar_summary = write_sidecars(
                        rows=sidecar_rows,
                        db_path=db.path,
                        sidecar_dir=sidecar_dir,
                    )
                    counters["sidecars_written"] = int(sidecar_summary["written"])
                    counters["sidecars_unchanged"] = int(sidecar_summary["unchanged"])
                    counters["sidecars_failed"] = int(sidecar_summary["failed"])
                    logging.info(
                        "Sidecars selected=%d written=%d unchanged=%d failed=%d",
                        sidecar_summary["selected"],
                        sidecar_summary["written"],
                        sidecar_summary["unchanged"],
                        sidecar_summary["failed"],
                    )

                summary = {
                    **counters,
                    "sidecars": sidecar_summary,
                    "reconciliation": reconciliation,
                    "current_files": int(
                        db.conn.execute(
                            "SELECT COUNT(*) FROM files WHERE is_current=1"
                        ).fetchone()[0]
                    ),
                    "unresolved_download_errors": int(
                        db.conn.execute(
                            """
                            SELECT COUNT(*) FROM errors
                            WHERE stage='download' AND resolved_at IS NULL
                            """
                        ).fetchone()[0]
                    ),
                    "downloads_directory": regdocs_paths.stored_path(downloads),
                }
                status = (
                    "SUCCEEDED"
                    if counters["failed"] == 0 and counters["sidecars_failed"] == 0
                    else "PARTIAL"
                )
                db.finish_run(
                    run_id,
                    status=status,
                    summary=summary,
                    counters=counters,
                    progress_path=progress_path,
                )
                return (
                    0
                    if counters["failed"] == 0 and counters["sidecars_failed"] == 0
                    else 2
                )
            except KeyboardInterrupt:
                if run_id is not None:
                    db.finish_run(
                        run_id,
                        status="INTERRUPTED",
                        summary={**counters, "reason": "KeyboardInterrupt"},
                        counters=counters,
                        progress_path=progress_path,
                    )
                return 130
            except Exception:
                if run_id is not None:
                    with contextlib.suppress(Exception):
                        db.finish_run(
                            run_id,
                            status="FAILED",
                            summary={**counters, "reason": "fatal_exception"},
                            counters=counters,
                            progress_path=progress_path,
                        )
                raise
            finally:
                db.close()
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
