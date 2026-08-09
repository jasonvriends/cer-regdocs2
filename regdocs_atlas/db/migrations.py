"""Central, idempotent SQLite schema migrations.

The migration registry is independent of PRAGMA user_version because the legacy
Scout implementation historically owns that pragma. Existing ledgers can be
adopted: each migration first makes the expected shape true, then records itself.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from .connection import column_names, table_exists

MigrationFn = Callable[[sqlite3.Connection, str], None]


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


BASE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    item_kind TEXT,
    is_file INTEGER NOT NULL DEFAULT 0,
    filing_date TEXT,
    submitter TEXT,
    company TEXT,
    project TEXT,
    filing_number TEXT,
    snippet TEXT,
    metadata JSON NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'NEW',
    scout_status TEXT NOT NULL DEFAULT 'PENDING',
    download_status TEXT NOT NULL DEFAULT 'PENDING',
    process_status TEXT NOT NULL DEFAULT 'PENDING',
    export_status TEXT NOT NULL DEFAULT 'PENDING',
    detail_status TEXT NOT NULL DEFAULT 'PENDING',
    detail_last_attempt_at TEXT,
    detail_succeeded_at TEXT,
    detail_snapshot_id INTEGER,
    file_path TEXT,
    hash TEXT,
    last_error TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_documents_scout_status ON documents(scout_status);
CREATE INDEX IF NOT EXISTS idx_documents_download_status ON documents(download_status);
CREATE INDEX IF NOT EXISTS idx_documents_filing_date ON documents(filing_date);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    parameters_json TEXT NOT NULL DEFAULT '{}',
    summary_json TEXT NOT NULL DEFAULT '{}',
    script_version TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    current_phase TEXT,
    heartbeat_at TEXT,
    completed_units INTEGER NOT NULL DEFAULT 0,
    total_units INTEGER,
    progress_message TEXT,
    logical_requests INTEGER NOT NULL DEFAULT 0,
    http_attempts INTEGER NOT NULL DEFAULT 0,
    successful_requests INTEGER NOT NULL DEFAULT 0,
    failed_requests INTEGER NOT NULL DEFAULT 0,
    retries INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_runs_stage_status ON runs(stage, status);

CREATE TABLE IF NOT EXISTS errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    document_id TEXT,
    stage TEXT NOT NULL,
    code TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    retryable INTEGER NOT NULL DEFAULT 0,
    context_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    FOREIGN KEY (run_id) REFERENCES runs(id)
);
CREATE INDEX IF NOT EXISTS idx_errors_run ON errors(run_id);
CREATE INDEX IF NOT EXISTS idx_errors_document ON errors(document_id);
CREATE INDEX IF NOT EXISTS idx_errors_unresolved ON errors(resolved_at, severity);

CREATE TABLE IF NOT EXISTS raw_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    document_id TEXT,
    source_kind TEXT NOT NULL,
    source_url TEXT NOT NULL,
    final_url TEXT,
    fetched_at TEXT NOT NULL,
    http_status INTEGER,
    content_type TEXT,
    content_sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    compressed_size_bytes INTEGER NOT NULL,
    relative_path TEXT NOT NULL,
    response_headers_json TEXT NOT NULL DEFAULT '{}',
    parser_version TEXT NOT NULL,
    UNIQUE(source_kind, source_url, content_sha256),
    FOREIGN KEY (run_id) REFERENCES runs(id)
);
CREATE INDEX IF NOT EXISTS idx_raw_snapshots_document ON raw_snapshots(document_id);
CREATE INDEX IF NOT EXISTS idx_raw_snapshots_hash ON raw_snapshots(content_sha256);

CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL,
    path TEXT NOT NULL,
    original_filename TEXT,
    mime_type TEXT,
    extension TEXT,
    size_bytes INTEGER,
    sha256 TEXT NOT NULL,
    downloaded_at TEXT NOT NULL,
    is_current INTEGER NOT NULL DEFAULT 1,
    UNIQUE(document_id, sha256),
    FOREIGN KEY (document_id) REFERENCES documents(id)
);
CREATE INDEX IF NOT EXISTS idx_files_document_current ON files(document_id, is_current);
"""

ANALYSES_SCHEMA_SQL = """
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
    artifact_source TEXT,
    reconciled_at TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(file_id, file_sha256, analyzer_id, api_version),
    FOREIGN KEY (run_id) REFERENCES runs(id),
    FOREIGN KEY (document_id) REFERENCES documents(id),
    FOREIGN KEY (file_id) REFERENCES files(id)
);
CREATE INDEX IF NOT EXISTS idx_analyses_document ON analyses(document_id);
CREATE INDEX IF NOT EXISTS idx_analyses_status ON analyses(status);
CREATE INDEX IF NOT EXISTS idx_analyses_file_analyzer ON analyses(file_id, analyzer_id, api_version);
"""

NORMALIZATIONS_SCHEMA_SQL = """
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
);
CREATE INDEX IF NOT EXISTS idx_normalizations_document ON normalizations(document_id);
CREATE INDEX IF NOT EXISTS idx_normalizations_status ON normalizations(status);
CREATE INDEX IF NOT EXISTS idx_normalizations_analysis ON normalizations(analysis_id);
"""


def _migration_base(con: sqlite3.Connection, _: str) -> None:
    con.executescript(BASE_SCHEMA_SQL)


def _analysis_identity_index_exists(con: sqlite3.Connection) -> bool:
    wanted = ["file_id", "file_sha256", "analyzer_id", "api_version"]
    if not table_exists(con, "analyses"):
        return False
    for row in con.execute("PRAGMA index_list(analyses)").fetchall():
        if not bool(row[2]):
            continue
        name = str(row[1]).replace("'", "''")
        cols = [str(item[2]) for item in con.execute(f"PRAGMA index_info('{name}')").fetchall()]
        if cols == wanted:
            return True
    return False


def _rebuild_legacy_analyses(con: sqlite3.Connection) -> None:
    columns = column_names(con, "analyses")
    required = {
        "id", "run_id", "document_id", "file_id", "file_sha256", "analyzer_id",
        "operation_id", "status", "started_at", "finished_at", "raw_json_path",
        "markdown_path", "page_count", "table_count", "section_count", "warning_count",
        "error_code", "error_message", "created_at", "updated_at",
    }
    missing = sorted(required - columns)
    if missing:
        raise RuntimeError(
            "Existing analyses table has an unknown/incomplete schema; "
            f"missing columns: {', '.join(missing)}"
        )
    legacy = "analyses__legacy_migration"
    if table_exists(con, legacy):
        raise RuntimeError(f"Refusing migration because temporary table {legacy!r} already exists")
    con.execute(f"ALTER TABLE analyses RENAME TO {legacy}")
    con.executescript(ANALYSES_SCHEMA_SQL)
    api_expr = "COALESCE(api_version, '2025-11-01')" if "api_version" in columns else "'2025-11-01'"
    elapsed_expr = "elapsed_seconds" if "elapsed_seconds" in columns else "NULL"
    attempts_expr = "attempt_count" if "attempt_count" in columns else "0"
    source_expr = "artifact_source" if "artifact_source" in columns else "NULL"
    reconciled_expr = "reconciled_at" if "reconciled_at" in columns else "NULL"
    con.execute(
        f"""
        INSERT OR IGNORE INTO analyses (
            id, run_id, document_id, file_id, file_sha256, analyzer_id, api_version,
            operation_id, status, started_at, finished_at, raw_json_path, markdown_path,
            page_count, table_count, section_count, warning_count, elapsed_seconds,
            attempt_count, artifact_source, reconciled_at, error_code, error_message,
            created_at, updated_at
        )
        SELECT
            id, run_id, document_id, file_id, file_sha256, analyzer_id, {api_expr},
            operation_id, status, started_at, finished_at, raw_json_path, markdown_path,
            page_count, table_count, section_count, warning_count, {elapsed_expr},
            {attempts_expr}, {source_expr}, {reconciled_expr}, error_code, error_message,
            created_at, updated_at
        FROM {legacy}
        """
    )
    con.execute(f"DROP TABLE {legacy}")


def _migration_analyses(con: sqlite3.Connection, _: str) -> None:
    if not table_exists(con, "analyses"):
        con.executescript(ANALYSES_SCHEMA_SQL)
        return
    columns = column_names(con, "analyses")
    if "api_version" not in columns:
        _rebuild_legacy_analyses(con)
        return
    additions = (
        ("elapsed_seconds", "REAL"),
        ("attempt_count", "INTEGER NOT NULL DEFAULT 0"),
        ("artifact_source", "TEXT"),
        ("reconciled_at", "TEXT"),
    )
    for name, sql_type in additions:
        if name not in columns:
            con.execute(f"ALTER TABLE analyses ADD COLUMN {name} {sql_type}")
    if not _analysis_identity_index_exists(con):
        con.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_analyses_identity "
            "ON analyses(file_id, file_sha256, analyzer_id, api_version)"
        )
    con.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_analyses_document ON analyses(document_id);
        CREATE INDEX IF NOT EXISTS idx_analyses_status ON analyses(status);
        CREATE INDEX IF NOT EXISTS idx_analyses_file_analyzer ON analyses(file_id, analyzer_id, api_version);
        """
    )


def _migration_normalizations(con: sqlite3.Connection, _: str) -> None:
    con.executescript(NORMALIZATIONS_SCHEMA_SQL)


def _migration_release_tracking(con: sqlite3.Connection, release: str) -> None:
    if "release_version" not in column_names(con, "runs"):
        con.execute("ALTER TABLE runs ADD COLUMN release_version TEXT")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS pipeline_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    now = utcnow()
    con.execute(
        """
        INSERT INTO pipeline_metadata(key, value, updated_at)
        VALUES ('release_version', ?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        """,
        (release, now),
    )
    con.execute(
        """
        INSERT OR IGNORE INTO pipeline_metadata(key, value, updated_at)
        VALUES ('release_versioning_started_at', ?, ?)
        """,
        (now, now),
    )
    con.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_runs_set_release_version
        AFTER INSERT ON runs
        FOR EACH ROW
        WHEN NEW.release_version IS NULL
        BEGIN
            UPDATE runs
            SET release_version = (
                SELECT value FROM pipeline_metadata WHERE key='release_version' LIMIT 1
            )
            WHERE id = NEW.id;
        END
        """
    )


def _migration_recovery_tracking(con: sqlite3.Connection, _: str) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS rebuilds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            output_db_path TEXT,
            inventory_json TEXT NOT NULL DEFAULT '{}',
            plan_json TEXT NOT NULL DEFAULT '{}',
            summary_json TEXT NOT NULL DEFAULT '{}',
            release_version TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS recovery_provenance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rebuild_id INTEGER NOT NULL,
            entity_type TEXT NOT NULL,
            entity_key TEXT NOT NULL,
            recovered_from TEXT NOT NULL,
            completeness TEXT NOT NULL,
            missing_facts_json TEXT NOT NULL DEFAULT '[]',
            evidence_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            UNIQUE(rebuild_id, entity_type, entity_key),
            FOREIGN KEY (rebuild_id) REFERENCES rebuilds(id)
        );
        CREATE INDEX IF NOT EXISTS idx_recovery_provenance_entity ON recovery_provenance(entity_type, entity_key);
        """
    )


@dataclass(frozen=True)
class Migration:
    migration_id: str
    description: str
    apply: MigrationFn


MIGRATIONS = (
    Migration("001_base_ledger", "Stage 1/2 acquisition ledger", _migration_base),
    Migration("002_analyses", "Stage 3 analyses ledger", _migration_analyses),
    Migration("003_normalizations", "Stage 4 normalization ledger", _migration_normalizations),
    Migration("004_release_tracking", "Repository release tracking", _migration_release_tracking),
    Migration("005_recovery_tracking", "Artifact rebuild provenance", _migration_recovery_tracking),
)


def _ensure_registry(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            migration_id TEXT PRIMARY KEY,
            description TEXT NOT NULL,
            applied_at TEXT NOT NULL,
            release_version TEXT NOT NULL
        )
        """
    )


def applied_migration_ids(con: sqlite3.Connection) -> set[str]:
    if not table_exists(con, "schema_migrations"):
        return set()
    return {str(row[0]) for row in con.execute("SELECT migration_id FROM schema_migrations").fetchall()}


def migration_status(con: sqlite3.Connection) -> dict[str, object]:
    applied = applied_migration_ids(con)
    return {
        "schema_migrations_installed": table_exists(con, "schema_migrations"),
        "applied": [item.migration_id for item in MIGRATIONS if item.migration_id in applied],
        "pending": [item.migration_id for item in MIGRATIONS if item.migration_id not in applied],
        "latest": MIGRATIONS[-1].migration_id,
    }


def migrate(con: sqlite3.Connection, release: str) -> dict[str, object]:
    _ensure_registry(con)
    con.commit()
    applied = applied_migration_ids(con)
    newly_applied: list[str] = []
    for item in MIGRATIONS:
        if item.migration_id in applied:
            continue
        con.execute("BEGIN IMMEDIATE")
        try:
            item.apply(con, release)
            con.execute(
                """
                INSERT INTO schema_migrations(migration_id, description, applied_at, release_version)
                VALUES (?, ?, ?, ?)
                """,
                (item.migration_id, item.description, utcnow(), release),
            )
            con.commit()
        except Exception:
            con.rollback()
            raise
        applied.add(item.migration_id)
        newly_applied.append(item.migration_id)
    if table_exists(con, "pipeline_metadata"):
        con.execute(
            """
            INSERT INTO pipeline_metadata(key, value, updated_at)
            VALUES ('release_version', ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            (release, utcnow()),
        )
        con.commit()
    result = migration_status(con)
    result["newly_applied"] = newly_applied
    result["release_version"] = release
    return result


REQUIRED_TABLES = {
    "documents", "runs", "errors", "raw_snapshots", "files", "analyses",
    "normalizations", "pipeline_metadata", "schema_migrations", "rebuilds",
    "recovery_provenance",
}


def verify_schema(con: sqlite3.Connection) -> dict[str, object]:
    tables = {
        str(row[0])
        for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    missing_tables = sorted(REQUIRED_TABLES - tables)
    missing_columns: dict[str, list[str]] = {}
    expected_columns = {
        "runs": {"script_version", "parser_version", "release_version"},
        "analyses": {
            "file_sha256", "analyzer_id", "api_version", "elapsed_seconds",
            "attempt_count", "artifact_source", "reconciled_at",
        },
        "normalizations": {"analysis_id", "normalizer_version", "config_hash"},
    }
    for table, expected in expected_columns.items():
        missing = sorted(expected - column_names(con, table))
        if missing:
            missing_columns[table] = missing
    return {
        "ok": not missing_tables and not missing_columns,
        "missing_tables": missing_tables,
        "missing_columns": missing_columns,
        "migration_status": migration_status(con),
    }
