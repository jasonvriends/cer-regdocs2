#!/usr/bin/env python3
"""Run resumable Stage 6 Foundry extraction and intelligence publication in Azure."""

from __future__ import annotations

import os
import signal
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

from azure.core.exceptions import ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

WORK_ROOT = Path(os.getenv("REGDOCS_WORK_ROOT", "/work"))
NORMALIZED_DIR = WORK_ROOT / "normalize"
ENRICH_DIR = WORK_ROOT / "enrich"
MODEL_DIR = ENRICH_DIR / "model"
CACHE_PATH = MODEL_DIR / "extraction.sqlite"
CACHE_SNAPSHOT = MODEL_DIR / "extraction-upload.sqlite"
CACHE_UPLOAD_LOCK = threading.Lock()


def setting(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None or not value.strip():
        raise ValueError(f"Missing required environment variable: {name}")
    return value.strip()


def download_blob(container, blob_name: str, destination: Path, *, optional: bool = False) -> bool:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("wb") as stream:
            container.download_blob(blob_name, max_concurrency=4).readinto(stream)
    except ResourceNotFoundError:
        destination.unlink(missing_ok=True)
        if optional:
            print(f"Optional Blob does not exist yet: {blob_name}", flush=True)
            return False
        raise
    print(f"Downloaded {blob_name} ({destination.stat().st_size:,} bytes)", flush=True)
    return True


def snapshot_sqlite(source: Path, target: Path) -> None:
    target.unlink(missing_ok=True)
    source_db = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=60)
    target_db = sqlite3.connect(target, timeout=60)
    try:
        source_db.backup(target_db)
    finally:
        target_db.close()
        source_db.close()


def upload_cache(container, blob_name: str) -> None:
    with CACHE_UPLOAD_LOCK:
        if not CACHE_PATH.is_file() or CACHE_PATH.stat().st_size == 0:
            return
        snapshot_sqlite(CACHE_PATH, CACHE_SNAPSHOT)
        with CACHE_SNAPSHOT.open("rb") as stream:
            container.upload_blob(blob_name, stream, overwrite=True, max_concurrency=4)
        print(f"Uploaded intelligence cache checkpoint to {blob_name}", flush=True)


def checkpoint_loop(container, blob_name: str, interval: int, stop: threading.Event) -> None:
    while not stop.wait(interval):
        try:
            upload_cache(container, blob_name)
        except Exception as exc:
            print(f"WARNING: intelligence cache checkpoint failed: {exc}", file=sys.stderr, flush=True)


def run_child(command: list[str], terminating: threading.Event, child_ref: list[subprocess.Popen[str] | None]) -> None:
    print("$ " + " ".join(command), flush=True)
    child = subprocess.Popen(command, cwd="/app", text=True)
    child_ref[0] = child
    return_code = child.wait()
    child_ref[0] = None
    if return_code != 0:
        if terminating.is_set():
            raise KeyboardInterrupt
        raise RuntimeError(f"Command failed with exit code {return_code}: {' '.join(command)}")


def upload_outputs(container, prefix: str) -> None:
    for path in sorted(ENRICH_DIR.rglob("*")):
        if not path.is_file() or path == CACHE_SNAPSHOT:
            continue
        relative = path.relative_to(ENRICH_DIR).as_posix()
        blob_name = f"{prefix}/{relative}"
        with path.open("rb") as stream:
            container.upload_blob(blob_name, stream, overwrite=True, max_concurrency=4)
        print(f"Uploaded {blob_name}", flush=True)


def main() -> int:
    storage_account = setting("REGDOCS_STORAGE_ACCOUNT")
    container_name = setting("REGDOCS_BLOB_CONTAINER")
    normalized_prefix = setting("REGDOCS_NORMALIZED_BLOB_PREFIX", "workspace/4_normalize").rstrip("/")
    enrich_prefix = setting("REGDOCS_ENRICH_BLOB_PREFIX", "workspace/6_enrich").rstrip("/")
    cache_blob = setting("REGDOCS_INTELLIGENCE_CACHE_BLOB", f"{enrich_prefix}/model/extraction.sqlite")
    sync_seconds = int(setting("REGDOCS_INTELLIGENCE_CACHE_SYNC_SECONDS", "900"))
    foundry_endpoint = setting("FOUNDRY_PROJECT_ENDPOINT")
    model_deployment = setting("FOUNDRY_MODEL_DEPLOYMENT")
    search_endpoint = setting("AZURE_SEARCH_ENDPOINT")
    max_input_characters = setting("REGDOCS_INTELLIGENCE_MAX_INPUT_CHARACTERS", "60000")
    max_chunks = setting("REGDOCS_INTELLIGENCE_MAX_CHUNKS", "16")
    model_limit = os.getenv("REGDOCS_INTELLIGENCE_DOCUMENT_LIMIT", "").strip()

    NORMALIZED_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    credential = DefaultAzureCredential()
    service = BlobServiceClient(
        account_url=f"https://{storage_account}.blob.core.windows.net",
        credential=credential,
    )
    container = service.get_container_client(container_name)

    download_blob(container, f"{normalized_prefix}/documents.jsonl", NORMALIZED_DIR / "documents.jsonl")
    download_blob(container, f"{normalized_prefix}/chunks.jsonl", NORMALIZED_DIR / "chunks.jsonl")
    download_blob(container, cache_blob, CACHE_PATH, optional=True)

    # A full-corpus run must materialize only requests that still belong to the
    # current normalized corpus. Keep valid cache hits, but remove obsolete
    # request hashes before model_enrichment writes its output ledgers.
    if not model_limit and CACHE_PATH.is_file():
        reconcile = [
            sys.executable,
            "tools/prune_intelligence_cache.py",
            "--chunks",
            str(NORMALIZED_DIR / "chunks.jsonl"),
            "--cache",
            str(CACHE_PATH),
            "--model",
            model_deployment,
            "--max-input-characters",
            max_input_characters,
            "--max-chunks",
            max_chunks,
        ]
        print("$ " + " ".join(reconcile), flush=True)
        completed = subprocess.run(reconcile, cwd="/app", text=True, check=False)
        if completed.returncode != 0:
            raise RuntimeError(f"Stage 6 cache reconciliation failed with exit code {completed.returncode}")
        upload_cache(container, cache_blob)

    stop = threading.Event()
    terminating = threading.Event()
    child_ref: list[subprocess.Popen[str] | None] = [None]
    checkpoint = threading.Thread(
        target=checkpoint_loop,
        args=(container, cache_blob, sync_seconds, stop),
        daemon=True,
    )
    checkpoint.start()

    def terminate(signum: int, _frame: object) -> None:
        print(f"Received signal {signum}; stopping intelligence job before final checkpoint", flush=True)
        terminating.set()
        stop.set()
        child = child_ref[0]
        if child and child.poll() is None:
            child.terminate()

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)

    try:
        extract_command = [
            sys.executable,
            "pipeline.py",
            "enrich",
            "extract",
            "run",
            "--all",
            "--normalized-dir",
            str(NORMALIZED_DIR),
            "--output-dir",
            str(ENRICH_DIR),
            "--model-cache",
            str(CACHE_PATH),
            "--foundry-endpoint",
            foundry_endpoint,
            "--model-deployment",
            model_deployment,
            "--model-max-input-characters",
            max_input_characters,
            "--model-max-chunks",
            max_chunks,
        ]
        if model_limit:
            # Pilot/debug only. The normal production job uses --all and does not
            # prune other valid cache entries or replace full production indexes.
            extract_command = [item for item in extract_command if item != "--all"]
            extract_command.extend(["--limit", model_limit])
        run_child(extract_command, terminating, child_ref)
        upload_cache(container, cache_blob)

        publish_command = [
            sys.executable,
            "pipeline.py",
            "enrich",
            "publish",
            "--normalized-dir",
            str(NORMALIZED_DIR),
            "--output-dir",
            str(ENRICH_DIR),
            "--include-model-dir",
            str(MODEL_DIR),
            "--endpoint",
            search_endpoint,
        ]
        if model_limit:
            publish_command.extend(["--limit", model_limit])
        else:
            # A final full-corpus run must exactly reflect the current extraction.
            # Recreate derived indexes so records removed by a newer extraction do
            # not survive indefinitely as stale regulatory intelligence.
            publish_command.append("--recreate-indexes")
        run_child(publish_command, terminating, child_ref)

        regulatory_command = [
            sys.executable,
            "tools/publish_regulatory_records.py",
            "--input-dir",
            str(ENRICH_DIR),
            "--endpoint",
            search_endpoint,
            "--claims-index",
            setting("AZURE_SEARCH_CLAIMS_INDEX", "regdocs-claims"),
            "--obligations-index",
            setting("AZURE_SEARCH_OBLIGATIONS_INDEX", "regdocs-obligations"),
        ]
        if not model_limit:
            regulatory_command.append("--recreate-indexes")
        run_child(regulatory_command, terminating, child_ref)

        upload_outputs(container, enrich_prefix)
        print("Stage 6 intelligence extraction and publication completed successfully", flush=True)
        return 0
    except KeyboardInterrupt:
        return 128 + signal.SIGTERM
    finally:
        stop.set()
        checkpoint.join(timeout=5)
        try:
            upload_cache(container, cache_blob)
        except Exception as exc:
            print(f"ERROR: final intelligence cache checkpoint failed: {exc}", file=sys.stderr, flush=True)
        CACHE_SNAPSHOT.unlink(missing_ok=True)
        credential.close()


if __name__ == "__main__":
    raise SystemExit(main())
