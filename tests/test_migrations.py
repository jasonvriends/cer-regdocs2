from __future__ import annotations

import sqlite3

from regdocs_atlas.db.connection import open_ledger
from regdocs_atlas.db.migrations import MIGRATIONS, migrate, verify_schema


def test_clean_database_migrates_to_current_schema(tmp_path):
    path = tmp_path / "regdocs.db"
    con = open_ledger(path)
    try:
        first = migrate(con, "0.0.test")
        assert first["newly_applied"] == [item.migration_id for item in MIGRATIONS]
        assert verify_schema(con)["ok"] is True
        second = migrate(con, "0.0.test")
        assert second["newly_applied"] == []
        assert verify_schema(con)["ok"] is True
    finally:
        con.close()


def test_existing_base_tables_are_adopted_without_rewriting_runs(tmp_path):
    path = tmp_path / "regdocs.db"
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE runs (
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
        INSERT INTO runs(stage,status,started_at,script_version,parser_version)
        VALUES ('scout','SUCCEEDED','2026-08-09T00:00:00+00:00','1.1.2','legacy');
        """
    )
    con.commit()
    con.close()
    con = open_ledger(path)
    try:
        migrate(con, "0.0.test")
        row = con.execute(
            "SELECT script_version, parser_version, release_version FROM runs WHERE id=1"
        ).fetchone()
        assert row["script_version"] == "1.1.2"
        assert row["parser_version"] == "legacy"
        assert row["release_version"] is None
    finally:
        con.close()
