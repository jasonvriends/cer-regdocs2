#!/usr/bin/env python3
"""Stage 4 public supervisor: provider selection plus per-document child isolation."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import regdocs_4_normalize_worker as worker
from regdocs_paths import ANALYZE_DIR, DATABASE_PATH, LOCKS_DIR, NORMALIZE_DIR, resolve_stored_path, stored_path

SCRIPT_VERSION = "4.2.0"
PARSER_VERSION = worker.PARSER_VERSION
AZURE_ANALYZER = "prebuilt-layout"
AZURE_VERSION = "2025-11-01"
DOCLING_ANALYZER = "docling-standard"
DEFAULT_LOCK = LOCKS_DIR / "4_normalize.lock"
OUTPUTS = ("documents", "pages", "chunks", "tables", "provenance")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def open_db(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA synchronous = NORMAL")
    return con


def _pid(path: Path) -> Optional[int]:
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("pid")
    except Exception:
        return None
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True


class StageLock:
    def __init__(self, path: Path, force: bool = False):
        self.path, self.force, self.owned = path, force, False

    def __enter__(self) -> "StageLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.force and self.path.exists():
            self.path.unlink()
        elif self.path.exists():
            pid = _pid(self.path)
            if pid is not None and not _alive(pid):
                with contextlib.suppress(FileNotFoundError):
                    self.path.unlink()
                print(f"Removing stale normalize lock: {self.path} (PID {pid} is not running).", file=sys.stderr)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise RuntimeError(f"Normalize lock already exists: {self.path}") from exc
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "created_at": utcnow(), "role": "normalize_supervisor", "script_version": SCRIPT_VERSION}, f, indent=2)
            f.write("\n")
        self.owned = True
        return self

    def __exit__(self, *_: Any) -> None:
        if self.owned:
            with contextlib.suppress(FileNotFoundError):
                self.path.unlink()


def latest_docling_version(con: sqlite3.Connection) -> str:
    row = con.execute(
        """SELECT a.api_version FROM analyses a JOIN files f ON f.id=a.file_id AND f.sha256=a.file_sha256
           WHERE a.analyzer_id=? AND a.status='SUCCEEDED' AND f.is_current=1 ORDER BY a.id DESC LIMIT 1""",
        (DOCLING_ANALYZER,),
    ).fetchone()
    if row is None:
        raise RuntimeError("No successful current Docling analysis exists. Run pipeline/regdocs_3_docling.py first.")
    return str(row[0])


def resolve_provider(args: argparse.Namespace, con: sqlite3.Connection) -> tuple[str, str, str]:
    provider = args.analysis_provider
    if provider is None:
        if not sys.stdin.isatty():
            raise RuntimeError("Choose --analysis-provider azure or --analysis-provider docling.")
        provider = input("Analysis provider [azure/docling]: ").strip().lower()
        if provider not in {"azure", "docling"}:
            raise RuntimeError("Analysis provider must be 'azure' or 'docling'.")
    if provider == "azure":
        analyzer, version = AZURE_ANALYZER, AZURE_VERSION
    else:
        analyzer, version = DOCLING_ANALYZER, latest_docling_version(con)
    return provider, args.analyzer_id or analyzer, args.api_version or version


def worker_args(args: argparse.Namespace, analyzer: str, version: str) -> argparse.Namespace:
    return argparse.Namespace(
        db=args.db, analysis_dir=args.analysis_dir, output_dir=args.output_dir,
        analyzer_id=analyzer, api_version=version, document_id=args.document_id,
        limit=args.limit, target_words=args.target_words, max_words=args.max_words,
        skip_errors=True, dry_run=args.dry_run, status=False, version=False,
    )


def create_run(con: sqlite3.Connection, args: argparse.Namespace, provider: str, analyzer: str, version: str, config_hash: str, total: int) -> int:
    now = utcnow()
    params = {
        "provider": provider, "db": stored_path(args.db), "analysis_dir": stored_path(args.analysis_dir),
        "output_dir": stored_path(args.output_dir), "analyzer_id": analyzer, "api_version": version,
        "document_id": args.document_id, "limit": args.limit, "target_words": args.target_words,
        "max_words": args.max_words, "config_hash": config_hash, "concurrency": 1,
        "worker_isolation": "one_document_per_child", "continue_on_document_error": not args.stop_on_error,
    }
    cur = con.execute(
        """INSERT INTO runs (stage,status,started_at,parameters_json,summary_json,script_version,parser_version,
           current_phase,heartbeat_at,completed_units,total_units,progress_message,logical_requests,http_attempts,
           successful_requests,failed_requests,retries)
           VALUES ('normalize','RUNNING',?,?,'{}',?,?, 'normalizing',?,0,?,?,0,0,0,0,0)""",
        (now, json.dumps(params, sort_keys=True), SCRIPT_VERSION, PARSER_VERSION, now, total,
         f"Normalize supervisor selected {total} document(s) from {provider}"),
    )
    con.commit()
    return int(cur.lastrowid)


def update_run(con: sqlite3.Connection, run_id: int, total: int, completed: int, succeeded: int, failed: int,
               pages: int, chunks: int, tables: int, provenance: int, status: Optional[str] = None,
               message: Optional[str] = None, hashes: Optional[dict[str, str]] = None) -> None:
    now = utcnow()
    summary: dict[str, Any] = {"documents_total": total, "documents_completed": completed, "succeeded": succeeded,
                               "failed": failed, "pages": pages, "chunks": chunks, "tables": tables,
                               "provenance": provenance}
    if hashes is not None:
        summary["output_sha256"] = hashes
    text = message or f"Normalize {completed}/{total}: {succeeded} succeeded, {failed} failed"
    if status is None:
        con.execute("UPDATE runs SET heartbeat_at=?,completed_units=?,total_units=?,summary_json=?,progress_message=?,successful_requests=?,failed_requests=? WHERE id=?",
                    (now, completed, total, json.dumps(summary, sort_keys=True), text, succeeded, failed, run_id))
    else:
        summary["status"] = status
        con.execute("UPDATE runs SET status=?,finished_at=?,heartbeat_at=?,current_phase='finished',completed_units=?,total_units=?,summary_json=?,progress_message=?,successful_requests=?,failed_requests=? WHERE id=?",
                    (status, now, now, completed, total, json.dumps(summary, sort_keys=True), text, succeeded, failed, run_id))
    con.commit()


def bootstrap(worker_path: Path) -> str:
    return f"""
import sys
sys.path.insert(0, {str(worker_path.parent)!r})
import regdocs_4_normalize_worker as worker
parent_run_id = int(sys.argv[1])
_original_open_db = worker.open_db
class Bound:
    def __init__(self, con): self._con = con
    def execute(self, sql, params=()):
        if ' '.join(str(sql).split()).upper().startswith('UPDATE RUNS SET'):
            return self._con.execute('SELECT 1')
        return self._con.execute(sql, params)
    def __getattr__(self, name): return getattr(self._con, name)
worker.open_db = lambda path: Bound(_original_open_db(path))
worker.create_run = lambda con, args, config_hash: parent_run_id
worker.update_run_progress = lambda *a, **k: None
worker.finish_run = lambda *a, **k: None
sys.argv = [worker.__file__] + sys.argv[2:]
raise SystemExit(worker.main())
""".strip()


def command(args: argparse.Namespace, run_id: int, document_id: str, analyzer: str, version: str, shard: Path) -> list[str]:
    worker_path = Path(__file__).with_name("regdocs_4_normalize_worker.py")
    return [sys.executable, "-c", bootstrap(worker_path), str(run_id), "--db", str(args.db),
            "--analysis-dir", str(args.analysis_dir), "--output-dir", str(shard), "--analyzer-id", analyzer,
            "--api-version", version, "--document-id", document_id, "--target-words", str(args.target_words),
            "--max-words", str(args.max_words), "--skip-errors"]


def result_row(con: sqlite3.Connection, analysis_id: int, config_hash: str) -> Optional[sqlite3.Row]:
    return con.execute(
        """SELECT status,page_count,chunk_count,table_count,provenance_count,error_code,error_message
           FROM normalizations WHERE analysis_id=? AND normalizer_version=? AND config_hash=? LIMIT 1""",
        (analysis_id, worker.SCRIPT_VERSION, config_hash),
    ).fetchone()


def merge(output_dir: Path, shards: list[Path]) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    hashes: dict[str, str] = {}
    for name in OUTPUTS:
        final = output_dir / f"{name}.jsonl"
        partial = final.with_name(final.name + ".partial")
        h = hashlib.sha256()
        with partial.open("wb") as out:
            for shard in shards:
                source = shard / f"{name}.jsonl"
                if not source.is_file():
                    raise FileNotFoundError(f"Successful worker shard is missing {source}")
                data = source.read_bytes()
                out.write(data); h.update(data)
            out.flush(); os.fsync(out.fileno())
        os.replace(partial, final)
        hashes[name] = h.hexdigest()
    return hashes


def show_status(con: sqlite3.Connection) -> int:
    row = con.execute("SELECT id,status,started_at,finished_at,parameters_json,summary_json,script_version,parser_version,progress_message FROM runs WHERE lower(stage)='normalize' ORDER BY id DESC LIMIT 1").fetchone()
    print(json.dumps(dict(row), indent=2, sort_keys=True) if row else "No normalize runs recorded.")
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="REGDOCS Stage 4: resilient single-worker normalization from Azure or Docling", formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("--analysis-provider", choices=("azure", "docling"), help="Stage 3 provider; prompts on a TTY when omitted")
    p.add_argument("--db", default=stored_path(DATABASE_PATH)); p.add_argument("--analysis-dir", default=stored_path(ANALYZE_DIR))
    p.add_argument("--output-dir", default=stored_path(NORMALIZE_DIR)); p.add_argument("--document-id", action="append")
    p.add_argument("--limit", type=int); p.add_argument("--target-words", type=int, default=worker.DEFAULT_TARGET_WORDS)
    p.add_argument("--max-words", type=int, default=worker.DEFAULT_MAX_WORDS)
    p.add_argument("--stop-on-error", action="store_true", help="Stop after first failed document; default continues")
    p.add_argument("--dry-run", action="store_true"); p.add_argument("--status", action="store_true")
    p.add_argument("--lock-file", default=stored_path(DEFAULT_LOCK)); p.add_argument("--force-lock", action="store_true")
    p.add_argument("--analyzer-id", help=argparse.SUPPRESS); p.add_argument("--api-version", help=argparse.SUPPRESS)
    p.add_argument("--skip-errors", action="store_true", help=argparse.SUPPRESS); p.add_argument("--version", action="store_true")
    return p


def main() -> int:
    args = parser().parse_args()
    if args.version:
        print(SCRIPT_VERSION); return 0
    args.db = resolve_stored_path(args.db); args.analysis_dir = resolve_stored_path(args.analysis_dir)
    args.output_dir = resolve_stored_path(args.output_dir); args.lock_file = resolve_stored_path(args.lock_file)
    if args.limit is not None and args.limit < 1: raise SystemExit("--limit must be >= 1")
    if args.target_words < 50: raise SystemExit("--target-words must be >= 50")
    if args.max_words < args.target_words: raise SystemExit("--max-words must be >= --target-words")
    if not args.db.is_file(): raise SystemExit(f"Database not found: {args.db}")
    con = open_db(args.db)
    try:
        worker.ensure_schema(con)
        if args.status: return show_status(con)
        provider, analyzer, version = resolve_provider(args, con)
        wargs = worker_args(args, analyzer, version); config_hash = worker.config_hash_for(wargs)
        candidates = worker.select_candidates(con, analyzer, version, args.document_id, args.limit)
        print(f"Stage 4 normalize {SCRIPT_VERSION}: {len(candidates)} document(s) selected")
        print(f"Provider:            {provider}\nAnalyzer:            {analyzer}\nVersion:             {version}\nConcurrency:         1 child process")
        print("Failure policy:      stop on first failed document" if args.stop_on_error else "Failure policy:      continue with next document")
        if not candidates: return 0
        if args.dry_run:
            missing = 0
            for c in candidates:
                raw = worker.resolve_artifact(c.raw_json_path, args.analysis_dir, "raw", c, "json")
                missing += int(raw is None); print(f"{c.document_id}: {'OK' if raw else 'MISSING_JSON'}; json={raw}")
            return 1 if missing else 0
        with StageLock(args.lock_file, args.force_lock):
            run_id = create_run(con, args, provider, analyzer, version, config_hash, len(candidates))
            root = args.output_dir / ".workers" / f"run-{run_id}"; root.mkdir(parents=True, exist_ok=True)
            shards: list[Path] = []; succeeded = failed = pages = chunks = tables = provenance = 0
            total = len(candidates); width = len(str(total))
            for i, candidate in enumerate(candidates, 1):
                shard = root / candidate.document_id
                if shard.exists(): shutil.rmtree(shard)
                print(f"[{i:0{width}d}/{total}] {candidate.document_id} {candidate.title}")
                try:
                    child = subprocess.run(command(args, run_id, candidate.document_id, analyzer, version, shard), text=True, capture_output=True)
                except KeyboardInterrupt:
                    update_run(con, run_id, total, i-1, succeeded, failed, pages, chunks, tables, provenance, "INTERRUPTED", "Normalize interrupted; completed shards retained")
                    print("\nInterrupted. Canonical JSONL was not replaced."); return 130
                row = result_row(con, candidate.analysis_id, config_hash)
                if child.returncode == 0 and row is not None and row["status"] == "SUCCEEDED":
                    succeeded += 1; p=int(row["page_count"] or 0); c=int(row["chunk_count"] or 0); t=int(row["table_count"] or 0); pr=int(row["provenance_count"] or 0)
                    pages += p; chunks += c; tables += t; provenance += pr; shards.append(shard)
                    print(f"          OK pages={p} chunks={c} tables={t}")
                else:
                    failed += 1; code = row["error_code"] if row else f"worker_exit_{child.returncode}"; message = row["error_message"] if row else "worker exited without a normalization row"
                    print(f"          FAILED {code}: {message}", file=sys.stderr)
                    diagnostics = "\n".join(x for x in (child.stdout.strip(), child.stderr.strip()) if x)
                    for line in diagnostics.splitlines()[-20:]: print(f"            {line}", file=sys.stderr)
                update_run(con, run_id, total, i, succeeded, failed, pages, chunks, tables, provenance)
                if failed and args.stop_on_error:
                    update_run(con, run_id, total, i, succeeded, failed, pages, chunks, tables, provenance, "FAILED", "Normalize stopped on first document failure; canonical JSONL was not replaced")
                    return 1
            hashes = merge(args.output_dir, shards); status = "SUCCEEDED" if failed == 0 else "COMPLETED_WITH_ERRORS"
            update_run(con, run_id, total, total, succeeded, failed, pages, chunks, tables, provenance, status, f"Normalize {status}: {succeeded} succeeded, {failed} failed", hashes)
            shutil.rmtree(root, ignore_errors=True)
            print(f"\nRun {run_id} {status}: {succeeded} succeeded, {failed} failed, {pages} pages, {chunks} chunks.")
            for name in OUTPUTS: print(f"  {name:10s} {args.output_dir / (name + '.jsonl')} sha256={hashes[name]}")
            return 0 if failed == 0 else 1
    finally:
        con.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FATAL: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
