"""Generic run/error lifecycle helpers for new/refactored stages."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Mapping


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def create_run(
    con: sqlite3.Connection,
    *,
    stage: str,
    component_version: str,
    parser_version: str,
    parameters: Mapping[str, Any] | None = None,
    phase: str = "starting",
    message: str | None = None,
    total_units: int | None = None,
) -> int:
    now = utcnow()
    cursor = con.execute(
        """
        INSERT INTO runs(
            stage, status, started_at, parameters_json, summary_json,
            script_version, parser_version, current_phase, heartbeat_at,
            completed_units, total_units, progress_message
        ) VALUES (?, 'RUNNING', ?, ?, '{}', ?, ?, ?, ?, 0, ?, ?)
        """,
        (
            stage,
            now,
            json.dumps(dict(parameters or {}), ensure_ascii=False, sort_keys=True, default=str),
            component_version,
            parser_version,
            phase,
            now,
            total_units,
            message,
        ),
    )
    con.commit()
    return int(cursor.lastrowid)


def heartbeat(
    con: sqlite3.Connection,
    run_id: int,
    *,
    completed_units: int,
    total_units: int | None = None,
    phase: str | None = None,
    message: str | None = None,
    summary: Mapping[str, Any] | None = None,
) -> None:
    con.execute(
        """
        UPDATE runs
        SET heartbeat_at=?,
            completed_units=?,
            total_units=COALESCE(?, total_units),
            current_phase=COALESCE(?, current_phase),
            progress_message=COALESCE(?, progress_message),
            summary_json=CASE WHEN ? IS NULL THEN summary_json ELSE ? END
        WHERE id=?
        """,
        (
            utcnow(),
            completed_units,
            total_units,
            phase,
            message,
            None if summary is None else 1,
            json.dumps(dict(summary or {}), ensure_ascii=False, sort_keys=True, default=str),
            run_id,
        ),
    )
    con.commit()


def finish_run(
    con: sqlite3.Connection,
    run_id: int,
    *,
    status: str,
    summary: Mapping[str, Any] | None = None,
    message: str | None = None,
) -> None:
    now = utcnow()
    con.execute(
        """
        UPDATE runs
        SET status=?, finished_at=?, heartbeat_at=?, current_phase='finished',
            summary_json=?, progress_message=COALESCE(?, progress_message)
        WHERE id=?
        """,
        (
            status,
            now,
            now,
            json.dumps(dict(summary or {}), ensure_ascii=False, sort_keys=True, default=str),
            message,
            run_id,
        ),
    )
    con.commit()


def record_error(
    con: sqlite3.Connection,
    *,
    stage: str,
    code: str,
    message: str,
    run_id: int | None = None,
    document_id: str | None = None,
    severity: str = "ERROR",
    retryable: bool = False,
    context: Mapping[str, Any] | None = None,
) -> int:
    cursor = con.execute(
        """
        INSERT INTO errors(
            run_id, document_id, stage, code, severity, message,
            retryable, context_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            document_id,
            stage,
            code,
            severity.upper(),
            message,
            1 if retryable else 0,
            json.dumps(dict(context or {}), ensure_ascii=False, sort_keys=True, default=str),
            utcnow(),
        ),
    )
    con.commit()
    return int(cursor.lastrowid)
