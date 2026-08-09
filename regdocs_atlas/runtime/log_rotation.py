"""Bounded rotation for the root pipeline log.

The active ``workspace/pipeline.log`` is intentionally just the latest mutating
stage run. Before a new mutating stage begins, the previous log is compressed
into ``workspace/logs/`` and old archives are pruned.
"""

from __future__ import annotations

import gzip
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

ARCHIVE_KEEP = 20
ARCHIVE_MAX_AGE_DAYS = 30
_COPY_BUFFER_SIZE = 1024 * 1024


def _archive_path(archive_dir: Path, *, now: datetime) -> Path:
    stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    candidate = archive_dir / f"pipeline-{stamp}.log.gz"
    if not candidate.exists():
        return candidate
    suffix = 1
    while True:
        candidate = archive_dir / f"pipeline-{stamp}-{suffix:02d}.log.gz"
        if not candidate.exists():
            return candidate
        suffix += 1


def prune_pipeline_log_archives(
    archive_dir: Path,
    *,
    keep: int = ARCHIVE_KEEP,
    max_age_days: int = ARCHIVE_MAX_AGE_DAYS,
    now_epoch: float | None = None,
) -> list[Path]:
    """Delete archives older than the age limit and then retain only newest N."""
    if not archive_dir.is_dir():
        return []

    removed: list[Path] = []
    now_value = time.time() if now_epoch is None else now_epoch
    cutoff = now_value - max(0, max_age_days) * 86400
    archives = sorted(
        archive_dir.glob("pipeline-*.log.gz"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )

    survivors: list[Path] = []
    for path in archives:
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed.append(path)
            else:
                survivors.append(path)
        except FileNotFoundError:
            continue

    for path in survivors[max(0, keep) :]:
        try:
            path.unlink()
            removed.append(path)
        except FileNotFoundError:
            continue
    return removed


def rotate_pipeline_log(
    log_path: Path,
    *,
    archive_dir: Path | None = None,
    keep: int = ARCHIVE_KEEP,
    max_age_days: int = ARCHIVE_MAX_AGE_DAYS,
) -> Path | None:
    """Compress the current log and prune archives.

    The source log is removed only after the compressed archive has been fully
    written and atomically renamed. An empty/nonexistent log simply triggers
    archive pruning and returns ``None``.
    """
    archive_root = archive_dir or (log_path.parent / "logs")
    archive_root.mkdir(parents=True, exist_ok=True)

    if not log_path.is_file() or log_path.stat().st_size == 0:
        prune_pipeline_log_archives(
            archive_root,
            keep=keep,
            max_age_days=max_age_days,
        )
        return None

    archive = _archive_path(archive_root, now=datetime.now(timezone.utc))
    temporary = archive.with_name(f".{archive.name}.{os.getpid()}.tmp")
    try:
        with log_path.open("rb") as source, gzip.open(temporary, "wb", compresslevel=6) as target:
            shutil.copyfileobj(source, target, length=_COPY_BUFFER_SIZE)
        os.replace(temporary, archive)
        log_path.unlink()
    finally:
        temporary.unlink(missing_ok=True)

    prune_pipeline_log_archives(
        archive_root,
        keep=keep,
        max_age_days=max_age_days,
    )
    return archive
