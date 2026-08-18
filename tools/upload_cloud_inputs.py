#!/usr/bin/env python3
"""Validate and upload the normalized REGDOCS cloud source package.

Run this on the computer where Stage 4 normalization was produced. The script
uploads the complete normalized contract to one existing Azure Blob container.
It does not create Azure infrastructure and it does not run Stage 5 or Stage 6.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PACKAGE_VERSION = "regdocs-cloud-source-v1"
PACKAGE_FILES = (
    "documents.jsonl",
    "pages.jsonl",
    "chunks.jsonl",
    "tables.jsonl",
    "provenance.jsonl",
)
NONEMPTY_FILES = {"documents.jsonl", "chunks.jsonl", "provenance.jsonl"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Upload the Stage 4 normalized source package used by REGDOCS Atlas in Azure.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    value.add_argument("--normalized-dir", default="workspace/4_normalize")
    value.add_argument("--account", required=True, help="Existing Azure Storage account name")
    value.add_argument("--container", required=True, help="Existing Blob container name")
    value.add_argument("--prefix", default="workspace/4_normalize")
    value.add_argument(
        "--sas-token",
        default=os.getenv("AZURE_STORAGE_SAS_TOKEN"),
        help="Container SAS; defaults to AZURE_STORAGE_SAS_TOKEN",
    )
    value.add_argument("--dry-run", action="store_true", help="Validate local files without contacting Azure")
    return value


def local_manifest(root: Path) -> dict[str, Any]:
    missing = [name for name in PACKAGE_FILES if not (root / name).is_file()]
    if missing:
        raise FileNotFoundError(
            "Stage 4 source package is incomplete. Missing: " + ", ".join(missing)
        )

    files: dict[str, Any] = {}
    for name in PACKAGE_FILES:
        path = root / name
        size = path.stat().st_size
        if name in NONEMPTY_FILES and size == 0:
            raise ValueError(f"{path} is empty; rerun/verify Stage 4 normalization before upload")
        files[name] = {"bytes": size, "sha256": sha256_file(path)}

    return {
        "package_version": PACKAGE_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "files": files,
        "runtime_contract": {
            "stage5_search_and_document_viewer": ["chunks.jsonl", "provenance.jsonl"],
            "stage6_regulatory_intelligence": ["documents.jsonl", "chunks.jsonl"],
            "durable_normalized_archive": list(PACKAGE_FILES),
            "markdown_required": False,
            "source_pdfs_required": False,
        },
    }


def main() -> int:
    args = parser().parse_args()
    root = Path(args.normalized_dir).expanduser().resolve()
    try:
        manifest = local_manifest(root)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(f"Validated normalized source package: {root}")
    for name in PACKAGE_FILES:
        details = manifest["files"][name]
        print(f"  {name:<18} {details['bytes']:>12,} bytes  sha256={details['sha256'][:12]}…")

    prefix = args.prefix.strip("/")
    print("\nCloud contract:")
    print("  Stage 5 / Search / HTML document viewer: chunks.jsonl + provenance.jsonl")
    print("  Stage 6 / Foundry regulatory intelligence: documents.jsonl + chunks.jsonl")
    print("  pages.jsonl + tables.jsonl: uploaded as part of the durable normalized package")
    print("  Markdown/PDF copies: not required by the deployed Atlas runtime")

    if args.dry_run:
        print("\nDRY RUN: local package is valid; Azure was not contacted.")
        return 0

    token = (args.sas_token or "").strip()
    token = token[1:] if token.startswith("?") else token
    if not token:
        print("ERROR: pass --sas-token or set AZURE_STORAGE_SAS_TOKEN", file=sys.stderr)
        return 2

    try:
        from azure.storage.blob import BlobServiceClient
    except ImportError:
        print(
            "ERROR: azure-storage-blob is not installed. Activate the REGDOCS environment or run "
            "python -m pip install azure-storage-blob.",
            file=sys.stderr,
        )
        return 2

    service = BlobServiceClient(
        account_url=f"https://{args.account}.blob.core.windows.net",
        credential=token,
    )
    container = service.get_container_client(args.container)
    try:
        container.get_container_properties()
        print(f"\nUploading to {args.account}/{args.container}/{prefix}/")
        for name in PACKAGE_FILES:
            path = root / name
            blob_name = f"{prefix}/{name}"
            with path.open("rb") as stream:
                container.upload_blob(blob_name, stream, overwrite=True, max_concurrency=4)
            remote_size = int(container.get_blob_client(blob_name).get_blob_properties().size)
            local_size = path.stat().st_size
            if remote_size != local_size:
                raise RuntimeError(
                    f"Size verification failed for {blob_name}: local={local_size}, remote={remote_size}"
                )
            print(f"  uploaded {blob_name} ({remote_size:,} bytes)")

        manifest_blob = f"{prefix}/source-package.json"
        manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
        container.upload_blob(manifest_blob, manifest_bytes, overwrite=True)
        print(f"  uploaded {manifest_blob}")
        print("\nUpload complete. Move to Cloud Shell and run:")
        print("  ./ui/deploy/deploy.sh --check-data")
        print("  ./ui/deploy/deploy.sh")
        return 0
    except Exception as exc:
        print(f"ERROR: Blob upload failed: {exc}", file=sys.stderr)
        return 1
    finally:
        service.close()


if __name__ == "__main__":
    raise SystemExit(main())
