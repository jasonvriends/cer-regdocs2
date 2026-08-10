"""Selective REGDOCS detail refresh for artifact-rebuilt documents."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from .db import open_ledger
from .paths import PIPELINE_DIR, SCOUT_RAW_DIR, stored_path
from .version import release_version


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _legacy_scout() -> Any:
    if str(PIPELINE_DIR) not in sys.path:
        sys.path.insert(0, str(PIPELINE_DIR))
    import regdocs_1_scout_core as scout  # type: ignore
    return scout


def _save_raw(content: bytes) -> tuple[str, Path, int, int]:
    digest = hashlib.sha256(content).hexdigest()
    path = SCOUT_RAW_DIR / "recovery-detail" / digest[:2] / f"{digest}.html.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
        with gzip.open(temporary, "wb", compresslevel=6) as stream:
            stream.write(content)
        os.replace(temporary, path)
    return digest, path, len(content), path.stat().st_size


def _json_object(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    try:
        value = json.loads(str(raw or "{}"))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _missing(raw: Any) -> list[str]:
    try:
        value = json.loads(str(raw or "[]"))
    except json.JSONDecodeError:
        return []
    return [str(x) for x in value] if isinstance(value, list) else []


def execute_scout_recovery(
    db_path: Path,
    *,
    priority: str | None = None,
    limit: int | None = None,
    timeout_seconds: float = 60.0,
) -> dict[str, Any]:
    scout = _legacy_scout()
    con = open_ledger(db_path)
    run_id: int | None = None
    try:
        clauses = ["task_type='SCOUT_REFRESH'", "status IN ('PENDING','PARTIAL','FAILED')"]
        params: list[Any] = []
        if priority:
            clauses.append("priority=?")
            params.append(priority)
        sql = f"""
            SELECT rt.*, d.recovery_missing_facts_json, d.metadata
            FROM recovery_tasks rt
            JOIN documents d ON d.id=rt.document_id
            WHERE {' AND '.join(clauses)}
            ORDER BY CASE rt.priority WHEN 'HIGH' THEN 0 WHEN 'NORMAL' THEN 1 ELSE 2 END,
                     rt.id
        """
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        tasks = con.execute(sql, params).fetchall()
        now = utcnow()
        cur = con.execute(
            """
            INSERT INTO runs(stage,status,started_at,parameters_json,summary_json,script_version,
                             parser_version,current_phase,heartbeat_at,completed_units,total_units,
                             progress_message)
            VALUES ('recover_scout','RUNNING',?,?,'{}',?,?, 'detail_refresh',?,0,?,?)
            """,
            (
                now,
                json.dumps({"priority": priority, "limit": limit, "mode": "selective_detail_refresh"}, sort_keys=True),
                "recovery-scout",
                str(scout.PARSER_VERSION),
                now,
                len(tasks),
                f"Selective Scout recovery selected {len(tasks)} document(s)",
            ),
        )
        run_id = int(cur.lastrowid)
        con.commit()

        succeeded = failed = partial = 0
        headers = dict(getattr(scout, "HEADERS", {}))
        with httpx.Client(headers=headers, follow_redirects=True, timeout=timeout_seconds) as client:
            for index, task in enumerate(tasks, 1):
                document_id = str(task["document_id"])
                detail_url = str(scout.DETAIL_URL_TEMPLATE).format(item_id=document_id)
                try:
                    if not document_id.isdigit():
                        raise ValueError("Selective detail recovery currently requires a numeric REGDOCS item ID")
                    response = client.get(detail_url)
                    response.raise_for_status()
                    content = response.content
                    digest, raw_path, size_bytes, compressed_size = _save_raw(content)
                    fetched_at = utcnow()
                    detail = scout.parse_detail_page(response.text, document_id, str(response.url))
                    detail_fields = dict(getattr(detail, "fields", {}) or {})
                    title = str(getattr(detail, "title", "") or "").strip()

                    normalized: dict[str, Any] = {}
                    for label, values in detail_fields.items():
                        key = scout.canonical_field_for_label(str(label))
                        if not key:
                            continue
                        if isinstance(values, list):
                            value = next((str(x).strip() for x in values if str(x).strip()), "")
                        else:
                            value = str(values or "").strip()
                        if value:
                            normalized[key] = value

                    snapshot = con.execute(
                        """
                        INSERT INTO raw_snapshots(
                            run_id,document_id,source_kind,source_url,final_url,fetched_at,http_status,
                            content_type,content_sha256,size_bytes,compressed_size_bytes,relative_path,
                            response_headers_json,parser_version
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(source_kind,source_url,content_sha256) DO UPDATE SET
                            run_id=excluded.run_id, document_id=excluded.document_id,
                            final_url=excluded.final_url, fetched_at=excluded.fetched_at,
                            http_status=excluded.http_status, content_type=excluded.content_type,
                            size_bytes=excluded.size_bytes, compressed_size_bytes=excluded.compressed_size_bytes,
                            relative_path=excluded.relative_path, response_headers_json=excluded.response_headers_json,
                            parser_version=excluded.parser_version
                        RETURNING id
                        """,
                        (
                            run_id, document_id, "recovery_detail", detail_url, str(response.url), fetched_at,
                            response.status_code, response.headers.get("content-type"), digest, size_bytes,
                            compressed_size, stored_path(raw_path), json.dumps(dict(response.headers), default=str),
                            str(scout.PARSER_VERSION),
                        ),
                    ).fetchone()
                    snapshot_id = int(snapshot[0])

                    metadata = _json_object(task["metadata"])
                    metadata["recovery_scout_refresh"] = {
                        "run_id": run_id,
                        "fetched_at": fetched_at,
                        "snapshot_id": snapshot_id,
                        "detail_url": str(response.url),
                        "fields": detail_fields,
                        "release_version": release_version(),
                    }

                    remaining = set(_missing(task["recovery_missing_facts_json"]))
                    remaining.discard("scout_raw_evidence")
                    remaining.discard("source_url")
                    if title:
                        remaining.discard("title")
                    field_to_missing = {
                        "filing_date": "filing_date",
                        "submitter": "submitter",
                        "company": "company",
                        "project": "project",
                        "filing_number": "filing_number",
                    }
                    for key, missing_name in field_to_missing.items():
                        if normalized.get(key):
                            remaining.discard(missing_name)

                    filing_date = normalized.get("filing_date")
                    if filing_date:
                        filing_date = scout.normalize_date(str(filing_date)) or str(filing_date)
                    complete = not remaining
                    acquisition_state = "OBSERVED" if complete else "RECOVERED_PARTIAL"
                    con.execute(
                        """
                        UPDATE documents
                        SET name=CASE WHEN ?<>'' THEN ? ELSE name END,
                            url=?,
                            filing_date=COALESCE(?, filing_date),
                            submitter=COALESCE(?, submitter),
                            company=COALESCE(?, company),
                            project=COALESCE(?, project),
                            filing_number=COALESCE(?, filing_number),
                            metadata=?, detail_status='SUCCEEDED', detail_last_attempt_at=?,
                            detail_succeeded_at=?, detail_snapshot_id=?, acquisition_state=?,
                            scout_refresh_needed=?, recovery_missing_facts_json=?, updated_at=?
                        WHERE id=?
                        """,
                        (
                            title, title, str(response.url), filing_date,
                            normalized.get("submitter"), normalized.get("company"), normalized.get("project"),
                            normalized.get("filing_number"), json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                            fetched_at, fetched_at, snapshot_id, acquisition_state, 0 if complete else 1,
                            json.dumps(sorted(remaining)), fetched_at, document_id,
                        ),
                    )
                    task_status = "COMPLETED" if complete else "PARTIAL"
                    con.execute(
                        """
                        UPDATE recovery_tasks
                        SET status=?, attempted_at=?, completed_at=?, updated_at=?, error_message=NULL,
                            missing_facts_json=?
                        WHERE id=?
                        """,
                        (task_status, fetched_at, fetched_at, fetched_at, json.dumps(sorted(remaining)), task["id"]),
                    )
                    if complete:
                        succeeded += 1
                    else:
                        partial += 1
                    con.commit()
                except Exception as exc:
                    failed += 1
                    failed_at = utcnow()
                    con.execute(
                        "UPDATE recovery_tasks SET status='FAILED', attempted_at=?, updated_at=?, error_message=? WHERE id=?",
                        (failed_at, failed_at, f"{type(exc).__name__}: {exc}"[:4000], task["id"]),
                    )
                    con.execute(
                        """
                        INSERT INTO errors(run_id,document_id,stage,code,severity,message,retryable,context_json,created_at)
                        VALUES (?,?,'recover_scout',?,'ERROR',?,1,?,?)
                        """,
                        (run_id, document_id, type(exc).__name__, str(exc)[:4000], json.dumps({"detail_url": detail_url}), failed_at),
                    )
                    con.commit()

                con.execute(
                    """
                    UPDATE runs SET heartbeat_at=?,completed_units=?,successful_requests=?,failed_requests=?,
                                    progress_message=?,summary_json=? WHERE id=?
                    """,
                    (
                        utcnow(), index, succeeded + partial, failed,
                        f"Scout recovery {index}/{len(tasks)}: complete={succeeded} partial={partial} failed={failed}",
                        json.dumps({"selected": len(tasks), "completed": succeeded, "partial": partial, "failed": failed}, sort_keys=True),
                        run_id,
                    ),
                )
                con.commit()

        status = "SUCCEEDED" if failed == 0 and partial == 0 else ("COMPLETED_WITH_GAPS" if failed == 0 else "COMPLETED_WITH_ERRORS")
        finished = utcnow()
        summary = {"selected": len(tasks), "completed": succeeded, "partial": partial, "failed": failed}
        con.execute(
            "UPDATE runs SET status=?,finished_at=?,heartbeat_at=?,current_phase='finished',summary_json=?,progress_message=? WHERE id=?",
            (status, finished, finished, json.dumps(summary, sort_keys=True), f"Selective Scout recovery {status}", run_id),
        )
        con.commit()
        return {"run_id": run_id, "status": status, **summary}
    except Exception:
        if run_id is not None:
            with con:
                con.execute(
                    "UPDATE runs SET status='FAILED',finished_at=?,heartbeat_at=?,current_phase='finished' WHERE id=?",
                    (utcnow(), utcnow(), run_id),
                )
        raise
    finally:
        con.close()
