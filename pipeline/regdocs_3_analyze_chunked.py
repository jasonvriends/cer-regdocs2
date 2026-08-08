#!/usr/bin/env python3
"""Stage 3 launcher with automatic Content-Range chunking for large PDFs.

This is a compatibility wrapper around ``regdocs_3_analyze.py``. PDFs with more
than 300 pages are analyzed in 300-page Azure Content Understanding ranges and
then merged back into the canonical Stage 3 JSON/Markdown artifact contract.

Completed range artifacts are reused on restart so a late-range failure does
not rebill already-successful ranges.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

try:
    from pypdf import PdfReader
except ImportError as exc:
    raise SystemExit(
        "Missing large-PDF dependency. Install with: "
        "python -m pip install -r pipeline/requirements.txt"
    ) from exc

import regdocs_3_analyze as base

MAX_PAGES_PER_ANALYSIS = 300
_ORIGINAL_ANALYZE_ONE = base.analyze_one


def pdf_page_count(path: Path) -> int:
    """Return the number of pages in a PDF without rendering page content."""
    return len(PdfReader(str(path)).pages)


def page_ranges(page_count: int, chunk_size: int = MAX_PAGES_PER_ANALYSIS) -> list[tuple[int, int]]:
    if page_count < 1:
        return []
    return [
        (start, min(start + chunk_size - 1, page_count))
        for start in range(1, page_count + 1, chunk_size)
    ]


def _part_paths(
    output_dir: Path,
    analyzer_id: str,
    api_version: str,
    candidate: base.Candidate,
    start_page: int,
    end_page: int,
) -> tuple[Path, Path]:
    raw_path, _ = base.canonical_artifact_paths(
        output_dir, analyzer_id, api_version, candidate
    )
    parts_dir = raw_path.with_suffix(".parts")
    stem = f"pages-{start_page:04d}-{end_page:04d}"
    return parts_dir / f"{stem}.json", parts_dir / f"{stem}.meta.json"


def _load_reusable_part(
    raw_path: Path,
    meta_path: Path,
    analyzer_id: str,
    api_version: str,
    content_range: str,
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Load a previously completed range only when its identity metadata matches."""
    if not raw_path.is_file() or not meta_path.is_file():
        return None, None
    try:
        result_dict, _ = base._load_and_validate_result_json(
            raw_path, analyzer_id, api_version
        )
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        if metadata.get("content_range") != content_range:
            return None, None
        return result_dict, metadata.get("operation_id")
    except (OSError, ValueError, json.JSONDecodeError):
        return None, None


def _result_counts(result_dict: dict[str, Any]) -> tuple[int, int, int, int, list[str]]:
    pages = 0
    tables = 0
    sections = 0
    markdown_parts: list[str] = []
    for content in result_dict.get("contents") or []:
        if not isinstance(content, dict):
            continue
        content_pages = content.get("pages") or []
        content_tables = content.get("tables") or []
        content_sections = content.get("sections") or []
        if isinstance(content_pages, list):
            pages += len(content_pages)
        if isinstance(content_tables, list):
            tables += len(content_tables)
        if isinstance(content_sections, list):
            sections += len(content_sections)
        markdown = content.get("markdown")
        if isinstance(markdown, str) and markdown:
            markdown_parts.append(markdown)
    warnings = result_dict.get("warnings") or []
    warning_count = len(warnings) if isinstance(warnings, list) else 0
    return pages, tables, sections, warning_count, markdown_parts


def analyze_one(
    client: Any,
    candidate: base.Candidate,
    file_path: Path,
    output_dir: Path,
    analyzer_id: str,
    api_version: str,
    verify_hash: bool,
    max_attempts: int,
    retry_base_delay: float,
    retry_max_delay: float,
) -> base.AnalysisOutcome:
    """Analyze one file, chunking PDFs over Azure's 300-page document limit."""
    if file_path.suffix.lower() != ".pdf":
        return _ORIGINAL_ANALYZE_ONE(
            client, candidate, file_path, output_dir, analyzer_id, api_version,
            verify_hash, max_attempts, retry_base_delay, retry_max_delay,
        )

    start_time = time.monotonic()

    if verify_hash:
        actual_hash = base.sha256_file(file_path)
        if actual_hash.lower() != candidate.sha256.lower():
            return base.AnalysisOutcome(
                document_id=candidate.document_id,
                file_id=candidate.file_id,
                status="FAILED",
                error_code="HASH_MISMATCH",
                error_message=(
                    f"DB sha256={candidate.sha256}, disk sha256={actual_hash}, "
                    f"path={base.stored_path(file_path)}"
                ),
                elapsed_seconds=time.monotonic() - start_time,
                attempt_count=0,
            )

    try:
        page_count = pdf_page_count(file_path)
    except Exception as exc:
        return base.AnalysisOutcome(
            document_id=candidate.document_id,
            file_id=candidate.file_id,
            status="FAILED",
            error_code="PDF_PAGE_COUNT_FAILED",
            error_message=f"Could not read PDF page count: {exc}",
            elapsed_seconds=time.monotonic() - start_time,
            attempt_count=0,
        )

    if page_count <= MAX_PAGES_PER_ANALYSIS:
        # Avoid hashing twice after the check above.
        return _ORIGINAL_ANALYZE_ONE(
            client, candidate, file_path, output_dir, analyzer_id, api_version,
            False, max_attempts, retry_base_delay, retry_max_delay,
        )

    ranges = page_ranges(page_count)
    print(
        f"          PDF pages={page_count}; splitting into {len(ranges)} Azure analyses "
        f"of at most {MAX_PAGES_PER_ANALYSIS} pages"
    )

    mime_type = base.detect_mime(file_path, candidate)
    data = file_path.read_bytes()
    range_results: list[dict[str, Any]] = []
    operation_ids: list[str] = []
    total_attempts = 0

    for chunk_index, (start_page, end_page) in enumerate(ranges, start=1):
        content_range = f"{start_page}-{end_page}"
        part_raw, part_meta = _part_paths(
            output_dir, analyzer_id, api_version, candidate, start_page, end_page
        )

        reused, reused_operation_id = _load_reusable_part(
            part_raw, part_meta, analyzer_id, api_version, content_range
        )
        if reused is not None:
            range_results.append(reused)
            if reused_operation_id:
                operation_ids.append(str(reused_operation_id))
            print(
                f"          [{chunk_index}/{len(ranges)}] pages {content_range} "
                "RECOVERED locally; no Azure call"
            )
            continue

        print(f"          [{chunk_index}/{len(ranges)}] pages {content_range}")
        operation_id: Optional[str] = None
        completed_result: Optional[dict[str, Any]] = None

        for attempt in range(1, max_attempts + 1):
            total_attempts += 1
            poller = None
            try:
                poller = client.begin_analyze_binary(
                    analyzer_id=analyzer_id,
                    binary_input=data,
                    content_type=mime_type,
                    content_range=content_range,
                )
                operation_id = getattr(poller, "operation_id", None)
                result = poller.result()
                completed_result = result.as_dict()

                # Commit each successful range immediately so restart can reuse it.
                base.atomic_write_text(part_raw, base.json_dumps(completed_result))
                base.atomic_write_text(
                    part_meta,
                    base.json_dumps({
                        "document_id": candidate.document_id,
                        "file_sha256": candidate.sha256,
                        "analyzer_id": analyzer_id,
                        "api_version": api_version,
                        "content_range": content_range,
                        "operation_id": operation_id,
                        "completed_at": base.utcnow(),
                    }),
                )
                break

            except base.HttpResponseError as exc:
                error_obj = getattr(exc, "error", None)
                code = (
                    getattr(error_obj, "code", None)
                    or getattr(exc, "status_code", None)
                    or "HTTP_ERROR"
                )
                code = str(code)
                can_resubmit = (
                    poller is None
                    and base.is_retryable_error_code(code)
                    and attempt < max_attempts
                )
                if can_resubmit:
                    delay = base.retry_delay_seconds(
                        exc, attempt, retry_base_delay, retry_max_delay
                    )
                    print(
                        f"              RETRY {attempt}/{max_attempts - 1} after {delay:g}s "
                        f"({code}: {str(exc)[:180]})"
                    )
                    time.sleep(delay)
                    continue
                return base.AnalysisOutcome(
                    document_id=candidate.document_id,
                    file_id=candidate.file_id,
                    status="FAILED",
                    operation_id=operation_id,
                    error_code=code,
                    error_message=f"pages {content_range}: {exc}",
                    elapsed_seconds=time.monotonic() - start_time,
                    attempt_count=total_attempts,
                )

            except base.ServiceRequestError as exc:
                can_resubmit = poller is None and attempt < max_attempts
                if can_resubmit:
                    delay = base.retry_delay_seconds(
                        exc, attempt, retry_base_delay, retry_max_delay
                    )
                    print(
                        f"              RETRY {attempt}/{max_attempts - 1} after {delay:g}s "
                        f"(SERVICE_REQUEST_ERROR: {str(exc)[:180]})"
                    )
                    time.sleep(delay)
                    continue
                return base.AnalysisOutcome(
                    document_id=candidate.document_id,
                    file_id=candidate.file_id,
                    status="FAILED",
                    operation_id=operation_id,
                    error_code="SERVICE_REQUEST_ERROR",
                    error_message=f"pages {content_range}: {exc}",
                    elapsed_seconds=time.monotonic() - start_time,
                    attempt_count=total_attempts,
                )

            except Exception as exc:
                return base.AnalysisOutcome(
                    document_id=candidate.document_id,
                    file_id=candidate.file_id,
                    status="FAILED",
                    operation_id=operation_id,
                    error_code=type(exc).__name__,
                    error_message=f"pages {content_range}: {exc}",
                    elapsed_seconds=time.monotonic() - start_time,
                    attempt_count=total_attempts,
                )

        if completed_result is None:
            return base.AnalysisOutcome(
                document_id=candidate.document_id,
                file_id=candidate.file_id,
                status="FAILED",
                operation_id=operation_id,
                error_code="RANGE_ANALYSIS_INCOMPLETE",
                error_message=f"pages {content_range}: no completed Azure result",
                elapsed_seconds=time.monotonic() - start_time,
                attempt_count=total_attempts,
            )

        range_results.append(completed_result)
        if operation_id:
            operation_ids.append(str(operation_id))

    # Keep each Azure response as a separate contents[] contribution. This avoids
    # rewriting Azure content spans/offsets across independently analyzed ranges.
    first = dict(range_results[0])
    combined_contents: list[Any] = []
    combined_warnings: list[Any] = []
    markdown_parts: list[str] = []
    total_pages = 0
    total_tables = 0
    total_sections = 0

    for result_dict in range_results:
        contents = result_dict.get("contents") or []
        if isinstance(contents, list):
            combined_contents.extend(contents)
        warnings = result_dict.get("warnings") or []
        if isinstance(warnings, list):
            combined_warnings.extend(warnings)

        pages, tables, sections, _, part_markdown = _result_counts(result_dict)
        total_pages += pages
        total_tables += tables
        total_sections += sections
        markdown_parts.extend(part_markdown)

    first["contents"] = combined_contents
    first["warnings"] = combined_warnings
    first["regdocsChunking"] = {
        "strategy": "content_range",
        "sourcePageCount": page_count,
        "maxPagesPerRequest": MAX_PAGES_PER_ANALYSIS,
        "parts": [
            {
                "range": f"{start_page}-{end_page}",
                "rawJsonPath": base.stored_path(
                    _part_paths(
                        output_dir, analyzer_id, api_version, candidate,
                        start_page, end_page,
                    )[0]
                ),
            }
            for start_page, end_page in ranges
        ],
    }

    raw_path, md_path = base.canonical_artifact_paths(
        output_dir, analyzer_id, api_version, candidate
    )
    base.atomic_write_text(raw_path, base.json_dumps(first))
    base.atomic_write_text(md_path, "\n\n".join(markdown_parts))

    print(
        f"          COMBINED pages={total_pages} tables={total_tables} "
        f"sections={total_sections} ranges={len(ranges)}"
    )

    return base.AnalysisOutcome(
        document_id=candidate.document_id,
        file_id=candidate.file_id,
        status="SUCCEEDED",
        operation_id=",".join(operation_ids) or None,
        api_version=api_version,
        raw_json_path=base.stored_path(raw_path),
        markdown_path=base.stored_path(md_path),
        page_count=total_pages,
        table_count=total_tables,
        section_count=total_sections,
        warning_count=len(combined_warnings),
        elapsed_seconds=time.monotonic() - start_time,
        attempt_count=total_attempts,
        artifact_source="azure_content_range",
    )


def main() -> int:
    base.analyze_one = analyze_one
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
