from __future__ import annotations

import json

from regdocs_atlas import rebuild as rebuild_module
from regdocs_atlas.db.connection import open_ledger
from regdocs_atlas.db.migrations import migrate, verify_schema
from regdocs_atlas.db.safety import backup_database, integrity_report
from regdocs_atlas.runtime.hashing import sha256_file


def test_sqlite_backup_and_recovery_migration_preserve_existing_run(tmp_path):
    db = tmp_path / "regdocs.db"
    con = open_ledger(db)
    try:
        migrate(con, "0.0.2")
        con.execute(
            """
            INSERT INTO runs(stage,status,started_at,script_version,parser_version,release_version)
            VALUES ('scout','SUCCEEDED','2026-08-09T00:00:00+00:00','1.1.2','legacy','0.0.2')
            """
        )
        con.commit()
    finally:
        con.close()

    backup = backup_database(db, tmp_path / "backups", release="0.0.3")
    assert backup.is_file()

    con = open_ledger(db)
    try:
        migrate(con, "0.0.3")
        row = con.execute(
            "SELECT script_version, parser_version, release_version FROM runs WHERE id=1"
        ).fetchone()
        assert tuple(row) == ("1.1.2", "legacy", "0.0.2")
        assert verify_schema(con)["ok"] is True
        assert integrity_report(con)["ok"] is True
    finally:
        con.close()


def test_rebuild_recovers_sidecar_source_and_stage3_and_queues_scout(tmp_path, monkeypatch):
    files = tmp_path / "workspace" / "2_download" / "files"
    azure = tmp_path / "workspace" / "3_analyze" / "content-understanding"
    docling = tmp_path / "workspace" / "3_analyze" / "docling"
    files.mkdir(parents=True)

    source = files / "4710492.pdf"
    source.write_bytes(b"%PDF-1.4\nsynthetic\n%%EOF\n")
    digest = sha256_file(source)
    sidecar = {
        "schema": "cer-regdocs-document-sidecar",
        "schema_version": 1,
        "document_id": "4710492",
        "title": "Recovered title",
        "source_url": "https://example.test/4710492",
        "item_kind": "PDF",
        "filing_date": "2020-01-02",
        "submitter": "A",
        "company": "B",
        "project": "C",
        "filing_number": "F1",
        "snippet": "s",
        "sha256": digest,
        "file": {
            "path": str(source),
            "original_filename": "orig.pdf",
            "content_type": "application/pdf",
            "extension": "pdf",
            "size_bytes": source.stat().st_size,
            "sha256": digest,
            "downloaded_at": "2026-01-01T00:00:00+00:00",
        },
        "pipeline": {
            "first_seen_at": "2020-01-01T00:00:00+00:00",
            "last_seen_at": "2020-01-02T00:00:00+00:00",
            "created_at": "2020-01-01T00:00:00+00:00",
        },
        "metadata": {},
    }
    (files / "4710492.metadata.json").write_text(json.dumps(sidecar), encoding="utf-8")

    raw = azure / "raw" / "prebuilt-layout" / "2025-11-01" / "4710492"
    raw.mkdir(parents=True)
    (raw / f"{digest}.json").write_text(
        json.dumps(
            {
                "analyzerId": "prebuilt-layout",
                "apiVersion": "2025-11-01",
                "contents": [{"pages": [{}], "tables": [], "sections": []}],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(rebuild_module, "DOWNLOAD_FILES_DIR", files)
    monkeypatch.setattr(rebuild_module, "CONTENT_UNDERSTANDING_DIR", azure)
    monkeypatch.setattr(rebuild_module, "DOCLING_DIR", docling)
    monkeypatch.setattr(rebuild_module, "inventory", lambda: type("I", (), {"to_dict": lambda self: {}})())
    monkeypatch.setattr(rebuild_module, "recovery_plan", lambda: {"best_recovery_tier": "B"})

    output = tmp_path / "rebuilt.db"
    result = rebuild_module.rebuild_create(output)
    assert result["summary"]["documents_recovered"] == 1
    assert result["summary"]["analyses_recovered"]["azure"] == 1

    queue = rebuild_module.recovery_queue(output)
    assert queue["pending"] == 1
    assert queue["tasks"][0]["document_id"] == "4710492"
    assert queue["tasks"][0]["priority"] == "LOW"


def test_rebuild_source_only_is_explicitly_minimal(tmp_path, monkeypatch):
    files = tmp_path / "workspace" / "2_download" / "files"
    files.mkdir(parents=True)
    (files / "999.pdf").write_bytes(b"%PDF-1.4\n%%EOF")

    monkeypatch.setattr(rebuild_module, "DOWNLOAD_FILES_DIR", files)
    monkeypatch.setattr(rebuild_module, "CONTENT_UNDERSTANDING_DIR", tmp_path / "azure")
    monkeypatch.setattr(rebuild_module, "DOCLING_DIR", tmp_path / "docling")
    monkeypatch.setattr(rebuild_module, "inventory", lambda: type("I", (), {"to_dict": lambda self: {}})())
    monkeypatch.setattr(rebuild_module, "recovery_plan", lambda: {"best_recovery_tier": "C"})

    output = tmp_path / "minimal.db"
    rebuild_module.rebuild_create(output)
    con = open_ledger(output, readonly=True)
    try:
        row = con.execute(
            "SELECT name,url,acquisition_state,scout_refresh_needed FROM documents WHERE id='999'"
        ).fetchone()
        assert tuple(row) == ("", "", "RECOVERED_MINIMAL", 1)
    finally:
        con.close()
    queue = rebuild_module.recovery_queue(output)
    assert queue["tasks"][0]["priority"] == "HIGH"
