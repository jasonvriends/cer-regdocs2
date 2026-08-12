#!/usr/bin/env python3
"""Render representative PDF pages and capture Azure/Docling page excerpts."""

from __future__ import annotations

import collections
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium


OUTPUT = Path("reports/azure-docling/assets")
SOURCE_PAGE_RE = re.compile(r"D\((\d+),")

# These cases deliberately exercise different failure modes and strengths.
CASES = {
    "4647200": {"label": "largest Azure-only document", "page": 301},
    "4600563": {"label": "largest successful paired document", "page": 1},
    "4576114": {"label": "duplicated hidden text layer", "page": 50},
    "4656932": {"label": "dense tables", "page": "table"},
    "4647207": {"label": "image-heavy with empty Docling Markdown", "page": "figure"},
    "4049587": {"label": "OCR-heavy financial statement", "page": "ocr"},
    "4648190": {"label": "French large report", "page": 25},
    "4662461": {"label": "mixed tables and figures", "page": "mixed"},
}


def source_pages(source: Any) -> list[int]:
    return [int(x) for x in SOURCE_PAGE_RE.findall(str(source or ""))]


def load_json(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    with Path(path).open(encoding="utf-8") as stream:
        return json.load(stream)


def azure_page_text(value: dict[str, Any], page_number: int) -> str:
    for content in value.get("contents") or []:
        for page in content.get("pages") or []:
            if int(page.get("pageNumber") or 0) == page_number:
                return "\n".join(str(x.get("content") or "") for x in page.get("lines") or [])
    return ""


def docling_page_text(value: dict[str, Any], page_number: int) -> str:
    parts: list[str] = []
    for content in value.get("contents") or []:
        for paragraph in content.get("paragraphs") or []:
            if page_number in source_pages(paragraph.get("source")):
                parts.append(str(paragraph.get("content") or ""))
        for table in content.get("tables") or []:
            if page_number in source_pages(table.get("source")):
                parts.extend(str(cell.get("content") or "") for cell in table.get("cells") or [])
    return "\n".join(x for x in parts if x)


def structure_counts(value: dict[str, Any], page_number: int) -> dict[str, int]:
    counts = {"tables": 0, "figures": 0, "paragraphs": 0}
    for content in value.get("contents") or []:
        for key in counts:
            for item in content.get(key) or []:
                if page_number in source_pages(item.get("source")):
                    counts[key] += 1
    return counts


def choose_page(mode: str | int, pdf_path: str, azure: dict[str, Any]) -> int:
    if isinstance(mode, int):
        return mode
    score: collections.Counter[int] = collections.Counter()
    for content in azure.get("contents") or []:
        if mode in {"table", "mixed"}:
            for item in content.get("tables") or []:
                for page in source_pages(item.get("source")):
                    score[page] += 3
        if mode in {"figure", "mixed"}:
            for item in content.get("figures") or []:
                for page in source_pages(item.get("source")):
                    score[page] += 2
    if mode == "ocr":
        document = pdfium.PdfDocument(pdf_path)
        for content in azure.get("contents") or []:
            for page in content.get("pages") or []:
                number = int(page.get("pageNumber") or 0)
                if not 1 <= number <= len(document):
                    continue
                pdf_page = document[number - 1]
                textpage = pdf_page.get_textpage()
                embedded = len(textpage.get_text_range().split())
                azure_words = len(page.get("words") or [])
                score[number] = azure_words if embedded < 10 else 0
                textpage.close()
                pdf_page.close()
        document.close()
    return score.most_common(1)[0][0] if score else 1


def render_page(pdf_path: str, page_number: int, output_path: Path) -> None:
    document = pdfium.PdfDocument(pdf_path)
    page = document[page_number - 1]
    bitmap = page.render(scale=1.5)
    image = bitmap.to_pil()
    image.save(output_path)
    bitmap.close()
    page.close()
    document.close()


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect("database/regdocs.db")
    con.row_factory = sqlite3.Row
    audits = []
    try:
        for document_id, case in CASES.items():
            rows = con.execute(
                """
                SELECT f.path, a.analyzer_id, a.raw_json_path, a.markdown_path,
                       a.page_count, a.table_count, a.elapsed_seconds
                FROM files f JOIN analyses a ON a.file_id=f.id AND a.file_sha256=f.sha256
                WHERE f.is_current=1 AND f.document_id=? AND a.status='SUCCEEDED'
                  AND a.analyzer_id IN ('prebuilt-layout','docling-standard')
                """,
                (document_id,),
            ).fetchall()
            by_analyzer = {str(row["analyzer_id"]): row for row in rows}
            azure_row = by_analyzer.get("prebuilt-layout")
            docling_row = by_analyzer.get("docling-standard")
            if azure_row is None:
                continue
            azure = load_json(azure_row["raw_json_path"])
            docling = load_json(docling_row["raw_json_path"] if docling_row else None)
            page_number = choose_page(case["page"], azure_row["path"], azure)
            image_name = f"{document_id}-page-{page_number:04d}.png"
            render_page(azure_row["path"], page_number, OUTPUT / image_name)
            az_text = azure_page_text(azure, page_number)
            dc_text = docling_page_text(docling, page_number)
            audits.append(
                {
                    "document_id": document_id,
                    "label": case["label"],
                    "page_number": page_number,
                    "image": f"assets/{image_name}",
                    "azure_page_characters": len(az_text),
                    "docling_page_characters": len(dc_text),
                    "azure_structures": structure_counts(azure, page_number),
                    "docling_structures": structure_counts(docling, page_number),
                    "azure_excerpt": az_text[:2500],
                    "docling_excerpt": dc_text[:2500],
                    "azure_document_pages": azure_row["page_count"],
                    "docling_document_pages": docling_row["page_count"] if docling_row else None,
                    "azure_seconds": azure_row["elapsed_seconds"],
                    "docling_seconds": docling_row["elapsed_seconds"] if docling_row else None,
                }
            )
    finally:
        con.close()
    Path("reports/azure-docling/visual-audit.json").write_text(
        json.dumps(audits, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Rendered {len(audits)} audit pages to {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
