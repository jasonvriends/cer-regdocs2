#!/usr/bin/env python3
"""Build a reproducible Azure Content Understanding vs Docling benchmark.

The evaluator is read-only with respect to the pipeline database and artifacts.
It writes a compact evaluation package under reports/azure-docling by default.
"""

from __future__ import annotations

import argparse
import collections
import csv
import html
import json
import math
import re
import sqlite3
import statistics
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import pypdfium2 as pdfium


TOKEN_RE = re.compile(r"[^\W_]+(?:[-’'][^\W_]+)*", re.UNICODE)
COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
HTML_TABLE_TAG_RE = re.compile(
    r"</?(?:table|thead|tbody|tfoot|tr|td|th|br)(?:\s[^>]*)?/?>", re.IGNORECASE
)
IMAGE_RE = re.compile(r"!\[[^]]*\]\([^)]*\)")
LINK_RE = re.compile(r"\[([^]]+)\]\([^)]*\)")
SOURCE_PAGE_RE = re.compile(r"D\((\d+),")
POINTER_RE = re.compile(r"^/(paragraphs|sections|tables|figures)/(\d+)$")


@dataclass
class Pair:
    document_id: str
    name: str
    file_path: str
    sha256: str
    size_bytes: int
    extension: str
    azure_pages: int
    docling_pages: int
    azure_tables: int
    docling_tables: int
    azure_sections: int
    docling_sections: int
    azure_seconds: float
    docling_seconds: float
    azure_markdown: str
    docling_markdown: str
    azure_raw: str
    docling_raw: str
    language: str = "unknown"
    azure_figures: int = 0
    actual_pages: int | None = None
    probe_tokens_per_page: float | None = None
    azure_token_count: int = 0
    docling_token_count: int = 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=Path("database/regdocs.db"))
    parser.add_argument("--workspace", type=Path, default=Path("workspace"))
    parser.add_argument(
        "--output", type=Path, default=Path("reports/azure-docling")
    )
    parser.add_argument("--large-count", type=int, default=15)
    parser.add_argument("--cohort-count", type=int, default=10)
    parser.add_argument("--stratum-count", type=int, default=12)
    return parser.parse_args()


def tokens(text: str) -> list[str]:
    return [x.casefold().replace("’", "'") for x in TOKEN_RE.findall(text)]


def markdown_tokens(path: str) -> list[str]:
    value = Path(path).read_text(encoding="utf-8", errors="replace")
    value = COMMENT_RE.sub(" ", value)
    value = HTML_TABLE_TAG_RE.sub(" ", value)
    value = IMAGE_RE.sub(" ", value)
    value = LINK_RE.sub(r"\1", value)
    return tokens(html.unescape(value))


def quantile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, math.ceil(probability * len(ordered)) - 1))
    return float(ordered[index])


def summary(values: Iterable[float]) -> dict[str, float]:
    data = [float(x) for x in values]
    if not data:
        return {"count": 0, "sum": 0, "mean": 0, "median": 0, "p90": 0, "p95": 0, "max": 0}
    return {
        "count": len(data),
        "sum": sum(data),
        "mean": statistics.mean(data),
        "median": statistics.median(data),
        "p90": quantile(data, 0.90),
        "p95": quantile(data, 0.95),
        "max": max(data),
    }


def ngrams(items: Sequence[str], width: int = 5) -> set[tuple[str, ...]]:
    if len(items) < width:
        return set()
    return set(zip(*(items[offset:] for offset in range(width))))


def set_score(candidate: set[Any], reference: set[Any]) -> dict[str, float]:
    common = len(candidate & reference)
    precision = common / len(candidate) if candidate else 0.0
    recall = common / len(reference) if reference else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def repetition_rate(items: Sequence[str], width: int = 5) -> float:
    total = max(0, len(items) - width + 1)
    return 1.0 - (len(ngrams(items, width)) / total) if total else 0.0


def read_normalized_metadata(path: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if not path.is_file():
        return result
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if item.get("analyzer_id") != "prebuilt-layout":
                continue
            document_id = str(item.get("document_id") or "")
            if document_id:
                result[document_id] = item
    return result


def load_pairs(con: sqlite3.Connection, normalized: dict[str, dict[str, Any]]) -> list[Pair]:
    rows = con.execute(
        """
        SELECT d.id, d.name, f.path, f.sha256, COALESCE(f.size_bytes,0),
               COALESCE(f.extension,''),
               COALESCE(az.page_count,0), COALESCE(dc.page_count,0),
               COALESCE(az.table_count,0), COALESCE(dc.table_count,0),
               COALESCE(az.section_count,0), COALESCE(dc.section_count,0),
               COALESCE(az.elapsed_seconds,0), COALESCE(dc.elapsed_seconds,0),
               az.markdown_path, dc.markdown_path, az.raw_json_path, dc.raw_json_path
        FROM files f
        JOIN documents d ON d.id=f.document_id
        JOIN analyses az ON az.file_id=f.id AND az.file_sha256=f.sha256
          AND az.analyzer_id='prebuilt-layout' AND az.status='SUCCEEDED'
        JOIN analyses dc ON dc.file_id=f.id AND dc.file_sha256=f.sha256
          AND dc.analyzer_id='docling-standard' AND dc.status='SUCCEEDED'
        WHERE f.is_current=1 AND lower(COALESCE(f.extension,''))='.pdf'
        ORDER BY CAST(d.id AS INTEGER), d.id
        """
    ).fetchall()
    pairs: list[Pair] = []
    for row in rows:
        meta = normalized.get(str(row[0]), {})
        pairs.append(
            Pair(
                document_id=str(row[0]), name=str(row[1]), file_path=str(row[2]),
                sha256=str(row[3]), size_bytes=int(row[4]), extension=str(row[5]),
                azure_pages=int(row[6]), docling_pages=int(row[7]),
                azure_tables=int(row[8]), docling_tables=int(row[9]),
                azure_sections=int(row[10]), docling_sections=int(row[11]),
                azure_seconds=float(row[12]), docling_seconds=float(row[13]),
                azure_markdown=str(row[14]), docling_markdown=str(row[15]),
                azure_raw=str(row[16]), docling_raw=str(row[17]),
                language=str(meta.get("language") or "unknown"),
                azure_figures=int(meta.get("figure_count") or 0),
            )
        )
    return pairs


def probe_pdf(pair: Pair) -> None:
    document = pdfium.PdfDocument(pair.file_path)
    count = len(document)
    pair.actual_pages = count
    indices = sorted({0, count // 2, count - 1}) if count else []
    sample_tokens = 0
    for index in indices:
        page = document[index]
        textpage = page.get_textpage()
        sample_tokens += len(tokens(textpage.get_text_range()))
        textpage.close()
        page.close()
    pair.probe_tokens_per_page = sample_tokens / len(indices) if indices else 0.0
    document.close()


def extract_pdf_tokens(path: str) -> tuple[int, list[str], list[int]]:
    document = pdfium.PdfDocument(path)
    all_tokens: list[str] = []
    per_page: list[int] = []
    for index in range(len(document)):
        page = document[index]
        textpage = page.get_textpage()
        page_tokens = tokens(textpage.get_text_range())
        per_page.append(len(page_tokens))
        all_tokens.extend(page_tokens)
        textpage.close()
        page.close()
    count = len(document)
    document.close()
    return count, all_tokens, per_page


def load_raw(path: str) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as stream:
        return json.load(stream)


def docling_native_counts(value: dict[str, Any]) -> tuple[int, int]:
    native = value.get("regdocsDocling", {}).get("native", {})
    pictures = native.get("pictures") or []
    page_keys = native.get("pages") or {}
    return len(pictures), len(page_keys)


def azure_structures(value: dict[str, Any]) -> tuple[int, int, int]:
    contents = value.get("contents") or []
    return (
        sum(len(x.get("figures") or []) for x in contents),
        sum(len(x.get("hyperlinks") or []) for x in contents),
        sum(len(x.get("pages") or []) for x in contents),
    )


def table_diagnostics(value: dict[str, Any]) -> dict[str, int]:
    result = {
        "tables": 0, "cells": 0, "empty_cells": 0, "invalid_cells": 0,
        "duplicate_coordinates": 0, "multi_page_tables": 0, "sourceless_tables": 0,
    }
    for content in value.get("contents") or []:
        for table in content.get("tables") or []:
            result["tables"] += 1
            rows = int(table.get("rowCount") or 0)
            columns = int(table.get("columnCount") or 0)
            pages = {int(x) for x in SOURCE_PAGE_RE.findall(str(table.get("source") or ""))}
            if len(pages) > 1:
                result["multi_page_tables"] += 1
            if not pages:
                result["sourceless_tables"] += 1
            coordinates: collections.Counter[tuple[int, int]] = collections.Counter()
            for cell in table.get("cells") or []:
                result["cells"] += 1
                row = int(cell.get("rowIndex") or 0)
                column = int(cell.get("columnIndex") or 0)
                coordinates[(row, column)] += 1
                if not str(cell.get("content") or "").strip():
                    result["empty_cells"] += 1
                if row < 0 or column < 0 or row >= rows or column >= columns:
                    result["invalid_cells"] += 1
            result["duplicate_coordinates"] += sum(max(0, count - 1) for count in coordinates.values())
    return result


def projected_tokens(value: dict[str, Any]) -> list[str]:
    """Approximate the normalizer's section traversal over paragraphs/tables."""
    output: list[str] = []
    for content in value.get("contents") or []:
        paragraphs = content.get("paragraphs") or []
        tables = content.get("tables") or []
        sections = content.get("sections") or []
        emitted_paragraphs: set[int] = set()
        emitted_tables: set[int] = set()
        visited_sections: set[int] = set()
        child_sections: set[int] = set()
        for section in sections:
            for pointer in section.get("elements") or []:
                match = POINTER_RE.match(str(pointer))
                if match and match.group(1) == "sections":
                    child_sections.add(int(match.group(2)))

        def emit_table(index: int) -> None:
            if index in emitted_tables or not 0 <= index < len(tables):
                return
            emitted_tables.add(index)
            cells = sorted(
                tables[index].get("cells") or [],
                key=lambda x: (int(x.get("rowIndex") or 0), int(x.get("columnIndex") or 0)),
            )
            output.extend(str(cell.get("content") or "") for cell in cells)

        def walk(index: int) -> None:
            if index in visited_sections or not 0 <= index < len(sections):
                return
            visited_sections.add(index)
            for pointer in sections[index].get("elements") or []:
                match = POINTER_RE.match(str(pointer))
                if not match:
                    continue
                kind, raw_index = match.groups()
                element_index = int(raw_index)
                if kind == "paragraphs" and element_index not in emitted_paragraphs and 0 <= element_index < len(paragraphs):
                    emitted_paragraphs.add(element_index)
                    output.append(str(paragraphs[element_index].get("content") or ""))
                elif kind == "tables":
                    emit_table(element_index)
                elif kind == "sections":
                    walk(element_index)

        for root in (x for x in range(len(sections)) if x not in child_sections):
            walk(root)
        for index in range(len(sections)):
            walk(index)
        for index, paragraph in enumerate(paragraphs):
            if index not in emitted_paragraphs:
                output.append(str(paragraph.get("content") or ""))
        for index in range(len(tables)):
            emit_table(index)
    return tokens("\n".join(output))


def select_benchmark(pairs: list[Pair], args: argparse.Namespace) -> tuple[list[Pair], dict[str, list[str]]]:
    cohorts: dict[str, list[Pair]] = {}
    cohorts["largest"] = sorted(pairs, key=lambda x: (x.actual_pages or x.azure_pages), reverse=True)[: args.large_count]
    cohorts["table_absolute"] = sorted(pairs, key=lambda x: x.azure_tables, reverse=True)[: args.cohort_count]
    cohorts["table_dense"] = sorted(
        [x for x in pairs if x.azure_pages >= 5],
        key=lambda x: x.azure_tables / max(1, x.azure_pages), reverse=True,
    )[: args.cohort_count]
    cohorts["figure_absolute"] = sorted(pairs, key=lambda x: x.azure_figures, reverse=True)[: args.cohort_count]
    cohorts["figure_dense"] = sorted(
        [x for x in pairs if x.azure_pages >= 5],
        key=lambda x: x.azure_figures / max(1, x.azure_pages), reverse=True,
    )[: args.cohort_count]
    bins = [(1, 1), (2, 5), (6, 20), (21, 100), (101, 10**9)]
    for low, high in bins:
        candidates = [x for x in pairs if low <= x.azure_pages <= high]
        cohorts[f"pages_{low}_{high if high < 10**9 else 'plus'}"] = sorted(
            candidates, key=lambda x: x.sha256
        )[: args.stratum_count]
    french_title = re.compile(
        r"\b(?:rapport|demande|réponse|pièce|annexe|ordonnance|règlement|"
        r"avis|lettre|preuve|canalisation|agrandissement|autorisation|exportation)\b",
        re.IGNORECASE,
    )
    cohorts["french"] = sorted(
        [
            x for x in pairs
            if x.language.casefold() in {"fra", "fre", "fr", "french"}
            or french_title.search(x.name)
        ],
        key=lambda x: (-(x.actual_pages or x.azure_pages), x.sha256),
    )[: args.stratum_count]
    # Probe text identifies likely image-only/scanned PDFs without treating the
    # PDF text layer as ground truth.
    cohorts["ocr_candidates"] = sorted(
        [x for x in pairs if (x.probe_tokens_per_page or 0) < 10],
        key=lambda x: (x.probe_tokens_per_page or 0, -max(x.azure_token_count, x.docling_token_count)),
    )[: max(args.cohort_count * 2, 20)]
    selected: dict[str, Pair] = {}
    membership: dict[str, list[str]] = collections.defaultdict(list)
    for cohort, values in cohorts.items():
        for pair in values:
            selected[pair.document_id] = pair
            membership[pair.document_id].append(cohort)
    return list(selected.values()), dict(membership)


def benchmark_one(pair: Pair, membership: list[str]) -> dict[str, Any]:
    actual_pages, source, source_page_counts = extract_pdf_tokens(pair.file_path)
    azure = markdown_tokens(pair.azure_markdown)
    docling = markdown_tokens(pair.docling_markdown)
    source_grams = ngrams(source)
    azure_grams = ngrams(azure)
    docling_grams = ngrams(docling)
    az_source = set_score(azure_grams, source_grams)
    dc_source = set_score(docling_grams, source_grams)
    agreement = set_score(azure_grams, docling_grams)
    azure_raw = load_raw(pair.azure_raw)
    docling_raw = load_raw(pair.docling_raw)
    azure_projected = projected_tokens(azure_raw)
    docling_projected = projected_tokens(docling_raw)
    azure_projected_grams = ngrams(azure_projected)
    docling_projected_grams = ngrams(docling_projected)
    az_projected_source = set_score(azure_projected_grams, source_grams)
    dc_projected_source = set_score(docling_projected_grams, source_grams)
    projected_agreement = set_score(azure_projected_grams, docling_projected_grams)
    docling_pictures, docling_native_pages = docling_native_counts(docling_raw)
    azure_figures, azure_hyperlinks, azure_raw_pages = azure_structures(azure_raw)
    azure_table_diag = table_diagnostics(azure_raw)
    docling_table_diag = table_diagnostics(docling_raw)
    low_text_pages = sum(value < 10 for value in source_page_counts)
    output_tokens_per_page = max(len(azure), len(docling)) / max(1, actual_pages)
    ocr_heavy = low_text_pages / max(1, actual_pages) >= 0.5 and output_tokens_per_page >= 30
    return {
        "document_id": pair.document_id,
        "name": pair.name,
        "cohorts": membership,
        "language": pair.language,
        "size_bytes": pair.size_bytes,
        "actual_pages": actual_pages,
        "azure_pages": pair.azure_pages,
        "docling_pages": pair.docling_pages,
        "azure_seconds": pair.azure_seconds,
        "docling_seconds": pair.docling_seconds,
        "time_ratio_docling_to_azure": pair.docling_seconds / pair.azure_seconds if pair.azure_seconds else None,
        "azure_tables": pair.azure_tables,
        "docling_tables": pair.docling_tables,
        **{f"azure_table_{key}": value for key, value in azure_table_diag.items() if key != "tables"},
        **{f"docling_table_{key}": value for key, value in docling_table_diag.items() if key != "tables"},
        "azure_sections": pair.azure_sections,
        "docling_sections": pair.docling_sections,
        "azure_figures": azure_figures,
        "docling_native_pictures": docling_pictures,
        "azure_hyperlinks": azure_hyperlinks,
        "azure_raw_pages": azure_raw_pages,
        "docling_native_pages": docling_native_pages,
        "source_tokens": len(source),
        "azure_tokens": len(azure),
        "docling_tokens": len(docling),
        "azure_projected_tokens": len(azure_projected),
        "docling_projected_tokens": len(docling_projected),
        "source_low_text_pages": low_text_pages,
        "source_low_text_page_ratio": low_text_pages / max(1, actual_pages),
        "ocr_heavy": ocr_heavy,
        "azure_source_5gram_precision": az_source["precision"],
        "azure_source_5gram_recall": az_source["recall"],
        "azure_source_5gram_f1": az_source["f1"],
        "docling_source_5gram_precision": dc_source["precision"],
        "docling_source_5gram_recall": dc_source["recall"],
        "docling_source_5gram_f1": dc_source["f1"],
        "azure_docling_5gram_f1": agreement["f1"],
        "azure_projected_source_5gram_f1": az_projected_source["f1"],
        "docling_projected_source_5gram_f1": dc_projected_source["f1"],
        "azure_docling_projected_5gram_f1": projected_agreement["f1"],
        "azure_5gram_repetition_rate": repetition_rate(azure),
        "docling_5gram_repetition_rate": repetition_rate(docling),
    }


def cohort_summaries(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    names = sorted({name for row in rows for name in row["cohorts"]})
    result = []
    for name in names:
        selected = [row for row in rows if name in row["cohorts"]]
        born_digital = [row for row in selected if not row["ocr_heavy"]]
        azure_wins = sum(
            row["azure_source_5gram_f1"] > row["docling_source_5gram_f1"] + 0.002
            for row in born_digital
        )
        docling_wins = sum(
            row["docling_source_5gram_f1"] > row["azure_source_5gram_f1"] + 0.002
            for row in born_digital
        )
        azure_projected_wins = sum(
            row["azure_projected_source_5gram_f1"] > row["docling_projected_source_5gram_f1"] + 0.002
            for row in born_digital
        )
        docling_projected_wins = sum(
            row["docling_projected_source_5gram_f1"] > row["azure_projected_source_5gram_f1"] + 0.002
            for row in born_digital
        )
        result.append(
            {
                "cohort": name,
                "documents": len(selected),
                "ocr_heavy_documents": sum(row["ocr_heavy"] for row in selected),
                "azure_text_wins": azure_wins,
                "docling_text_wins": docling_wins,
                "text_ties": len(born_digital) - azure_wins - docling_wins,
                "azure_projected_text_wins": azure_projected_wins,
                "docling_projected_text_wins": docling_projected_wins,
                "projected_text_ties": len(born_digital) - azure_projected_wins - docling_projected_wins,
                "mean_azure_source_5gram_f1": statistics.mean(
                    row["azure_source_5gram_f1"] for row in born_digital
                ) if born_digital else None,
                "mean_docling_source_5gram_f1": statistics.mean(
                    row["docling_source_5gram_f1"] for row in born_digital
                ) if born_digital else None,
                "mean_azure_docling_5gram_f1": statistics.mean(
                    row["azure_docling_5gram_f1"] for row in selected
                ) if selected else None,
                "mean_azure_projected_source_5gram_f1": statistics.mean(
                    row["azure_projected_source_5gram_f1"] for row in born_digital
                ) if born_digital else None,
                "mean_docling_projected_source_5gram_f1": statistics.mean(
                    row["docling_projected_source_5gram_f1"] for row in born_digital
                ) if born_digital else None,
                "mean_azure_docling_projected_5gram_f1": statistics.mean(
                    row["azure_docling_projected_5gram_f1"] for row in selected
                ) if selected else None,
                "mean_time_ratio_docling_to_azure": statistics.mean(
                    row["time_ratio_docling_to_azure"] for row in selected
                    if row["time_ratio_docling_to_azure"] is not None
                ) if selected else None,
                "azure_pages": sum(row["azure_pages"] for row in selected),
                "docling_pages": sum(row["docling_pages"] for row in selected),
                "azure_tables": sum(row["azure_tables"] for row in selected),
                "docling_tables": sum(row["docling_tables"] for row in selected),
                "azure_figures": sum(row["azure_figures"] for row in selected),
                "docling_native_pictures": sum(row["docling_native_pictures"] for row in selected),
                "azure_table_cells": sum(row["azure_table_cells"] for row in selected),
                "docling_table_cells": sum(row["docling_table_cells"] for row in selected),
                "azure_table_empty_cell_ratio": (
                    sum(row["azure_table_empty_cells"] for row in selected)
                    / max(1, sum(row["azure_table_cells"] for row in selected))
                ),
                "docling_table_empty_cell_ratio": (
                    sum(row["docling_table_empty_cells"] for row in selected)
                    / max(1, sum(row["docling_table_cells"] for row in selected))
                ),
                "azure_table_invalid_cells": sum(row["azure_table_invalid_cells"] for row in selected),
                "docling_table_invalid_cells": sum(row["docling_table_invalid_cells"] for row in selected),
                "azure_table_duplicate_coordinates": sum(row["azure_table_duplicate_coordinates"] for row in selected),
                "docling_table_duplicate_coordinates": sum(row["docling_table_duplicate_coordinates"] for row in selected),
                "azure_multi_page_tables": sum(row["azure_table_multi_page_tables"] for row in selected),
                "docling_multi_page_tables": sum(row["docling_table_multi_page_tables"] for row in selected),
            }
        )
    return result


def analyzer_inventory(con: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT analyzer_id, api_version, status, COUNT(*),
               SUM(COALESCE(page_count,0)), SUM(COALESCE(table_count,0)),
               SUM(COALESCE(section_count,0)), SUM(COALESCE(warning_count,0)),
               SUM(COALESCE(elapsed_seconds,0))
        FROM analyses GROUP BY analyzer_id, api_version, status
        ORDER BY analyzer_id, status
        """
    ).fetchall()
    keys = ["analyzer_id", "api_version", "status", "documents", "pages", "tables", "sections", "warnings", "elapsed_seconds"]
    return [dict(zip(keys, row)) for row in rows]


def docling_reliability(state_path: Path) -> dict[str, Any]:
    if not state_path.is_file():
        return {}
    state = json.loads(state_path.read_text(encoding="utf-8"))
    documents = {k: v for k, v in (state.get("documents") or {}).items() if isinstance(v, dict)}
    succeeded = {k: v for k, v in documents.items() if "completed_at" in v}
    quarantined = {k: v for k, v in documents.items() if v.get("quarantined")}
    unfinished = {k: v for k, v in documents.items() if k not in succeeded and k not in quarantined}
    attempts = lambda value: int(value.get("attempts") or 0)
    return {
        "state_updated_at": state.get("updated_at"),
        "tracked": len(documents),
        "succeeded": len(succeeded),
        "first_attempt_successes": sum(attempts(v) == 1 for v in succeeded.values()),
        "retry_successes": sum(attempts(v) > 1 for v in succeeded.values()),
        "failed_attempts_before_success": sum(max(0, attempts(v) - 1) for v in succeeded.values()),
        "quarantined": len(quarantined),
        "quarantine_attempts": sum(attempts(v) for v in quarantined.values()),
        "timeout_attempts": sum(int(v.get("timeout_count") or 0) for v in documents.values()),
        "unfinished": len(unfinished),
        "unfinished_documents": [{"document_id": k, **v} for k, v in unfinished.items()],
        "quarantined_documents": [{"document_id": k, **v} for k, v in quarantined.items()],
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            value = dict(row)
            if isinstance(value.get("cohorts"), list):
                value["cohorts"] = ";".join(value["cohorts"])
            writer.writerow(value)


def main() -> int:
    args = parse_args()
    started = time.monotonic()
    args.output.mkdir(parents=True, exist_ok=True)
    normalized = read_normalized_metadata(args.workspace / "4_normalize" / "documents.jsonl")
    con = sqlite3.connect(args.db)
    try:
        pairs = load_pairs(con, normalized)
        print(f"Loaded {len(pairs)} paired PDFs")
        for index, pair in enumerate(pairs, 1):
            probe_pdf(pair)
            pair.azure_token_count = len(markdown_tokens(pair.azure_markdown))
            pair.docling_token_count = len(markdown_tokens(pair.docling_markdown))
            if index % 250 == 0:
                print(f"Probed {index}/{len(pairs)} paired PDFs", flush=True)
        benchmark, membership = select_benchmark(pairs, args)
        print(f"Selected {len(benchmark)} unique benchmark documents")
        benchmark_rows = []
        for index, pair in enumerate(sorted(benchmark, key=lambda x: int(x.document_id)), 1):
            print(f"Benchmark {index}/{len(benchmark)}: {pair.document_id}", flush=True)
            benchmark_rows.append(benchmark_one(pair, membership[pair.document_id]))
        cohorts = cohort_summaries(benchmark_rows)
        paired_pages_azure = sum(x.azure_pages for x in pairs)
        paired_pages_docling = sum(x.docling_pages for x in pairs)
        paired_seconds_azure = sum(x.azure_seconds for x in pairs)
        paired_seconds_docling = sum(x.docling_seconds for x in pairs)
        corpus = {
            "paired_pdf_documents": len(pairs),
            "paired_source_pages": sum(x.actual_pages or 0 for x in pairs),
            "paired_azure_pages": paired_pages_azure,
            "paired_docling_pages": paired_pages_docling,
            "paired_exact_azure_source_pages": sum(x.azure_pages == x.actual_pages for x in pairs),
            "paired_exact_docling_source_pages": sum(x.docling_pages == x.actual_pages for x in pairs),
            "paired_azure_seconds": paired_seconds_azure,
            "paired_docling_seconds": paired_seconds_docling,
            "paired_time_ratio_docling_to_azure": paired_seconds_docling / paired_seconds_azure,
            "paired_azure_pages_per_hour": paired_pages_azure / (paired_seconds_azure / 3600),
            "paired_docling_pages_per_hour": paired_pages_docling / (paired_seconds_docling / 3600),
            "paired_azure_tables": sum(x.azure_tables for x in pairs),
            "paired_docling_tables": sum(x.docling_tables for x in pairs),
            "paired_azure_sections": sum(x.azure_sections for x in pairs),
            "paired_docling_sections": sum(x.docling_sections for x in pairs),
            "page_delta_docling_minus_source": summary(
                (x.docling_pages - (x.actual_pages or 0)) for x in pairs
            ),
            "time_ratio_per_document": summary(
                x.docling_seconds / x.azure_seconds for x in pairs if x.azure_seconds
            ),
        }
        metrics = {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "methodology_version": 1,
            "elapsed_seconds": time.monotonic() - started,
            "corpus": corpus,
            "analyzers": analyzer_inventory(con),
            "docling_reliability": docling_reliability(
                args.workspace / "3_analyze" / "docling" / "supervisor-state.json"
            ),
            "benchmark": {
                "documents": len(benchmark_rows),
                "ocr_heavy_documents": sum(row["ocr_heavy"] for row in benchmark_rows),
                "cohorts": cohorts,
            },
        }
        (args.output / "metrics.json").write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        write_csv(args.output / "benchmark-documents.csv", benchmark_rows)
        write_csv(args.output / "cohort-summary.csv", cohorts)
        large_rows = [row for row in benchmark_rows if "largest" in row["cohorts"]]
        large_rows.sort(key=lambda x: x["actual_pages"], reverse=True)
        write_csv(args.output / "large-documents.csv", large_rows)
        probe_rows = [
            {
                "document_id": x.document_id, "name": x.name,
                "actual_pages": x.actual_pages, "azure_pages": x.azure_pages,
                "docling_pages": x.docling_pages,
                "probe_tokens_per_page": x.probe_tokens_per_page,
                "azure_tokens": x.azure_token_count,
                "docling_tokens": x.docling_token_count,
            }
            for x in pairs
        ]
        write_csv(args.output / "paired-page-probes.csv", probe_rows)
        print(f"Wrote evaluation package to {args.output}")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
