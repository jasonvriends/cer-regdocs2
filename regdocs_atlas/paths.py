"""Canonical repository paths used by the REGDOCS Atlas pipeline."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PIPELINE_DIR = PROJECT_ROOT / "pipeline"

DATABASE_DIR = PROJECT_ROOT / "database"
DATABASE_PATH = DATABASE_DIR / "regdocs.db"
DATABASE_BACKUP_DIR = DATABASE_DIR / "backups"
LOCKS_DIR = DATABASE_DIR / "locks"

SCOUT_LOCK_PATH = LOCKS_DIR / "1_scout.lock"
DOWNLOAD_LOCK_PATH = LOCKS_DIR / "2_download.lock"
ANALYZE_LOCK_PATH = LOCKS_DIR / "3_analyze.lock"
NORMALIZE_LOCK_PATH = LOCKS_DIR / "4_normalize.lock"
MIGRATION_LOCK_PATH = LOCKS_DIR / "schema_migrate.lock"

WORKSPACE_DIR = PROJECT_ROOT / "workspace"

SCOUT_DIR = WORKSPACE_DIR / "1_scout"
SCOUT_RAW_DIR = SCOUT_DIR / "raw" / "regdocs"
SCOUT_RUN_DIR = SCOUT_DIR / "run"
SCOUT_PROGRESS_PATH = SCOUT_RUN_DIR / "progress.json"
SCOUT_LOG_PATH = SCOUT_RUN_DIR / "scout.log"

DOWNLOAD_DIR = WORKSPACE_DIR / "2_download"
DOWNLOAD_FILES_DIR = DOWNLOAD_DIR / "files"
DOWNLOAD_RUN_DIR = DOWNLOAD_DIR / "run"
DOWNLOAD_PROGRESS_PATH = DOWNLOAD_RUN_DIR / "progress.json"
DOWNLOAD_LOG_PATH = DOWNLOAD_RUN_DIR / "download.log"

ANALYZE_DIR = WORKSPACE_DIR / "3_analyze"
CONTENT_UNDERSTANDING_DIR = ANALYZE_DIR / "content-understanding"
DOCLING_DIR = ANALYZE_DIR / "docling"

NORMALIZE_DIR = WORKSPACE_DIR / "4_normalize"
INDEX_DIR = WORKSPACE_DIR / "5_index"


def stored_path(path: Path) -> str:
    """Return a portable repository-relative path when possible."""
    resolved = path.expanduser().resolve()
    try:
        return str(resolved.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(resolved)


def resolve_stored_path(value: str | Path, *, legacy_base: Path | None = None) -> Path:
    """Resolve a recorded path with compatibility for the old DB-relative layout."""
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    if path.parts and path.parts[0] in {"workspace", "database"}:
        return (PROJECT_ROOT / path).resolve()
    if legacy_base is not None:
        return (legacy_base / path).resolve()
    return (PROJECT_ROOT / path).resolve()
