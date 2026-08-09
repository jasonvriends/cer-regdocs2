"""Create a clean operational SQLite baseline from durable workspace artifacts.

Flat rebuilds deliberately discard historical execution/recovery bookkeeping after
an exact manifest-backed Stage 1-3 reconstruction succeeds. They never contact
REGDOCS, Azure Content Understanding, Docling, or Azure AI Search.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .db import migrate, open_ledger
from .db.safety import integrity_report
from .rebuild_manifest_overlay import rebuild_create as manifest_rebuild_create
from .version import release_version


def _strip_recovery_metadata(value: Any) -> str:
    try:
        payload = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload.pop("recovery", None)
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def flatten_create(output_db: Path) -> dict[str, Any]:
    """Rebuild Stages 1-3 from disk, then remove historical/recovery bookkeeping.

    The manifest-backed rebuild is allowed to finish first because it provides the
    proof that the durable artifacts are sufficient and internally consistent. If
    that reconstruction reports gaps, flattening stops and preserves the recovery
    evidence for diagnosis instead of hiding it.
    """
    result = manifest_rebuild_create(output_db)
    if result.get("status") != "SUCCEEDED":
        result["flat"] = False
        result["flat_note"] = (
            "Flattening was not applied because the artifact rebuild did not finish "
            "with exact SUCCEEDED status. Recovery provenance was preserved for diagnosis."
        )
        return result

    db_path = Path(result["output_db"]).expanduser().resolve()
    con = open_ledger(db_path)
    try:
        before = {
            "runs": int(con.execute("SELECT COUNT(*) FROM runs").fetchone()[0]),
            "errors": int(con.execute("SELECT COUNT(*) FROM errors").fetchone()[0]),
            "rebuilds": int(con.execute("SELECT COUNT(*) FROM rebuilds").fetchone()[0]),
            "recovery_provenance": int(
                con.execute("SELECT COUNT(*) FROM recovery_provenance").fetchone()[0]
            ),
            "recovery_tasks": int(
                con.execute("SELECT COUNT(*) FROM recovery_tasks").fetchone()[0]
            ),
            "normalizations": int(
                con.execute("SELECT COUNT(*) FROM normalizations").fetchone()[0]
            ),
        }

        rows = con.execute("SELECT id, metadata FROM documents").fetchall()
        for row in rows:
            con.execute(
                "UPDATE documents SET metadata=? WHERE id=?",
                (_strip_recovery_metadata(row["metadata"]), str(row["id"])),
            )

        con.execute(
            """
            UPDATE documents
            SET acquisition_state='OBSERVED',
                scout_refresh_needed=0,
                recovery_rebuild_id=NULL,
                recovery_missing_facts_json='[]'
            """
        )
        con.execute("UPDATE raw_snapshots SET run_id=NULL")
        con.execute("UPDATE analyses SET run_id=NULL")
        con.execute("UPDATE normalizations SET run_id=NULL")

        # Dependency order matters because errors/recovery rows reference their
        # parent execution/rebuild records.
        con.execute("DELETE FROM errors")
        con.execute("DELETE FROM recovery_tasks")
        con.execute("DELETE FROM recovery_provenance")
        con.execute("DELETE FROM rebuilds")
        con.execute("DELETE FROM runs")

        # Stage 4 is intentionally a rebuildable local derivative in this POC.
        # The artifact rebuild does not reconstruct normalization ledger rows.
        con.execute("DELETE FROM normalizations")

        migrate(con, release_version())
        con.commit()
        after = {
            "documents": int(con.execute("SELECT COUNT(*) FROM documents").fetchone()[0]),
            "raw_snapshots": int(con.execute("SELECT COUNT(*) FROM raw_snapshots").fetchone()[0]),
            "current_files": int(
                con.execute("SELECT COUNT(*) FROM files WHERE is_current=1").fetchone()[0]
            ),
            "successful_analyses": int(
                con.execute("SELECT COUNT(*) FROM analyses WHERE status='SUCCEEDED'").fetchone()[0]
            ),
            "normalizations": int(con.execute("SELECT COUNT(*) FROM normalizations").fetchone()[0]),
            "runs": int(con.execute("SELECT COUNT(*) FROM runs").fetchone()[0]),
            "errors": int(con.execute("SELECT COUNT(*) FROM errors").fetchone()[0]),
            "rebuilds": int(con.execute("SELECT COUNT(*) FROM rebuilds").fetchone()[0]),
            "recovery_provenance": int(
                con.execute("SELECT COUNT(*) FROM recovery_provenance").fetchone()[0]
            ),
            "recovery_tasks": int(
                con.execute("SELECT COUNT(*) FROM recovery_tasks").fetchone()[0]
            ),
        }
    finally:
        con.close()

    # The temporary recovery provenance can occupy real pages even after DELETE.
    # Compact only the new output DB; the active database is never touched.
    vacuum = open_ledger(db_path)
    try:
        vacuum.execute("VACUUM")
        vacuum.commit()
        integrity = integrity_report(vacuum)
    finally:
        vacuum.close()

    result["flat"] = True
    result["status"] = "SUCCEEDED"
    result["flat_cleanup"] = {
        "before": before,
        "after": after,
        "stage4_policy": "not reconstructed; rerun Normalize locally when desired",
        "version": release_version(),
    }
    result["integrity"] = integrity
    return result
