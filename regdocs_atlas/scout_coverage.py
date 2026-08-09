"""Durable Scout date-range coverage independent of SQLite run history."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .db import open_ledger
from .db.connection import table_exists
from .paths import DATABASE_PATH, SCOUT_MANIFEST_DIR
from .runtime.atomic import atomic_write_json

COVERAGE_SCHEMA = "cer-regdocs-scout-coverage"
COVERAGE_SCHEMA_VERSION = 1
COVERAGE_PATH = SCOUT_MANIFEST_DIR / "coverage.json"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _existing_ranges() -> list[tuple[date, date, list[str]]]:
    if not COVERAGE_PATH.is_file():
        return []
    try:
        payload = json.loads(COVERAGE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []
    if payload.get("schema") != COVERAGE_SCHEMA or payload.get("schema_version") != COVERAGE_SCHEMA_VERSION:
        return []
    ranges: list[tuple[date, date, list[str]]] = []
    for item in payload.get("intervals") or []:
        if not isinstance(item, dict):
            continue
        start = _parse_date(item.get("start_date"))
        end = _parse_date(item.get("end_date"))
        if start is None or end is None or start > end:
            continue
        sources = [str(v) for v in item.get("sources") or [] if v not in (None, "")]
        ranges.append((start, end, sources))
    return ranges


def _db_ranges(db_path: Path) -> tuple[list[tuple[date, date, list[str]]], int]:
    db_path = db_path.expanduser().resolve()
    if not db_path.is_file():
        return [], 0
    con = open_ledger(db_path, readonly=True)
    try:
        if not table_exists(con, "runs"):
            return [], 0
        rows = con.execute(
            """
            SELECT id, parameters_json, summary_json
            FROM runs
            WHERE lower(stage)='scout' AND status='SUCCEEDED'
            ORDER BY id
            """
        ).fetchall()
    finally:
        con.close()

    ranges: list[tuple[date, date, list[str]]] = []
    qualifying = 0
    for row in rows:
        try:
            params = json.loads(row["parameters_json"] or "{}")
            summary = json.loads(row["summary_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(params, dict) or not isinstance(summary, dict):
            continue
        # A dry run proves network reachability, not durable acquisition, so it
        # must never advance the durable coverage watermark.
        if bool(params.get("dry_run")):
            continue
        if summary.get("base_complete") is not True:
            continue
        if int(summary.get("base_search_pages_failed") or 0) != 0:
            continue
        if summary.get("post_run_audit_ok") is not True:
            continue
        start = _parse_date(summary.get("start_date") or params.get("start_date"))
        end = _parse_date(summary.get("end_date") or params.get("end_date"))
        if start is None or end is None or start > end:
            continue
        qualifying += 1
        ranges.append((start, end, [f"run:{int(row['id'])}"]))
    return ranges, qualifying


def _merge_ranges(ranges: list[tuple[date, date, list[str]]]) -> list[tuple[date, date, list[str]]]:
    if not ranges:
        return []
    ordered = sorted(ranges, key=lambda item: (item[0], item[1]))
    merged: list[tuple[date, date, list[str]]] = []
    for start, end, sources in ordered:
        if not merged or start > merged[-1][1] + timedelta(days=1):
            merged.append((start, end, list(dict.fromkeys(sources))))
            continue
        old_start, old_end, old_sources = merged[-1]
        merged[-1] = (
            old_start,
            max(old_end, end),
            list(dict.fromkeys([*old_sources, *sources])),
        )
    return merged


def refresh_scout_coverage(db_path: Path = DATABASE_PATH) -> dict[str, Any]:
    """Merge proven successful Scout ranges into a durable coverage manifest."""
    existing = _existing_ranges()
    discovered, qualifying_runs = _db_ranges(db_path)
    intervals = _merge_ranges([*existing, *discovered])

    gaps: list[dict[str, str]] = []
    for previous, current in zip(intervals, intervals[1:]):
        gap_start = previous[1] + timedelta(days=1)
        gap_end = current[0] - timedelta(days=1)
        if gap_start <= gap_end:
            gaps.append({
                "start_date": gap_start.isoformat(),
                "end_date": gap_end.isoformat(),
            })

    earliest = intervals[0][0] if intervals else None
    latest = intervals[-1][1] if intervals else None
    if gaps:
        recommended = gaps[0]["start_date"]
    elif latest is not None:
        recommended = (latest + timedelta(days=1)).isoformat()
    else:
        recommended = None

    payload: dict[str, Any] = {
        "schema": COVERAGE_SCHEMA,
        "schema_version": COVERAGE_SCHEMA_VERSION,
        "coverage_definition": (
            "non-dry-run Scout runs with status SUCCEEDED, base_complete=true, "
            "zero failed base-search pages, and post_run_audit_ok=true"
        ),
        "database": str(Path(db_path).expanduser().resolve()),
        "qualifying_runs_seen_in_database": qualifying_runs,
        "intervals": [
            {
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "sources": sources,
            }
            for start, end, sources in intervals
        ],
        "coverage_is_contiguous": len(gaps) == 0,
        "gaps": gaps,
        "earliest_covered_date": earliest.isoformat() if earliest else None,
        "latest_covered_date": latest.isoformat() if latest else None,
        "recommended_next_start_date": recommended,
        "updated_at": utcnow(),
    }
    COVERAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(COVERAGE_PATH, payload)
    return payload
