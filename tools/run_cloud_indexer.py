#!/usr/bin/env python3
"""Run the hybrid index publisher as a resumable Azure Container Apps job.

The job uses managed identity for Blob Storage, Azure AI Search, and Foundry.
No Azure CLI login, SAS token, storage key, or service principal secret is used
inside the container.
"""
from __future__ import annotations

import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

from azure.core.exceptions import ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

WORK_ROOT = Path(os.getenv("REGDOCS_WORK_ROOT", "/work"))
NORMALIZED_DIR = WORK_ROOT / "normalize"
INDEX_DIR = WORK_ROOT / "index"
CACHE_PATH = INDEX_DIR / "embedding-cache.sqlite"
CACHE_SNAPSHOT = INDEX_DIR / "embedding-cache-upload.sqlite"
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
        print(f"Uploaded embedding cache checkpoint to {blob_name}", flush=True)


def checkpoint_loop(container, blob_name: str, interval: int, stop: threading.Event) -> None:
    while not stop.wait(interval):
        try:
            upload_cache(container, blob_name)
        except Exception as exc:  # The main publisher remains authoritative.
            print(f"WARNING: periodic cache checkpoint failed: {exc}", file=sys.stderr, flush=True)


def publisher_command() -> list[str]:
    command = [
        sys.executable,
        "tools/publish_hybrid_index.py",
        "--normalized-dir",
        str(NORMALIZED_DIR),
        "--cache-db",
        str(CACHE_PATH),
        "--embedding-batch-size",
        setting("REGDOCS_EMBEDDING_BATCH_SIZE", "128"),
        "--upload-batch-size",
        setting("REGDOCS_SEARCH_UPLOAD_BATCH_SIZE", "1000"),
    ]
    limit = os.getenv("REGDOCS_PUBLISH_LIMIT", "").strip()
    if limit:
        command.extend(["--limit", limit])
    if os.getenv("REGDOCS_RECREATE_INDEX", "false").lower() == "true":
        command.append("--recreate-index")
    return command


def main() -> int:
    storage_account = setting("REGDOCS_STORAGE_ACCOUNT")
    container_name = setting("REGDOCS_BLOB_CONTAINER")
    prefix = setting("REGDOCS_NORMALIZED_BLOB_PREFIX", "workspace/4_normalize").rstrip("/")
    cache_blob = setting("REGDOCS_EMBEDDING_CACHE_BLOB", "workspace/5_index/embedding-cache.sqlite")
    sync_seconds = int(setting("REGDOCS_CACHE_SYNC_SECONDS", "1800"))

    NORMALIZED_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_DIR.mkdir(parents=True, exist_ok=True)

    credential = DefaultAzureCredential()
    service = BlobServiceClient(
        account_url=f"https://{storage_account}.blob.core.windows.net",
        credential=credential,
    )
    container = service.get_container_client(container_name)

    download_blob(container, f"{prefix}/chunks.jsonl", NORMALIZED_DIR / "chunks.jsonl")
    download_blob(container, f"{prefix}/provenance.jsonl", NORMALIZED_DIR / "provenance.jsonl")
    download_blob(container, cache_blob, CACHE_PATH, optional=True)

    stop = threading.Event()
    checkpoint = threading.Thread(
        target=checkpoint_loop,
        args=(container, cache_blob, sync_seconds, stop),
        daemon=True,
    )
    checkpoint.start()

    child: subprocess.Popen[str] | None = None
    terminating = threading.Event()

    def terminate(signum: int, _frame: object) -> None:
        nonlocal child
        print(f"Received signal {signum}; stopping publisher before final cache checkpoint", flush=True)
        terminating.set()
        stop.set()
        if child and child.poll() is None:
            child.terminate()

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)

    try:
        command = publisher_command()
        print("Starting managed-identity hybrid index publisher", flush=True)
        for attempt in range(1, 6):
            child = subprocess.Popen(command, cwd="/app", text=True)
            return_code = child.wait()
            if return_code == 0:
                return 0
            if terminating.is_set():
                return 128 + signal.SIGTERM
            if attempt == 5:
                return return_code
            delay = min(30 * (2 ** (attempt - 1)), 120)
            print(
                f"Publisher failed with exit code {return_code}; retrying in {delay}s "
                f"(managed identity roles can take several minutes to propagate)",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(delay)
        return 1
    finally:
        stop.set()
        checkpoint.join(timeout=5)
        try:
            upload_cache(container, cache_blob)
        except Exception as exc:
            print(f"ERROR: final cache checkpoint failed: {exc}", file=sys.stderr, flush=True)
        CACHE_SNAPSHOT.unlink(missing_ok=True)
        credential.close()


if __name__ == "__main__":
    raise SystemExit(main())
