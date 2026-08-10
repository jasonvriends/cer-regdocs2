#!/usr/bin/env python3
"""Docling Stage 3 worker facade with conversion-status/error preservation."""

from __future__ import annotations

import json
import time
from typing import Any

import regdocs_3_docling_worker_core as core

# The supervisor monkeypatches these names before calling main().
open_db = core.open_db
create_run = core.create_run

SCRIPT_VERSION = "3d.3.1"
PARSER_VERSION = core.PARSER_VERSION
DEFAULT_ANALYZER_ID = core.DEFAULT_ANALYZER_ID
DEFAULT_OUTPUT_DIR = core.DEFAULT_OUTPUT_DIR


def _status_name(value: Any) -> str:
    raw = getattr(value, "value", value)
    text = str(raw or "unknown").strip().lower()
    return text.rsplit(".", 1)[-1] if "." in text else text


def _serialize_error(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        try:
            payload = value.model_dump(mode="json")
            if isinstance(payload, dict):
                return payload
        except Exception:
            pass
    if hasattr(value, "dict"):
        try:
            payload = value.dict()
            if isinstance(payload, dict):
                return json.loads(json.dumps(payload, default=str))
        except Exception:
            pass
    payload: dict[str, Any] = {"message": str(value)}
    for name in ("component_type", "module_name", "error_message", "category"):
        item = getattr(value, name, None)
        if item not in (None, ""):
            payload[name] = getattr(item, "value", item)
    return json.loads(json.dumps(payload, default=str))


def analyze_one(con: Any, run_id: int, args: Any, c: Any, version: str, converter: Any) -> bool:
    started_at = core.utcnow()
    t0 = time.monotonic()
    try:
        source = core.resolve_source(c, args.download_dir)
        result = converter.convert(source)
        conversion_status = _status_name(getattr(result, "status", None))
        conversion_errors = [_serialize_error(x) for x in (getattr(result, "errors", None) or [])]
        if conversion_status not in {"success", "partial_success"}:
            detail = "; ".join(str(x.get("error_message") or x.get("message") or x) for x in conversion_errors[:3])
            raise ValueError(f"Docling conversion status={conversion_status}" + (f": {detail}" if detail else ""))
        warnings = list(conversion_errors)
        if conversion_status == "partial_success" and not warnings:
            warnings.append({"category": "PARTIAL_SUCCESS", "message": "Docling returned partial_success"})
        doc = result.document
        native = doc.export_to_dict()
        markdown = doc.export_to_markdown()
        if not isinstance(native, dict) or not isinstance(markdown, str):
            raise ValueError("Docling returned an unexpected document export")
        content = core.project_docling(doc, native, markdown)
        envelope = {
            "analyzerId": args.analyzer_id,
            "apiVersion": version,
            "contents": [content],
            "warnings": warnings,
            "regdocsDocling": {
                "provider": "docling",
                "pipeline": "standard",
                "doclingVersion": version,
                "projectionVersion": PARSER_VERSION,
                "conversionStatus": conversion_status,
                "conversionErrors": conversion_errors,
                "native": native,
            },
        }
        raw_path = args.output_dir / "raw" / args.analyzer_id / version / c.document_id / f"{c.sha256}.json"
        md_path = args.output_dir / "markdown" / args.analyzer_id / version / c.document_id / f"{c.sha256}.md"
        core.atomic_write_text(raw_path, json.dumps(envelope, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        core.atomic_write_text(md_path, markdown)
        elapsed = time.monotonic() - t0
        core.upsert_analysis(
            con, run_id, c, args.analyzer_id, version, "SUCCEEDED", started_at, elapsed,
            raw_path, md_path, len(content["pages"]), len(content["tables"]),
            len(content["sections"]), len(warnings),
        )
        extra = f", warnings={len(warnings)}, status={conversion_status}" if warnings else ""
        print(f"{c.document_id}: SUCCEEDED ({len(content['pages'])} pages, {len(content['tables'])} tables, {elapsed:.1f}s{extra})")
        return True
    except Exception as exc:
        elapsed = time.monotonic() - t0
        core.upsert_analysis(con, run_id, c, args.analyzer_id, version, "FAILED", started_at, elapsed,
                             error_code=type(exc).__name__, error_message=str(exc))
        print(f"{c.document_id}: FAILED: {exc}", file=core.sys.stderr)
        return False


def main() -> int:
    core.open_db = open_db
    core.create_run = create_run
    core.analyze_one = analyze_one
    core.SCRIPT_VERSION = SCRIPT_VERSION
    return core.main()


if __name__ == "__main__":
    raise SystemExit(main())
