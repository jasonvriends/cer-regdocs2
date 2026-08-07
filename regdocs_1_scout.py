#!/usr/bin/env python3
"""Scout file-level metadata from Canada Energy Regulator REGDOCS.

PURPOSE
=======

This is Stage 1 of a simple document-processing pipeline:

    scout -> download -> document intelligence -> export

The scout answers two questions for every item returned by a REGDOCS date
search:

1. What metadata does REGDOCS expose without downloading the file?
2. What is the current pipeline status for that item?

It fetches only:

* the selected date-range search results;
* the explicit member result fragment loaded by each selected or nested container page;
* the live Advanced Search facet catalogue and filtered facet result pages;
* each selected or container-discovered item's own REGDOCS detail page.

Container expansion is deliberately narrow. REGDOCS items explicitly labelled
``Compound Document`` or ``Folder`` are treated as container shells. The shell explicitly declares the AJAX endpoint that
loads the filing's result list (for example ``/REGDOCS/Item/LoadResult/<id>``).
The scout fetches only that declared result fragment, parses its explicit rows,
adds those stable member IDs to the document ledger, records membership in
JSON, and queues any explicit child Folder or Compound Document. A seen set,
maximum depth, and maximum container count make nested traversal loop-safe. It
does not follow company, project, taxonomy, breadcrumb, navigation, or arbitrary
outbound links.

The numeric REGDOCS item ID is the stable identity key for normal items. When
REGDOCS reuses one placeholder ID for several paper-only rows, the scout assigns
a stable synthetic ID based on the parent container and exhibit number while
preserving the original numeric value in metadata. If the same normal item is
found first through a container and later through a date search (or the reverse),
the existing row is updated rather than duplicated. Download and downstream
stage state are preserved.

SQLITE DESIGN
=============

The database is a pipeline ledger, not an application search engine. The scout
creates only five user tables:

    documents       One row per REGDOCS item, metadata, and stage statuses.
    runs            Run history, live progress, heartbeat, counters, summary.
    errors          Structured warnings and errors for every pipeline stage.
    raw_snapshots   Pointers to compressed REGDOCS source responses.
    files           Downloaded file facts; empty until the download stage writes them.

All REGDOCS metadata for an item is projected into ``documents.metadata`` as
JSON. Facets and detail-page fields are arrays/objects inside that JSON instead
of separate normalized tables. This keeps the database easy to inspect and
makes later ``document-id.pdf`` + ``document-id.json`` export straightforward.

RAW SOURCE ARCHIVE
==================

Successful HTML responses are gzip-compressed and stored by SHA-256:

    raw/regdocs/
      advanced/<prefix>/<sha256>.html.gz
      search/<prefix>/<sha256>.html.gz
      facet/<prefix>/<sha256>.html.gz
      detail/<prefix>/<sha256>.html.gz
      container/<prefix>/<sha256>.html.gz  # AJAX member-list fragments

Keep this directory with the database. It is the evidence behind the metadata
and allows parser improvements without losing the original REGDOCS response.
The database stores relative paths so the database and raw archive can be moved
together.

DEFAULT GOLD PROFILE
====================

Running with no arguments uses the production defaults:

    python regdocs_1_scout.py

On a new database it scouts January 1 of the current year through today. Later
runs normally refresh a seven-day overlap from the newest stored filing date.
At least every 30 days it refreshes the full current year.

The defaults traverse explicit nested members of Compound Document and Folder items,
collect all live facets, fetch each selected or container-discovered item's own detail page,
preserve raw responses, use one globally paced request worker, wait a random
2-4 seconds between request starts, retry temporary failures four times, and
reuse successful detail metadata for 30 days.

PROGRESS
========

During base discovery, progress reports pages checked, estimated page count,
unique records parsed, failed pages, and the latest result offset. The estimate is
derived from REGDOCS' approximate total and can grow when a tail probe finds more rows.

Live status is persisted to SQLite and atomically projected to:

    _audit/scout-progress.json

Detailed logs are written to:

    _audit/scout.log

Monitor a running scout from another terminal:

    watch -n 5 'python regdocs_1_scout.py --status'

DEPENDENCIES
============

    pip install httpx beautifulsoup4 tqdm

Optional faster HTML parser:

    pip install lxml

USEFUL COMMANDS
===============

    python regdocs_1_scout.py --self-test
    python regdocs_1_scout.py --show-defaults
    python regdocs_1_scout.py --status
    python regdocs_1_scout.py --status-json
    python regdocs_1_scout.py --audit
    python regdocs_1_scout.py --start-date 2025-01-01 --end-date 2025-12-31
    python regdocs_1_scout.py --repair-containers
"""

from __future__ import annotations

import argparse
import asyncio
import calendar
import contextlib
import gzip
import hashlib
import json
import logging
import os
import random
import re
import sqlite3
import sys
import tempfile
import time
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urljoin

try:
    import httpx
except ImportError as exc:
    raise SystemExit("Missing dependency 'httpx'. Install with: pip install httpx") from exc

try:
    from bs4 import BeautifulSoup, Tag
except ImportError as exc:
    raise SystemExit(
        "Missing dependency 'beautifulsoup4'. Install with: pip install beautifulsoup4"
    ) from exc

try:
    from tqdm import tqdm
except ImportError:
    class tqdm:  # type: ignore[no-redef]
        def __init__(self, iterable=None, total=None, **_: Any):
            self.iterable = iterable
            self.total = total
        def __iter__(self):
            return iter(self.iterable or [])
        def __enter__(self):
            return self
        def __exit__(self, *_: Any):
            return None
        def update(self, _: int = 1) -> None:
            return None

SCRIPT_VERSION = "1.1.1"
PARSER_VERSION = "document-ledger-scout-2026-08-07-v1-container-tree-audit-progress"
SCHEMA_VERSION = 2
EXPECTED_USER_TABLES = {"documents", "runs", "errors", "raw_snapshots", "files"}

DOMAIN = "https://apps.cer-rec.gc.ca"
ADVANCED_URL = f"{DOMAIN}/REGDOCS/Search/Advanced"
RESULTS_URL = f"{DOMAIN}/REGDOCS/Search/SearchAdvancedResults"
DETAIL_URL_TEMPLATE = f"{DOMAIN}/REGDOCS/Item/View/{{item_id}}"

PAGE_SIZES = (20, 50, 100, 200)
SORT_OLDEST_FIRST = 21
RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-CA,en;q=0.9,fr-CA;q=0.7,fr;q=0.6",
}
AJAX_HEADERS = {**HEADERS, "X-Requested-With": "XMLHttpRequest"}

ITEM_HREF_RE = re.compile(r"/REGDOCS/(Item/View|File/Download)/(\d+)", re.IGNORECASE)
TOTAL_RE = re.compile(
    r"Item\(s\)\s*-\s*[\d,]+\s*to\s*[\d,]+\s*out of about\s*([\d,]+)",
    re.IGNORECASE,
)
FILING_RE = re.compile(r"Filing:\s*(\S+)", re.IGNORECASE)
WHITESPACE_RE = re.compile(r"\s+")
NON_WORD_RE = re.compile(r"[^a-z0-9]+")
DATE_TOKEN_RE = re.compile(r"\b((?:19|20)\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b")
FILING_TITLE_RE = re.compile(r"\b(C\d{4,})(?:-(\d{1,4}))?\b", re.IGNORECASE)
EXHIBIT_RE = re.compile(r"\b(A(?=[0-9A-Z]{5,}\b)(?=[0-9A-Z]*\d)[0-9A-Z]{5,})\b", re.IGNORECASE)
ACTIVITY_RE = re.compile(
    r"\b([A-Z]{2,8}(?:-[A-Z]{2,8})*[- ]?\d{2,4}(?:-\d{1,4})+)\b",
    re.IGNORECASE,
)
REGULATORY_ID_RE = re.compile(
    r"\b((?:AO|GO|GPSO|EPR|MO|RO|XG|GC|GH|OH|MH|RH|OF|OM)"
    r"[A-Z0-9]*(?:-[A-Z0-9]+)+)\b",
    re.IGNORECASE,
)
NO_RESULTS_RE = re.compile(
    r"(?:no\s+(?:items?|results?|records?|documents?)\s+(?:(?:were|are)\s+)?(?:found|available)|"
    r"there\s+(?:are|were)\s+no\s+(?:items?|results?|records?|documents?)|"
    r"no\s+(?:items?|results?|records?|documents?)\s+to\s+display|"
    r"aucun(?:e)?\s+(?:élément|résultat|document)(?:\s+n['’]a\s+été\s+trouvé)?)",
    re.IGNORECASE,
)

BOILERPLATE_LABELS = {
    "viewport", "description", "dcterms creator", "dcterms issued",
    "dcterms modified", "dcterms subject",
}
BOILERPLATE_VALUES = {
    "add to favourites",
    "cer regulatory document index / index des documents de réglementation",
    "width=device-width,initial-scale=1",
    "width=device-width, initial-scale=1",
    "french name of the content author / nom en français de l'auteur du contenu",
    "french subject terms / termes de sujet en français",
    "date published (2016-12-23) / date de publication (2016-12-23)",
    "date modified (2017-03-23) / date de modification (2017-03-23)",
}
BOILERPLATE_HEADING_FRAGMENTS = {
    "add to favourites", "ajouter aux favoris", "skip to main content",
    "passer au contenu principal",
}

KNOWN_FACET_FIELDS = {
    "Document Type": "document_types",
    "Application Type": "application_types",
    "File Type": "file_types",
    "Role": "roles",
    "Commodity": "commodities",
}

FIELD_ALIASES = {
    "title": "title", "document title": "title", "name": "title",
    "date": "date", "document date": "date", "filing date": "filing_date",
    "submitted": "filing_date", "submission date": "filing_date",
    "submitter": "submitter", "submitted by": "submitter",
    "company": "company", "organization": "organization",
    "organisation": "organization", "applicant": "applicant",
    "project": "project", "filing": "filing_number",
    "filing number": "filing_number", "filing no": "filing_number",
    "filing id": "filing_id", "document type": "document_type",
    "application type": "application_type", "file type": "file_type",
    "role": "role", "commodity": "commodity", "language": "language",
    "pages": "page_count", "page count": "page_count",
    "number of pages": "page_count", "description": "description",
    "subject": "subject", "author": "author", "contact": "contact",
    "hearing order": "hearing_order", "docket": "docket",
    "status": "regulatory_status",
}

BASE_METADATA_KEYS = {
    "id", "name", "url", "is_file", "date", "submitter", "kind",
    "filing_number", "filing_id", "company", "company_id", "project",
    "project_id", "snippet", "search_row_text", "scraped_at",
    "search_range", "base_search_complete", "regdocs_item_id",
    "identity_kind", "source_container_id", "exhibit_number",
}

try:
    import lxml  # noqa: F401
    SOUP_PARSER = "lxml"
except ImportError:
    SOUP_PARSER = "html.parser"


def utc_now() -> str:
    """Return a compact, timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def clean_text(value: Any) -> str:
    """Normalize visible HTML or database text without changing its meaning."""
    if value is None:
        return ""
    return WHITESPACE_RE.sub(" ", str(value)).strip()

def is_container_kind(value: Any) -> bool:
    """Return True for explicit REGDOCS container item kinds.

    Only ``Compound Document`` and ``Folder`` are expanded. Their own item page
    must declare a ``/REGDOCS/Item/LoadResult/<id>`` endpoint; arbitrary links
    are never followed.
    """
    return clean_text(value).casefold() in {"compound document", "folder"}


def is_paper_only_text(value: Any) -> bool:
    """Return True when a REGDOCS row explicitly identifies a paper-only item."""
    text = clean_text(value).casefold()
    return "paper only" in text or "papier seulement" in text


def explicit_empty_result(html: str) -> bool:
    """Return True only when the fragment explicitly states that it has no rows."""
    if not clean_text(html):
        return False
    soup = BeautifulSoup(html, SOUP_PARSER)
    return bool(NO_RESULTS_RE.search(clean_text(soup.get_text(" ", strip=True))))


def synthetic_container_row_id(
    *, container_id: str, regdocs_item_id: str, name: str, row_text: str,
    paper_only: bool, used_ids: set[str],
) -> tuple[str, str | None]:
    """Build a stable identity for a non-unique container row.

    REGDOCS sometimes assigns the same placeholder numeric item ID to several
    distinct paper-only rows. The exhibit number is preferred because it is
    stable and human-auditable. A content hash is used when no exhibit appears.
    """
    combined = clean_text(f"{name} {row_text}")
    match = EXHIBIT_RE.search(combined)
    exhibit = match.group(1).upper() if match else None
    if exhibit:
        token = exhibit
    else:
        fingerprint = clean_text(f"{regdocs_item_id}|{name}|{row_text}").casefold()
        token = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:16]
    prefix = "paper" if paper_only else "container-row"
    candidate = f"{prefix}:{container_id}:{token}"
    if candidate in used_ids:
        suffix = hashlib.sha256(combined.encode("utf-8")).hexdigest()[:8]
        candidate = f"{candidate}:{suffix}"
    used_ids.add(candidate)
    return candidate, exhibit


def slugify(value: str) -> str:
    """Convert a label into a stable lower-case identifier."""
    return NON_WORD_RE.sub("-", clean_text(value).casefold()).strip("-")

def facet_field(category: str) -> str:
    """Return the JSON metadata key for a facet category."""
    return KNOWN_FACET_FIELDS.get(category, slugify(category).replace("-", "_"))

def normalize_label(label: str) -> str:
    """Normalize a displayed label for alias matching, not for presentation."""
    text = clean_text(label).casefold().rstrip(":")
    text = re.sub(r"[\[\](){}]", " ", text)
    text = NON_WORD_RE.sub(" ", text)
    return clean_text(text)

def canonical_field_for_label(label: str) -> str | None:
    """Map a high-confidence REGDOCS label to a stable field name."""
    normalized = normalize_label(label)
    if normalized in FIELD_ALIASES:
        return FIELD_ALIASES[normalized]
    # Restrained pattern matching for labels that include harmless suffixes.
    if normalized.startswith("filing number"):
        return "filing_number"
    if normalized.startswith("document type"):
        return "document_type"
    if normalized.startswith("application type"):
        return "application_type"
    if normalized.endswith(" page count"):
        return "page_count"
    return None

def is_boilerplate_field(label: str, value: str) -> bool:
    """Return True for site-template metadata that is not item metadata.

    The raw HTML is always preserved, so filtering here is lossless from an
    audit perspective. The goal is only to keep normalized metadata and
    sidecars free of Government-of-Canada template placeholders.
    """
    label_key = normalize_label(label)
    value_key = clean_text(value).casefold()
    if value_key in BOILERPLATE_VALUES:
        return True
    if label_key in BOILERPLATE_LABELS:
        # Keep real item-specific values when a generic Dublin Core label is
        # genuinely populated. Placeholder/template prose is filtered above.
        if label_key == "description" and value_key not in BOILERPLATE_VALUES:
            return False
        if label_key.startswith("dcterms") and value_key not in BOILERPLATE_VALUES:
            return False
        return True
    if any(fragment in value_key for fragment in BOILERPLATE_HEADING_FRAGMENTS):
        return True
    return False

def cleaned_page_title(value: str | None) -> str | None:
    """Remove the fixed REGDOCS site prefix from a browser page title."""
    text = clean_text(value)
    if not text:
        return None
    text = re.sub(
        r"^Canada Energy Regulator\s*-\s*REGDOCS\s*-\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"^Régie de l'énergie du Canada\s*-\s*REGDOCS\s*-\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return clean_text(text) or None

def explicit_title_language(title: str) -> str | None:
    """Return an explicit EN/FR language marker without linguistic guessing."""
    text = f" {clean_text(title)} "
    if re.search(r"(?:^|\s|[-–—_/])(FR|FRENCH|FRANÇAIS)(?:\s|[-–—_/]|$)", text, re.I):
        return "fr"
    if re.search(r"(?:^|\s|[-–—_/])(EN|ENGLISH|ANGLAIS)(?:\s|[-–—_/]|$)", text, re.I):
        return "en"
    return None

def extract_title_identifiers(title: str) -> list[dict[str, Any]]:
    """Extract high-confidence identifiers displayed in a REGDOCS title.

    These are labelled ``title_pattern`` in provenance. They are useful for
    metadata lookup and export but are never presented as API-supplied structured fields.
    """
    text = clean_text(title)
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    filing = FILING_TITLE_RE.search(text)
    if filing:
        value = filing.group(1).upper()
        seen.add(("filing_number", value))
        output.append({"type": "filing_number", "value": value, "confidence": 0.99})
        if filing.group(2):
            output.append(
                {
                    "type": "filing_sequence",
                    "value": filing.group(2),
                    "confidence": 0.99,
                }
            )

    for match in EXHIBIT_RE.finditer(text):
        value = match.group(1).upper()
        key = ("exhibit_number", value)
        if key not in seen:
            seen.add(key)
            output.append({"type": key[0], "value": value, "confidence": 0.98})

    for match in REGULATORY_ID_RE.finditer(text):
        value = match.group(1).upper()
        identifier_type = (
            "regulatory_instrument_number"
            if value.startswith(("AO-", "GO-", "GPSO-", "EPR-", "MO-", "RO-"))
            else "activity_number"
        )
        key = (identifier_type, value)
        if key not in seen:
            seen.add(key)
            output.append({"type": key[0], "value": value, "confidence": 0.97})

    for match in ACTIVITY_RE.finditer(text):
        value = clean_text(match.group(1)).upper().replace(" ", "-")
        if value.startswith("C") and value[1:].replace("-", "").isdigit():
            continue
        identifier_type = "activity_number"
        if any(prefix in value for prefix in ("ORDER", "GPSO", "AO-", "GO-")):
            identifier_type = "regulatory_instrument_number"
        key = (identifier_type, value)
        if key not in seen:
            seen.add(key)
            output.append({"type": key[0], "value": value, "confidence": 0.95})

    language = explicit_title_language(text)
    if language:
        output.append({"type": "language_marker", "value": language, "confidence": 1.0})
    return output

def normalize_date(value: str) -> str | None:
    """Best-effort ISO date normalization while preserving the raw source value."""
    text = clean_text(value)
    if not text:
        return None
    candidate = text.split("T", 1)[0]
    formats = (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y.%m.%d",
        "%d/%m/%Y",
        "%m/%d/%Y",
        "%B %d, %Y",
        "%b %d, %Y",
        "%d %B %Y",
        "%d %b %Y",
        "%Y-%m-%d %H:%M:%S",
    )
    for fmt in formats:
        try:
            return datetime.strptime(candidate, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    match = DATE_TOKEN_RE.search(candidate)
    if match:
        year, month, day = map(int, match.groups())
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return None
    return None

def validate_date(value: str) -> str:
    """Validate YYYY-MM-DD and clamp an overflowing day to month end."""
    parts = value.split("-")
    if len(parts) != 3:
        raise ValueError(f"Invalid date '{value}'; expected YYYY-MM-DD")
    try:
        year, month, day = map(int, parts)
    except ValueError as exc:
        raise ValueError(f"Invalid date '{value}'; expected YYYY-MM-DD") from exc
    if month < 1 or month > 12:
        raise ValueError(f"Invalid date '{value}'; month must be 1-12")
    max_day = calendar.monthrange(year, month)[1]
    if day < 1:
        raise ValueError(f"Invalid date '{value}'; day must be at least 1")
    day = min(day, max_day)
    return f"{year:04d}-{month:02d}-{day:02d}"

def sha256_bytes(payload: bytes) -> str:
    """Return the SHA-256 digest of a byte string."""
    return hashlib.sha256(payload).hexdigest()

def json_dumps(value: Any, *, pretty: bool = False) -> str:
    """Serialize JSON consistently for storage and hashing."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=not pretty,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
        default=str,
    )

def parse_json_object(raw: Any) -> dict[str, Any]:
    """Parse a JSON object, returning an empty object for malformed input."""
    if isinstance(raw, Mapping):
        return dict(raw)
    if raw in (None, ""):
        return {}
    try:
        parsed = json.loads(str(raw))
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}

def dedupe_strings(values: Iterable[Any]) -> list[str]:
    """Return stable, case-insensitive de-duplicated strings."""
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = clean_text(value)
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            output.append(text)
    return output

def absolute_regdocs_url(href: str) -> str:
    """Convert a REGDOCS-relative link into an absolute URL."""
    return urljoin(DOMAIN, href)

def item_reference_from_url(url: str) -> tuple[str, str] | None:
    """Return ``(link_kind, item_id)`` for a REGDOCS item/download URL."""
    match = ITEM_HREF_RE.search(url)
    if not match:
        return None
    link_kind = "download" if match.group(1).casefold() == "file/download" else "item"
    return link_kind, match.group(2)

def configure_logging(verbose: bool, log_file: Path | None = None) -> None:
    """Configure console logging and an optional durable log file."""
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
        force=True,
    )
    if not verbose:
        logging.getLogger("httpx").setLevel(logging.WARNING)

def atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    """Atomically replace a small JSON status file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(json_dumps(value, pretty=True) + "\n")
            stream.flush()
            with contextlib.suppress(OSError):
                os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class ProgressMonitor:
    """Persist throttled live progress to SQLite and an atomic JSON file."""

    def __init__(
        self,
        *,
        db: "PipelineDB",
        run_id: int,
        progress_file: Path,
        database_path: Path,
        persist_interval: float = 1.0,
        log_interval: float = 60.0,
    ):
        self.db = db
        self.run_id = run_id
        self.progress_file = progress_file
        self.database_path = database_path
        self.persist_interval = max(0.2, persist_interval)
        self.log_interval = max(10.0, log_interval)
        self.started_monotonic = time.monotonic()
        self.phase_started_monotonic = self.started_monotonic
        self.phase = "starting"
        self.message = "Initializing scout"
        self.completed_units = 0
        self.total_units: int | None = None
        self.logical_requests = 0
        self.http_attempts = 0
        self.successful_requests = 0
        self.failed_requests = 0
        self.retries = 0
        self._last_persist = 0.0
        self._last_log = 0.0
        self._finished_status: str | None = None
        self.persist(force=True)

    def set_phase(self, phase: str, *, total: int | None = None, message: str | None = None) -> None:
        self.phase = clean_text(phase) or "working"
        self.message = clean_text(message) or self.phase.replace("_", " ").title()
        self.completed_units = 0
        self.total_units = total
        self.phase_started_monotonic = time.monotonic()
        self.persist(force=True)

    def set_total(self, total: int | None) -> None:
        self.total_units = total
        self.persist()

    def advance(self, units: int = 1, *, message: str | None = None) -> None:
        self.completed_units += max(0, units)
        if message:
            self.message = clean_text(message)
        self.persist()

    def logical_request_started(self) -> None:
        self.logical_requests += 1
        self.persist()

    def attempt_started(self, attempt: int) -> None:
        self.http_attempts += 1
        if attempt > 1:
            self.retries += 1
        self.persist()

    def attempt_finished(self, *, ok: bool) -> None:
        if ok:
            self.successful_requests += 1
        else:
            self.failed_requests += 1
        self.persist()

    def snapshot(self, *, heartbeat_at: str | None = None) -> dict[str, Any]:
        now = time.monotonic()
        elapsed = max(now - self.started_monotonic, 0.001)
        phase_elapsed = max(now - self.phase_started_monotonic, 0.001)
        rate = self.completed_units / phase_elapsed if self.completed_units else 0.0
        percentage = None
        eta_seconds = None
        if self.total_units is not None and self.total_units > 0:
            percentage = min(100.0, self.completed_units * 100.0 / self.total_units)
            remaining = max(0, self.total_units - self.completed_units)
            if rate > 0:
                eta_seconds = remaining / rate
        return {
            "run_id": self.run_id,
            "status": self._finished_status or "RUNNING",
            "phase": self.phase,
            "message": self.message,
            "heartbeat_at": heartbeat_at or utc_now(),
            "completed_units": self.completed_units,
            "total_units": self.total_units,
            "percentage": round(percentage, 2) if percentage is not None else None,
            "rate_per_second": round(rate, 3),
            "eta_seconds": round(eta_seconds, 1) if eta_seconds is not None else None,
            "elapsed_seconds": round(elapsed, 1),
            "logical_requests": self.logical_requests,
            "http_attempts": self.http_attempts,
            "successful_requests": self.successful_requests,
            "failed_requests": self.failed_requests,
            "retries": self.retries,
            "database": str(self.database_path),
            "progress_file": str(self.progress_file),
        }

    def persist(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_persist < self.persist_interval:
            return
        state = self.snapshot(heartbeat_at=utc_now())
        self.db.update_progress(self.run_id, state)
        atomic_write_json(self.progress_file, state)
        self._last_persist = now
        if force or now - self._last_log >= self.log_interval:
            total = state["total_units"]
            completed = state["completed_units"]
            percent = state["percentage"]
            progress_text = (
                f"{completed:,}/{total:,} ({percent:.1f}%)"
                if total is not None and percent is not None
                else f"{completed:,} completed"
            )
            eta = state["eta_seconds"]
            eta_text = f", ETA {eta:.0f}s" if eta is not None else ""
            logging.info(
                "Progress [%s]: %s; %s; requests=%d attempts=%d retries=%d failures=%d%s",
                self.phase, self.message, progress_text, self.logical_requests,
                self.http_attempts, self.retries, self.failed_requests, eta_text,
            )
            self._last_log = now

    def finish(self, status: str, *, message: str | None = None) -> None:
        self._finished_status = status
        self.phase = status.casefold()
        self.message = clean_text(message) or f"Run {status}"
        self.persist(force=True)


@dataclass
class StageLock:
    """Exclusive lock file preventing concurrent scout writers."""
    path: Path
    force: bool = False
    _owned: bool = False

    def __enter__(self) -> "StageLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.force and self.path.exists():
            self.path.unlink()
        payload = {"pid": os.getpid(), "created_at_utc": utc_now(), "command": " ".join(sys.argv)}
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            detail = ""
            with contextlib.suppress(OSError):
                detail = self.path.read_text(encoding="utf-8")
            raise RuntimeError(
                f"Scout lock already exists: {self.path}. Verify no scout is running, "
                "then use --force-lock if the lock is stale."
                + (f"\nLock contents:\n{detail}" if detail else "")
            ) from exc
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2)
            stream.write("\n")
        self._owned = True
        return self

    def __exit__(self, *_: Any) -> None:
        if self._owned:
            with contextlib.suppress(FileNotFoundError):
                self.path.unlink()
        self._owned = False


@dataclass(frozen=True)
class StoredRaw:
    sha256: str
    path: Path
    size_bytes: int
    compressed_size_bytes: int


class RawStore:
    """Content-addressed gzip store for successful HTML responses."""
    def __init__(self, root: Path):
        self.root = root

    def save(self, source_kind: str, content: bytes) -> StoredRaw:
        digest = sha256_bytes(content)
        folder = self.root / slugify(source_kind) / digest[:2]
        path = folder / f"{digest}.html.gz"
        folder.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
            with gzip.open(temporary, "wb", compresslevel=6) as stream:
                stream.write(content)
            os.replace(temporary, path)
        return StoredRaw(digest, path.resolve(), len(content), path.stat().st_size)



SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    item_kind TEXT,
    is_file INTEGER NOT NULL DEFAULT 0,
    filing_date TEXT,
    submitter TEXT,
    company TEXT,
    project TEXT,
    filing_number TEXT,
    snippet TEXT,
    metadata JSON NOT NULL DEFAULT '{}',

    status TEXT NOT NULL DEFAULT 'NEW',
    scout_status TEXT NOT NULL DEFAULT 'PENDING',
    download_status TEXT NOT NULL DEFAULT 'PENDING',
    process_status TEXT NOT NULL DEFAULT 'PENDING',
    export_status TEXT NOT NULL DEFAULT 'PENDING',
    detail_status TEXT NOT NULL DEFAULT 'PENDING',
    detail_last_attempt_at TEXT,
    detail_succeeded_at TEXT,
    detail_snapshot_id INTEGER,

    file_path TEXT,
    hash TEXT,
    last_error TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,

    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_documents_scout_status ON documents(scout_status);
CREATE INDEX IF NOT EXISTS idx_documents_download_status ON documents(download_status);
CREATE INDEX IF NOT EXISTS idx_documents_filing_date ON documents(filing_date);

CREATE TABLE IF NOT EXISTS runs (
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
CREATE INDEX IF NOT EXISTS idx_runs_stage_status ON runs(stage, status);

CREATE TABLE IF NOT EXISTS errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    document_id TEXT,
    stage TEXT NOT NULL,
    code TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    retryable INTEGER NOT NULL DEFAULT 0,
    context_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    FOREIGN KEY (run_id) REFERENCES runs(id)
);
CREATE INDEX IF NOT EXISTS idx_errors_run ON errors(run_id);
CREATE INDEX IF NOT EXISTS idx_errors_document ON errors(document_id);
CREATE INDEX IF NOT EXISTS idx_errors_unresolved ON errors(resolved_at, severity);

CREATE TABLE IF NOT EXISTS raw_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    document_id TEXT,
    source_kind TEXT NOT NULL,
    source_url TEXT NOT NULL,
    final_url TEXT,
    fetched_at TEXT NOT NULL,
    http_status INTEGER,
    content_type TEXT,
    content_sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    compressed_size_bytes INTEGER NOT NULL,
    relative_path TEXT NOT NULL,
    response_headers_json TEXT NOT NULL DEFAULT '{}',
    parser_version TEXT NOT NULL,
    UNIQUE(source_kind, source_url, content_sha256),
    FOREIGN KEY (run_id) REFERENCES runs(id)
);
CREATE INDEX IF NOT EXISTS idx_raw_snapshots_document ON raw_snapshots(document_id);
CREATE INDEX IF NOT EXISTS idx_raw_snapshots_hash ON raw_snapshots(content_sha256);

CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL,
    path TEXT NOT NULL,
    original_filename TEXT,
    mime_type TEXT,
    extension TEXT,
    size_bytes INTEGER,
    sha256 TEXT NOT NULL,
    downloaded_at TEXT NOT NULL,
    is_current INTEGER NOT NULL DEFAULT 1,
    UNIQUE(document_id, sha256),
    FOREIGN KEY (document_id) REFERENCES documents(id)
);
CREATE INDEX IF NOT EXISTS idx_files_document_current ON files(document_id, is_current);
"""


class PipelineDB:
    """Five-table SQLite ledger for metadata, status, runs, errors, and files."""

    def __init__(self, path: Path):
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), timeout=60.0)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA busy_timeout=60000")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._install_schema()

    def _install_schema(self) -> None:
        existing = {
            row[0] for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        unexpected = existing - EXPECTED_USER_TABLES
        if unexpected:
            raise RuntimeError(
                "This is not a five-table pipeline-ledger database. "
                "The scout will not modify it. Back it up, then use a new --db path "
                "or delete the database, WAL, and SHM files before retrying. "
                "Unexpected tables: " + ", ".join(sorted(unexpected))
            )
        self.conn.executescript(SCHEMA_SQL)
        self.conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        self.conn.commit()
        actual = {
            row[0] for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if actual != EXPECTED_USER_TABLES:
            raise RuntimeError(
                "Schema verification failed. Expected exactly: "
                + ", ".join(sorted(EXPECTED_USER_TABLES))
                + "; found: " + ", ".join(sorted(actual))
            )

    def close(self) -> None:
        self.conn.close()

    def start_run(self, parameters: Mapping[str, Any]) -> int:
        now = utc_now()
        cursor = self.conn.execute(
            """
            INSERT INTO runs (
                stage, status, started_at, parameters_json, script_version,
                parser_version, current_phase, heartbeat_at
            ) VALUES ('scout', 'RUNNING', ?, ?, ?, ?, 'starting', ?)
            """,
            (now, json_dumps(parameters), SCRIPT_VERSION, PARSER_VERSION, now),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def update_progress(self, run_id: int, state: Mapping[str, Any]) -> None:
        self.conn.execute(
            """
            UPDATE runs SET
                current_phase=?, heartbeat_at=?, completed_units=?, total_units=?,
                progress_message=?, logical_requests=?, http_attempts=?,
                successful_requests=?, failed_requests=?, retries=?
            WHERE id=?
            """,
            (
                state.get("phase"), state.get("heartbeat_at"),
                int(state.get("completed_units") or 0), state.get("total_units"),
                state.get("message"), int(state.get("logical_requests") or 0),
                int(state.get("http_attempts") or 0),
                int(state.get("successful_requests") or 0),
                int(state.get("failed_requests") or 0), int(state.get("retries") or 0),
                run_id,
            ),
        )
        self.conn.commit()

    def finish_run(self, run_id: int, status: str, summary: Mapping[str, Any]) -> None:
        self.conn.execute(
            """
            UPDATE runs SET status=?, finished_at=?, summary_json=?,
                current_phase=?, heartbeat_at=? WHERE id=?
            """,
            (status, utc_now(), json_dumps(summary), status.casefold(), utc_now(), run_id),
        )
        self.conn.commit()

    def add_error(
        self,
        *,
        run_id: int | None,
        stage: str,
        code: str,
        severity: str,
        message: str,
        document_id: str | None = None,
        retryable: bool = False,
        context: Mapping[str, Any] | None = None,
    ) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO errors (
                run_id, document_id, stage, code, severity, message,
                retryable, context_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id, document_id, stage, code, severity.upper(), clean_text(message),
                1 if retryable else 0, json_dumps(context or {}), utc_now(),
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def resolve_errors(
        self,
        *,
        document_id: str,
        stage: str,
        codes: Sequence[str] | None = None,
    ) -> int:
        """Mark prior unresolved errors as resolved after a successful retry.

        Errors remain in the ledger for audit purposes; ``resolved_at`` only
        indicates that a later run successfully completed the same work.
        """
        parameters: list[Any] = [utc_now(), document_id, stage]
        clause = ""
        if codes:
            normalized = [clean_text(code) for code in codes if clean_text(code)]
            if normalized:
                clause = f" AND code IN ({','.join('?' for _ in normalized)})"
                parameters.extend(normalized)
        cursor = self.conn.execute(
            f"""
            UPDATE errors
            SET resolved_at=?
            WHERE resolved_at IS NULL
              AND document_id=?
              AND stage=?
              {clause}
            """,
            parameters,
        )
        self.conn.commit()
        return int(cursor.rowcount)

    def save_snapshot(
        self,
        *,
        run_id: int,
        document_id: str | None,
        source_kind: str,
        source_url: str,
        final_url: str,
        fetched_at: str,
        http_status: int,
        content_type: str,
        stored: StoredRaw,
        response_headers: Mapping[str, str],
    ) -> int:
        try:
            relative = stored.path.relative_to(self.path.parent)
        except ValueError:
            relative = stored.path
        self.conn.execute(
            """
            INSERT INTO raw_snapshots (
                run_id, document_id, source_kind, source_url, final_url,
                fetched_at, http_status, content_type, content_sha256,
                size_bytes, compressed_size_bytes, relative_path,
                response_headers_json, parser_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_kind, source_url, content_sha256) DO UPDATE SET
                run_id=excluded.run_id,
                document_id=COALESCE(excluded.document_id, raw_snapshots.document_id),
                final_url=excluded.final_url,
                fetched_at=excluded.fetched_at,
                http_status=excluded.http_status,
                content_type=excluded.content_type,
                response_headers_json=excluded.response_headers_json,
                parser_version=excluded.parser_version
            """,
            (
                run_id, document_id, source_kind, source_url, final_url, fetched_at,
                http_status, content_type, stored.sha256, stored.size_bytes,
                stored.compressed_size_bytes, str(relative).replace(os.sep, "/"),
                json_dumps(dict(response_headers)), PARSER_VERSION,
            ),
        )
        row = self.conn.execute(
            """
            SELECT id FROM raw_snapshots
            WHERE source_kind=? AND source_url=? AND content_sha256=?
            """,
            (source_kind, source_url, stored.sha256),
        ).fetchone()
        self.conn.commit()
        return int(row[0])

    @staticmethod
    def _merge_scout_metadata(existing: Mapping[str, Any], scout: Mapping[str, Any]) -> dict[str, Any]:
        """Replace only scout-owned keys supplied by this observation.

        Container result rows can contain fewer fields than the date-search row
        for the same item. Selective replacement prevents a container discovery
        from erasing richer metadata collected through the date search.
        """
        merged = dict(existing)
        for key in BASE_METADATA_KEYS:
            if key in scout:
                merged.pop(key, None)
        merged.update(scout)
        return merged

    def upsert_base_document(
        self, record: "SearchRecord", *, run_id: int, base_complete: bool,
        start_date: str, end_date: str,
        discovery_source: str = "date_search",
        container_id: str | None = None,
        container_kind: str | None = None,
        mark_running: bool = True,
    ) -> None:
        now = utc_now()
        existing = self.conn.execute(
            "SELECT metadata, scout_status FROM documents WHERE id=?", (record.document_id,)
        ).fetchone()
        existing_metadata = parse_json_object(existing["metadata"]) if existing else {}
        previous_scout_status = str(existing["scout_status"]) if existing else None
        scout = record.metadata()
        identifiers = parse_json_object(existing_metadata.get("identifiers"))
        for identifier in extract_title_identifiers(record.name):
            key = str(identifier["type"])
            current = identifiers.get(key, [])
            current_values = current if isinstance(current, list) else []
            identifiers[key] = dedupe_strings([*current_values, str(identifier["value"])])
        if identifiers:
            scout["identifiers"] = identifiers
        collection = parse_json_object(existing_metadata.get("collection"))
        collection["scout_run_id"] = run_id
        collection.setdefault("facets", {})
        collection.setdefault("detail", {})
        discovery = parse_json_object(existing_metadata.get("discovery"))
        if discovery_source == "date_search":
            collection["base_search"] = "SUCCEEDED" if base_complete else "PARTIAL"
            discovery["date_search"] = {
                "run_id": run_id,
                "start_date": start_date,
                "end_date": end_date,
                "complete": base_complete,
                "observed_at": now,
            }
            scout.update(
                scraped_at=now,
                search_range={"start_date": start_date, "end_date": end_date},
                base_search_complete=base_complete,
            )
        elif discovery_source == "container":
            containers = discovery.get("containers", [])
            container_ids = dedupe_strings([
                *(containers if isinstance(containers, list) else []),
                container_id,
            ])
            discovery["containers"] = container_ids
            discovery["last_container_observed_at"] = now
            if container_id:
                kinds = parse_json_object(discovery.get("container_kinds"))
                kinds[str(container_id)] = clean_text(container_kind)
                discovery["container_kinds"] = kinds
            collection["container_discovery"] = "SUCCEEDED" if base_complete else "PARTIAL"
            scout["scraped_at"] = now
        else:
            raise ValueError(f"Unsupported discovery_source: {discovery_source}")
        if record.is_synthetic_container_row:
            collection["detail"] = {
                "status": "NOT_APPLICABLE",
                "reason": "synthetic_container_row",
                "run_id": run_id,
                "updated_at": now,
            }
            collection["download"] = {
                "status": "NOT_APPLICABLE",
                "reason": "paper_only_or_non_unique_container_row",
                "run_id": run_id,
                "updated_at": now,
            }
        scout["discovery"] = discovery
        scout["collection"] = collection
        metadata = self._merge_scout_metadata(existing_metadata, scout)
        self.conn.execute(
            """
            INSERT INTO documents (
                id, name, url, item_kind, is_file, filing_date, submitter,
                company, project, filing_number, snippet, metadata,
                scout_status, first_seen_at, last_seen_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'RUNNING', ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                url=excluded.url,
                item_kind=COALESCE(NULLIF(excluded.item_kind,''), documents.item_kind),
                is_file=excluded.is_file,
                filing_date=COALESCE(NULLIF(excluded.filing_date,''), documents.filing_date),
                submitter=COALESCE(NULLIF(excluded.submitter,''), documents.submitter),
                company=COALESCE(NULLIF(excluded.company,''), documents.company),
                project=COALESCE(NULLIF(excluded.project,''), documents.project),
                filing_number=COALESCE(NULLIF(excluded.filing_number,''), documents.filing_number),
                snippet=COALESCE(NULLIF(excluded.snippet,''), documents.snippet),
                metadata=excluded.metadata,
                scout_status='RUNNING',
                last_seen_at=excluded.last_seen_at,
                updated_at=excluded.updated_at
            """,
            (
                record.document_id, record.name, record.url, record.kind,
                1 if record.is_file else 0, normalize_date(record.date_raw),
                record.submitter, record.company, record.project,
                record.filing_number, record.snippet, json_dumps(metadata),
                now, now, now, now,
            ),
        )
        if not mark_running:
            repaired_status = previous_scout_status or ("SUCCEEDED" if base_complete else "PARTIAL")
            self.conn.execute(
                "UPDATE documents SET scout_status=? WHERE id=?",
                (repaired_status, record.document_id),
            )
        if record.is_synthetic_container_row:
            self.conn.execute(
                """
                UPDATE documents SET
                    detail_status='NOT_APPLICABLE',
                    download_status=CASE
                        WHEN download_status IN ('PENDING','NOT_APPLICABLE') THEN 'NOT_APPLICABLE'
                        ELSE download_status
                    END,
                    updated_at=?
                WHERE id=?
                """,
                (now, record.document_id),
            )
        self.conn.commit()

    def apply_container_membership(
        self,
        *,
        container: "SearchRecord",
        members: Sequence["SearchRecord"],
        run_id: int,
        snapshot_ids: Sequence[int],
        result_endpoint: str,
        reported_total: int | None,
        complete: bool,
    ) -> None:
        """Persist explicit REGDOCS container membership in document JSON.

        A complete refresh replaces the previous membership and removes stale
        child links. An incomplete refresh merges newly observed members with
        the last complete manifest so a truncated page cannot silently erase
        known files. No generic relationship or link table is created.
        """
        now = utc_now()
        membership_state = (
            "EMPTY" if complete and reported_total == 0
            else "COMPLETE" if complete
            else "INCOMPLETE"
        )
        observed_ids = dedupe_strings(member.document_id for member in members)
        evidence_snapshot_ids = [int(value) for value in snapshot_ids if value is not None]
        primary_snapshot_id = evidence_snapshot_ids[0] if evidence_snapshot_ids else None
        with self.conn:
            parent_row = self.conn.execute(
                "SELECT metadata FROM documents WHERE id=?", (container.document_id,)
            ).fetchone()
            previous_ids: list[str] = []
            parent_metadata: dict[str, Any] = {}
            if parent_row:
                parent_metadata = parse_json_object(parent_row[0])
                # Migrate v4.1 compound-only keys in place when encountered.
                previous_container = parse_json_object(parent_metadata.get("container"))
                if not previous_container:
                    previous_container = parse_json_object(parent_metadata.get("compound"))
                previous_raw = previous_container.get("member_ids", [])
                previous_ids = dedupe_strings(previous_raw if isinstance(previous_raw, list) else [])

            effective_ids = observed_ids if complete else dedupe_strings([*previous_ids, *observed_ids])
            removed_ids = sorted(set(previous_ids) - set(effective_ids)) if complete else []

            if parent_row:
                parent_metadata.pop("compound", None)
                parent_metadata["container"] = {
                    "is_container": True,
                    "container_kind": container.kind,
                    "membership_source": "explicit_regdocs_container_result_list",
                    "member_ids": effective_ids,
                    "member_ids_observed_this_run": observed_ids,
                    "member_count_parsed": len(observed_ids),
                    "member_count_effective": len(effective_ids),
                    "member_count_reported": reported_total,
                    "membership_complete": complete,
                    "membership_state": membership_state,
                    "snapshot_id": primary_snapshot_id,
                    "snapshot_ids": evidence_snapshot_ids,
                    "result_endpoint": result_endpoint,
                    "expanded_at": now,
                    "run_id": run_id,
                }
                self.conn.execute(
                    "UPDATE documents SET metadata=?, updated_at=? WHERE id=?",
                    (json_dumps(parent_metadata), now, container.document_id),
                )

            for member in members:
                child_row = self.conn.execute(
                    "SELECT metadata FROM documents WHERE id=?", (member.document_id,)
                ).fetchone()
                if not child_row:
                    continue
                child_metadata = parse_json_object(child_row[0])
                raw_memberships = child_metadata.get("container_memberships")
                if not isinstance(raw_memberships, list):
                    raw_memberships = child_metadata.get("compound_memberships", [])
                memberships = [
                    value for value in raw_memberships
                    if isinstance(value, Mapping)
                    and str(value.get("container_id") or value.get("compound_id")) != container.document_id
                ] if isinstance(raw_memberships, list) else []
                memberships.append({
                    "container_id": container.document_id,
                    "container_kind": container.kind,
                    "container_title": container.name,
                    "filing_number": container.filing_number,
                    "membership_source": "explicit_regdocs_container_result_list",
                    "snapshot_id": primary_snapshot_id,
                    "snapshot_ids": evidence_snapshot_ids,
                    "result_endpoint": result_endpoint,
                    "observed_at": now,
                    "run_id": run_id,
                })
                child_metadata.pop("compound_memberships", None)
                child_metadata["container_memberships"] = memberships
                self.conn.execute(
                    "UPDATE documents SET metadata=?, updated_at=? WHERE id=?",
                    (json_dumps(child_metadata), now, member.document_id),
                )

            for removed_id in removed_ids:
                child_row = self.conn.execute(
                    "SELECT metadata FROM documents WHERE id=?", (removed_id,)
                ).fetchone()
                if not child_row:
                    continue
                child_metadata = parse_json_object(child_row[0])
                raw_memberships = child_metadata.get("container_memberships")
                if not isinstance(raw_memberships, list):
                    raw_memberships = child_metadata.get("compound_memberships", [])
                if isinstance(raw_memberships, list):
                    remaining = [
                        value for value in raw_memberships
                        if not (
                            isinstance(value, Mapping)
                            and str(value.get("container_id") or value.get("compound_id")) == container.document_id
                        )
                    ]
                    if remaining:
                        child_metadata.pop("compound_memberships", None)
                        child_metadata["container_memberships"] = remaining
                    else:
                        child_metadata.pop("compound_memberships", None)
                        child_metadata.pop("container_memberships", None)
                    self.conn.execute(
                        "UPDATE documents SET metadata=?, updated_at=? WHERE id=?",
                        (json_dumps(child_metadata), now, removed_id),
                    )

    def apply_facet_category(
        self,
        *,
        document_ids: Sequence[str],
        category: str,
        matches: Mapping[str, Sequence[tuple[str, str]]],
        run_id: int,
        category_complete: bool,
    ) -> None:
        now = utc_now()
        with self.conn:
            for document_id in document_ids:
                row = self.conn.execute(
                    "SELECT metadata FROM documents WHERE id=?", (document_id,)
                ).fetchone()
                if not row:
                    continue
                metadata = parse_json_object(row[0])
                facets = parse_json_object(metadata.get("facets"))
                found = dedupe_strings(label for _filter_id, label in matches.get(document_id, []))
                previous = facets.get(category, [])
                previous_values = previous if isinstance(previous, list) else []
                facets[category] = found if category_complete else dedupe_strings([*previous_values, *found])
                metadata["facets"] = facets
                for facet_category, key in KNOWN_FACET_FIELDS.items():
                    values = facets.get(facet_category, [])
                    metadata[key] = values if isinstance(values, list) else []
                collection = parse_json_object(metadata.get("collection"))
                facet_state = parse_json_object(collection.get("facets"))
                facet_state[category] = {
                    "status": "SUCCEEDED" if category_complete else "PARTIAL",
                    "run_id": run_id,
                    "updated_at": now,
                    "values_found": len(found),
                }
                collection["facets"] = facet_state
                metadata["collection"] = collection
                self.conn.execute(
                    "UPDATE documents SET metadata=?, updated_at=? WHERE id=?",
                    (json_dumps(metadata), now, document_id),
                )

    def detail_is_fresh(self, document_id: str, refresh_days: int) -> bool:
        row = self.conn.execute(
            "SELECT detail_status, detail_succeeded_at FROM documents WHERE id=?",
            (document_id,),
        ).fetchone()
        if not row or row["detail_status"] != "SUCCEEDED" or not row["detail_succeeded_at"]:
            return False
        try:
            succeeded = datetime.fromisoformat(row["detail_succeeded_at"])
            if succeeded.tzinfo is None:
                succeeded = succeeded.replace(tzinfo=timezone.utc)
        except ValueError:
            return False
        return datetime.now(timezone.utc) - succeeded <= timedelta(days=refresh_days)

    def mark_detail_started(self, document_id: str) -> None:
        now = utc_now()
        self.conn.execute(
            """
            UPDATE documents SET detail_status='RUNNING', detail_last_attempt_at=?,
                updated_at=? WHERE id=?
            """,
            (now, now, document_id),
        )
        self.conn.commit()

    def mark_detail_failed(self, document_id: str, status: str, run_id: int) -> None:
        now = utc_now()
        row = self.conn.execute("SELECT metadata FROM documents WHERE id=?", (document_id,)).fetchone()
        if row:
            metadata = parse_json_object(row[0])
            collection = parse_json_object(metadata.get("collection"))
            collection["detail"] = {"status": status, "run_id": run_id, "updated_at": now}
            metadata["collection"] = collection
            self.conn.execute(
                """
                UPDATE documents SET detail_status=?, metadata=?, updated_at=? WHERE id=?
                """,
                (status, json_dumps(metadata), now, document_id),
            )
            self.conn.commit()

    def apply_detail(
        self,
        *,
        document_id: str,
        data: "DetailData",
        normalized: Mapping[str, str],
        snapshot_id: int | None,
        run_id: int,
        fallback_title: str,
    ) -> None:
        now = utc_now()
        row = self.conn.execute("SELECT metadata FROM documents WHERE id=?", (document_id,)).fetchone()
        if not row:
            return
        metadata = parse_json_object(row[0])
        detail_fields = {
            label: dedupe_strings(values)
            for label, values in data.fields.items()
            if values
        }
        useful_meta = {
            key: value for key, value in data.meta.items()
            if not is_boilerplate_field(key, value)
        }
        metadata["regdocs_detail_fields"] = detail_fields
        metadata["detail_page"] = {
            "title": data.title,
            "page_title": data.page_title,
            "language": data.language,
            "canonical_url": data.canonical_url,
            "meta": useful_meta,
            "raw_meta_count": len(data.meta),
            "filtered_meta_count": len(data.meta) - len(useful_meta),
            "field_count": sum(len(values) for values in data.fields.values()),
            "snapshot_id": snapshot_id,
            "fetched_at": now,
        }
        grouped = parse_json_object(metadata.get("identifiers"))
        title = normalized.get("title") or data.title or data.page_title or fallback_title
        for identifier in extract_title_identifiers(title):
            key = str(identifier["type"])
            current = grouped.get(key, [])
            current_values = current if isinstance(current, list) else []
            grouped[key] = dedupe_strings([*current_values, str(identifier["value"])])
        if grouped:
            metadata["identifiers"] = grouped
        collection = parse_json_object(metadata.get("collection"))
        collection["detail"] = {
            "status": "SUCCEEDED", "run_id": run_id,
            "snapshot_id": snapshot_id, "updated_at": now,
        }
        metadata["collection"] = collection
        self.conn.execute(
            """
            UPDATE documents SET
                name=COALESCE(NULLIF(?,''), name),
                filing_date=COALESCE(NULLIF(?,''), filing_date),
                submitter=COALESCE(NULLIF(?,''), submitter),
                company=COALESCE(NULLIF(?,''), company),
                project=COALESCE(NULLIF(?,''), project),
                filing_number=COALESCE(NULLIF(?,''), filing_number),
                metadata=?, detail_status='SUCCEEDED', detail_succeeded_at=?,
                detail_snapshot_id=?, updated_at=?
            WHERE id=?
            """,
            (
                title, normalize_date(normalized.get("date") or normalized.get("filing_date", "")),
                normalized.get("submitter"),
                normalized.get("company") or normalized.get("applicant"),
                normalized.get("project"), normalized.get("filing_number"),
                json_dumps(metadata), now, snapshot_id, now, document_id,
            ),
        )
        self.conn.commit()

    def finalize_documents(
        self,
        document_ids: Sequence[str],
        *,
        run_id: int,
        base_complete: bool,
        containers_enabled: bool,
        facets_enabled: bool,
        facet_categories_complete: Mapping[str, bool],
        details_enabled: bool,
    ) -> None:
        all_facets_complete = all(facet_categories_complete.values()) if facet_categories_complete else True
        now = utc_now()
        with self.conn:
            for document_id in document_ids:
                row = self.conn.execute(
                    "SELECT metadata, detail_status, item_kind FROM documents WHERE id=?", (document_id,)
                ).fetchone()
                if not row:
                    continue
                metadata = parse_json_object(row["metadata"])
                detail_ok = not details_enabled or row["detail_status"] in {"SUCCEEDED", "NOT_APPLICABLE"}
                facets_ok = not facets_enabled or all_facets_complete
                container_state = parse_json_object(metadata.get("container"))
                if not container_state:
                    container_state = parse_json_object(metadata.get("compound"))
                container_ok = (
                    not containers_enabled
                    or not is_container_kind(row["item_kind"])
                    or container_state.get("membership_complete") is True
                )
                status = (
                    "SUCCEEDED"
                    if base_complete and container_ok and facets_ok and detail_ok
                    else "PARTIAL"
                )
                collection = parse_json_object(metadata.get("collection"))
                collection.update(
                    scout_run_id=run_id,
                    overall_status=status,
                    base_search="SUCCEEDED" if base_complete else "PARTIAL",
                    container_overall=(
                        "SUCCEEDED" if container_ok else "PARTIAL"
                    ) if containers_enabled and is_container_kind(row["item_kind"]) else "NOT_APPLICABLE",
                    facets_overall=("SUCCEEDED" if facets_ok else "PARTIAL") if facets_enabled else "SKIPPED",
                    detail_overall=("SUCCEEDED" if detail_ok else "PARTIAL") if details_enabled else "SKIPPED",
                    finalized_at=now,
                    script_version=SCRIPT_VERSION,
                    parser_version=PARSER_VERSION,
                )
                metadata["collection"] = collection
                self.conn.execute(
                    "UPDATE documents SET scout_status=?, metadata=?, updated_at=? WHERE id=?",
                    (status, json_dumps(metadata), now, document_id),
                )

    def load_container_records(self) -> list["SearchRecord"]:
        """Load every known Folder and Compound Document as crawl seeds."""
        records: list[SearchRecord] = []
        rows = self.conn.execute(
            """
            SELECT id, name, url, item_kind, is_file, filing_date, submitter,
                   company, project, filing_number, snippet, metadata
            FROM documents
            WHERE lower(trim(item_kind)) IN ('compound document', 'folder')
            ORDER BY id
            """
        ).fetchall()
        for row in rows:
            metadata = parse_json_object(row["metadata"])
            records.append(SearchRecord(
                document_id=str(row["id"]),
                name=clean_text(row["name"]),
                url=clean_text(row["url"]) or DETAIL_URL_TEMPLATE.format(item_id=row["id"]),
                is_file=bool(row["is_file"]),
                date_raw=clean_text(row["filing_date"] or metadata.get("date")),
                submitter=clean_text(row["submitter"] or metadata.get("submitter")),
                kind=clean_text(row["item_kind"]) or None,
                filing_number=clean_text(row["filing_number"] or metadata.get("filing_number")) or None,
                filing_id=clean_text(metadata.get("filing_id")) or None,
                company=clean_text(row["company"] or metadata.get("company")) or None,
                company_id=clean_text(metadata.get("company_id")) or None,
                project=clean_text(row["project"] or metadata.get("project")) or None,
                project_id=clean_text(metadata.get("project_id")) or None,
                snippet=clean_text(row["snippet"] or metadata.get("snippet")) or None,
                row_text=clean_text(metadata.get("search_row_text")),
            ))
        return records

    def finalize_container_repairs(self, document_ids: Sequence[str], *, run_id: int) -> None:
        """Finalize only container-repair metadata without rewriting other stages."""
        now = utc_now()
        with self.conn:
            for document_id in dedupe_strings(document_ids):
                row = self.conn.execute(
                    "SELECT metadata, scout_status FROM documents WHERE id=?",
                    (document_id,),
                ).fetchone()
                if not row:
                    continue
                metadata = parse_json_object(row["metadata"])
                container_state = parse_json_object(metadata.get("container"))
                complete = container_state.get("membership_complete") is True
                collection = parse_json_object(metadata.get("collection"))
                collection["container_overall"] = "SUCCEEDED" if complete else "PARTIAL"
                collection["container_repair"] = {
                    "status": "SUCCEEDED" if complete else "PARTIAL",
                    "run_id": run_id,
                    "updated_at": now,
                    "script_version": SCRIPT_VERSION,
                    "parser_version": PARSER_VERSION,
                }
                metadata["collection"] = collection
                scout_status = str(row["scout_status"] or "PENDING")
                if scout_status == "RUNNING":
                    scout_status = "SUCCEEDED" if complete else "PARTIAL"
                self.conn.execute(
                    "UPDATE documents SET scout_status=?, metadata=?, updated_at=? WHERE id=?",
                    (scout_status, json_dumps(metadata), now, document_id),
                )

    def latest_filing_date(self) -> str | None:
        return self.conn.execute(
            "SELECT MAX(filing_date) FROM documents WHERE filing_date IS NOT NULL"
        ).fetchone()[0]

    def summary_counts(self, run_id: int) -> dict[str, int]:
        return {
            "documents_total": int(self.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]),
            "raw_snapshots_total": int(self.conn.execute("SELECT COUNT(*) FROM raw_snapshots").fetchone()[0]),
            "files_total": int(self.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]),
            "errors_this_run": int(self.conn.execute("SELECT COUNT(*) FROM errors WHERE run_id=?", (run_id,)).fetchone()[0]),
            "error_severity_this_run": int(self.conn.execute("SELECT COUNT(*) FROM errors WHERE run_id=? AND severity='ERROR'", (run_id,)).fetchone()[0]),
            "warnings_this_run": int(self.conn.execute("SELECT COUNT(*) FROM errors WHERE run_id=? AND severity='WARNING'", (run_id,)).fetchone()[0]),
        }



@dataclass
class FetchResult:
    ok: bool
    url: str
    final_url: str | None = None
    status_code: int | None = None
    text: str | None = None
    content: bytes | None = None
    headers: dict[str, str] = field(default_factory=dict)
    snapshot_id: int | None = None
    response_sha256: str | None = None
    error_type: str | None = None
    error_detail: str | None = None
    attempts: int = 0


class RequestPacer:
    """Shared polite pacing across all request workers."""
    def __init__(self, min_delay: float, max_delay: float):
        self.min_delay = max(0.0, min_delay)
        self.max_delay = max(self.min_delay, max_delay)
        self._lock = asyncio.Lock()
        self._next_allowed = 0.0

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            if now < self._next_allowed:
                await asyncio.sleep(self._next_allowed - now)
            self._next_allowed = time.monotonic() + random.uniform(self.min_delay, self.max_delay)

    async def defer(self, seconds: float) -> None:
        if seconds <= 0:
            return
        async with self._lock:
            self._next_allowed = max(self._next_allowed, time.monotonic() + seconds)


class PoliteHttpClient:
    """Bounded, retried, globally paced REGDOCS HTTP client."""
    def __init__(
        self, *, client: httpx.AsyncClient, db: PipelineDB, raw_store: RawStore,
        run_id: int, progress: ProgressMonitor, concurrency: int,
        min_delay: float, max_delay: float, max_retries: int, retry_backoff: float,
    ):
        self.client = client
        self.db = db
        self.raw_store = raw_store
        self.run_id = run_id
        self.progress = progress
        self.semaphore = asyncio.Semaphore(max(1, concurrency))
        self.pacer = RequestPacer(min_delay, max_delay)
        self.max_retries = max(0, max_retries)
        self.retry_backoff = max(1.0, retry_backoff)
        self.logical_requests = 0
        self.http_attempts = 0

    @staticmethod
    def retry_after_seconds(headers: Mapping[str, str]) -> float | None:
        raw = clean_text(headers.get("retry-after"))
        if not raw:
            return None
        if raw.isdigit():
            return float(raw)
        with contextlib.suppress(Exception):
            from email.utils import parsedate_to_datetime
            parsed = parsedate_to_datetime(raw)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds())
        return None

    async def get(
        self, url: str, *, source_kind: str, request_key: str,
        params: Mapping[str, Any] | None = None, document_id: str | None = None,
        ajax: bool = False, referer: str | None = None,
    ) -> FetchResult:
        self.logical_requests += 1
        self.progress.logical_request_started()
        headers = dict(AJAX_HEADERS if ajax else HEADERS)
        if referer:
            headers["Referer"] = referer
        last_error_type = "unknown"
        last_error_detail = "Unknown request failure"
        status_code = None
        attempts_used = 0
        async with self.semaphore:
            for attempt in range(1, self.max_retries + 2):
                attempts_used = attempt
                await self.pacer.wait()
                response = None
                status_code = None
                retryable = False
                self.http_attempts += 1
                self.progress.attempt_started(attempt)
                try:
                    response = await self.client.get(
                        url, params=dict(params or {}), headers=headers,
                        timeout=httpx.Timeout(60.0, connect=30.0),
                    )
                    status_code = response.status_code
                    retryable = status_code in RETRYABLE_STATUS_CODES
                    if status_code != 200:
                        raise httpx.HTTPStatusError(
                            f"HTTP {status_code}", request=response.request, response=response
                        )
                    content = response.content
                    stored = self.raw_store.save(source_kind, content)
                    response_headers = {k: v for k, v in response.headers.items()}
                    snapshot_id = self.db.save_snapshot(
                        run_id=self.run_id, document_id=document_id,
                        source_kind=source_kind, source_url=str(response.request.url),
                        final_url=str(response.url), fetched_at=utc_now(),
                        http_status=status_code,
                        content_type=response.headers.get("content-type", ""),
                        stored=stored, response_headers=response_headers,
                    )
                    self.progress.attempt_finished(ok=True)
                    return FetchResult(
                        True, str(response.request.url), str(response.url), status_code,
                        response.text, content, response_headers, snapshot_id,
                        stored.sha256, attempts=attempt,
                    )
                except (httpx.RequestError, httpx.HTTPStatusError) as exc:
                    last_error_type = type(exc).__name__
                    last_error_detail = clean_text(exc)
                    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
                        status_code = exc.response.status_code
                        retryable = status_code in RETRYABLE_STATUS_CODES
                    elif isinstance(exc, httpx.RequestError):
                        retryable = True
                    response_headers = dict(response.headers) if response is not None else {}
                    self.progress.attempt_finished(ok=False)
                    retry_after = self.retry_after_seconds(response_headers)
                    if retry_after:
                        await self.pacer.defer(retry_after)
                    if not retryable or attempt > self.max_retries:
                        break
                    sleep_for = max(
                        retry_after or 0.0,
                        self.retry_backoff ** attempt + random.uniform(0, 1),
                    )
                    logging.warning(
                        "Retrying %s after %s (attempt %d/%d, %.1fs)",
                        request_key, last_error_detail, attempt,
                        self.max_retries + 1, sleep_for,
                    )
                    await asyncio.sleep(sleep_for)
        return FetchResult(
            False, str(httpx.URL(url, params=params)), status_code=status_code,
            error_type=last_error_type, error_detail=last_error_detail,
            attempts=attempts_used,
        )


@dataclass
class SearchRecord:
    """One normalized result row plus preserved row-level details."""

    document_id: str
    name: str
    url: str
    is_file: bool
    date_raw: str
    submitter: str
    kind: str | None = None
    filing_number: str | None = None
    filing_id: str | None = None
    company: str | None = None
    company_id: str | None = None
    project: str | None = None
    project_id: str | None = None
    snippet: str | None = None
    row_text: str = ""
    regdocs_item_id: str | None = None
    identity_kind: str | None = None
    source_container_id: str | None = None
    exhibit_number: str | None = None

    @property
    def is_synthetic_container_row(self) -> bool:
        return self.identity_kind == "container_row_synthetic"

    def metadata(self) -> dict[str, Any]:
        data = {
            "id": int(self.document_id) if self.document_id.isdigit() else self.document_id,
            "name": self.name,
            "url": self.url,
            "is_file": self.is_file,
            "date": self.date_raw,
            "submitter": self.submitter,
            "kind": self.kind,
            "filing_number": self.filing_number,
            "filing_id": int(self.filing_id) if self.filing_id and self.filing_id.isdigit() else self.filing_id,
            "company": self.company,
            "company_id": int(self.company_id) if self.company_id and self.company_id.isdigit() else self.company_id,
            "project": self.project,
            "project_id": int(self.project_id) if self.project_id and self.project_id.isdigit() else self.project_id,
            "snippet": self.snippet,
            "search_row_text": self.row_text,
            "regdocs_item_id": int(self.regdocs_item_id) if self.regdocs_item_id and self.regdocs_item_id.isdigit() else self.regdocs_item_id,
            "identity_kind": self.identity_kind,
            "source_container_id": self.source_container_id,
            "exhibit_number": self.exhibit_number,
        }
        return {key: value for key, value in data.items() if value not in (None, "")}

@dataclass
class DetailData:
    """Generic and normalized metadata extracted from an item detail page."""

    item_id: str
    title: str | None
    page_title: str | None
    language: str | None
    canonical_url: str | None
    fields: dict[str, list[str]]
    meta: dict[str, str]
    visible_text_length: int

@dataclass
class CrawlResult:
    """Result of crawling every page for one search query."""

    records: dict[str, SearchRecord]
    reported_total: int | None
    pages_succeeded: int
    pages_failed: int
    complete: bool
    snapshots: list[int]

def parse_total(html: str) -> int | None:
    """Parse REGDOCS' approximate result count."""
    match = TOTAL_RE.search(html)
    return int(match.group(1).replace(",", "")) if match else None


def expected_search_pages(
    reported_total: int | None, *, page_size: int, limit: int | None = None,
) -> int | None:
    """Return the expected number of fetched pages for progress reporting.

    REGDOCS labels its total as approximate, so this is an estimate rather than
    a completeness rule. The first page is always fetched, including an empty
    result set. Tail probes can increase the displayed total later.
    """
    if reported_total is None:
        return None
    target = min(reported_total, limit) if limit is not None else reported_total
    return max(1, (max(target, 0) + page_size - 1) // page_size)

def search_response_recognized(html: str) -> bool:
    """Reject generic HTTP-200 maintenance/error pages as search results."""
    if not clean_text(html):
        return False
    soup = BeautifulSoup(html, SOUP_PARSER)
    if soup.find("tbody") is not None:
        return True
    if parse_total(html) is not None:
        return True
    return bool(NO_RESULTS_RE.search(clean_text(soup.get_text(" ", strip=True))))

def parse_container_result_endpoint(html: str, page_url: str, item_id: str) -> str | None:
    """Return the AJAX result-list endpoint declared by a container page.

    REGDOCS container ``Item/View`` responses are page shells. The
    visible rows shown by a browser are loaded into ``#section-items`` from the
    URL in its ``data-ajax-replace`` attribute. We use only that explicit page
    declaration and do not infer or crawl unrelated links.
    """
    soup = BeautifulSoup(html, SOUP_PARSER)
    section = soup.find("section", id="section-items")
    candidates: list[str] = []
    if isinstance(section, Tag):
        for attribute in ("data-ajax-replace", "data-ajax-append", "data-url"):
            value = clean_text(section.get(attribute))
            if value:
                candidates.append(value)
    if not candidates:
        for element in soup.find_all(attrs={"data-ajax-replace": True}):
            value = clean_text(element.get("data-ajax-replace"))
            if "/REGDOCS/Item/LoadResult/" in value:
                candidates.append(value)
    expected_suffix = f"/REGDOCS/Item/LoadResult/{item_id}"
    for candidate in candidates:
        absolute = urljoin(page_url, candidate)
        if expected_suffix.casefold() in absolute.casefold():
            return absolute
    return None


def parse_next_container_result_url(html: str, current_url: str) -> str | None:
    """Return an explicit next-page URL from a loaded container result fragment.

    No pagination parameter is guessed. If REGDOCS emits a next link, its
    declared URL is followed. If no such link exists while the reported total
    exceeds parsed rows, the membership is marked incomplete rather than
    fabricating a request.
    """
    soup = BeautifulSoup(html, SOUP_PARSER)
    for anchor in soup.find_all("a"):
        rel = " ".join(anchor.get("rel", [])).casefold() if anchor.get("rel") else ""
        text = clean_text(anchor.get_text(" ", strip=True)).casefold()
        title = clean_text(anchor.get("title") or anchor.get("aria-label")).casefold()
        classes = " ".join(anchor.get("class", [])).casefold()
        is_next = (
            "next" in rel
            or "next" in title
            or "next" in classes
            or text in {"next", "next page", ">", "»", "›"}
        )
        if not is_next:
            continue
        for attribute in ("href", "data-ajax-url", "data-url"):
            target = clean_text(anchor.get(attribute))
            if target and target != "#":
                return urljoin(current_url, target)
        onclick = clean_text(anchor.get("onclick"))
        match = re.search(r"['\"]([^'\"]*?/REGDOCS/Item/LoadResult/[^'\"]+)['\"]", onclick)
        if match:
            return urljoin(current_url, match.group(1))
    return None


def nearby_label(anchor: Tag) -> str:
    """Find a short label immediately preceding an anchor in its local block."""
    parent = anchor.parent
    if not isinstance(parent, Tag):
        return ""
    previous = parent.find_previous_sibling()
    if isinstance(previous, Tag):
        text = clean_text(previous.get_text(" ", strip=True)).rstrip(":")
        if 0 < len(text) <= 80:
            return text
    # Bootstrap-style label/value rows.
    row = parent.find_parent(class_=re.compile(r"\brow\b", re.IGNORECASE))
    if isinstance(row, Tag):
        candidates = row.find_all(
            class_=re.compile(r"label|field|name|header", re.IGNORECASE), limit=3
        )
        for candidate in candidates:
            text = clean_text(candidate.get_text(" ", strip=True)).rstrip(":")
            if text and anchor not in candidate.descendants and len(text) <= 80:
                return text
    return ""

def parse_search_rows(html: str, *, container_id: str | None = None) -> list[SearchRecord]:
    """Parse REGDOCS result rows using stable document or container-row IDs.

    Normal rows retain their numeric REGDOCS item ID. Within an explicit
    container, paper-only rows and any repeated numeric placeholder IDs receive
    stable synthetic IDs so distinct rows cannot collapse onto one document.
    """
    soup = BeautifulSoup(html, SOUP_PARSER)
    table_bodies = soup.find_all("tbody")
    if not table_bodies:
        return []
    results: list[SearchRecord] = []
    for row in (row for tbody in table_bodies for row in tbody.find_all("tr")):
        cells = row.find_all("td")
        if len(cells) < 3:
            continue
        summary = cells[0].find("summary")
        primary_cell = summary if isinstance(summary, Tag) else cells[0]
        link_el: Tag | None = None
        match: re.Match[str] | None = None
        for anchor in primary_cell.find_all("a", href=True):
            candidate = ITEM_HREF_RE.search(anchor.get("href", ""))
            if candidate:
                link_el = anchor
                match = candidate
                break
        if link_el is None or match is None:
            continue
        document_id = match.group(2)
        row_text = clean_text(row.get_text(" ", strip=True))
        record = SearchRecord(
            document_id=document_id,
            name=clean_text(link_el.get_text(" ", strip=True)),
            url=absolute_regdocs_url(link_el["href"]),
            is_file=match.group(1).casefold() == "file/download",
            date_raw=clean_text(cells[1].get_text(" ", strip=True)),
            submitter=clean_text(cells[2].get_text(" ", strip=True)),
            row_text=row_text,
        )
        for icon in primary_cell.find_all(["i", "span", "img"]):
            icon_text = clean_text(icon.get("title") or icon.get("alt"))
            if icon_text and (
                "document" in icon_text.casefold()
                or icon_text.casefold() in {"folder", "compound document"}
            ):
                record.kind = icon_text
                break
        if not record.kind and re.search(r"(?:^|[-–— ])HTML(?:[-–— ]|$)", record.name, re.I):
            record.kind = "Html Document"

        details = cells[0].find("details")
        if isinstance(details, Tag):
            for anchor in details.find_all("a", href=True):
                if anchor is link_el:
                    continue
                reference = item_reference_from_url(anchor["href"])
                if not reference:
                    continue
                _link_kind, target_id = reference
                text = clean_text(anchor.get_text(" ", strip=True))
                filing_match = FILING_RE.search(text)
                label = nearby_label(anchor)
                if filing_match:
                    record.filing_number = filing_match.group(1)
                    record.filing_id = target_id
                    label = "Filing"
                elif normalize_label(label) == "company":
                    record.company = text
                    record.company_id = target_id
                elif normalize_label(label) == "project":
                    record.project = text
                    record.project_id = target_id
            divider = details.find("hr")
            if isinstance(divider, Tag):
                snippet = divider.find_next_sibling("div")
                if isinstance(snippet, Tag):
                    record.snippet = clean_text(snippet.get_text(" ", strip=True)) or None
        results.append(record)

    if not container_id or not results:
        return results

    id_counts: dict[str, int] = defaultdict(int)
    for record in results:
        id_counts[record.document_id] += 1
    used_ids = {record.document_id for record in results if id_counts[record.document_id] == 1}
    for record in results:
        paper_only = is_paper_only_text(f"{record.name} {record.row_text} {record.kind or ''}")
        repeated_placeholder = id_counts[record.document_id] > 1
        if not paper_only and not repeated_placeholder:
            continue
        original_id = record.document_id
        synthetic_id, exhibit = synthetic_container_row_id(
            container_id=container_id,
            regdocs_item_id=original_id,
            name=record.name,
            row_text=record.row_text,
            paper_only=paper_only,
            used_ids=used_ids,
        )
        record.document_id = synthetic_id
        record.regdocs_item_id = original_id
        record.identity_kind = "container_row_synthetic"
        record.source_container_id = container_id
        record.exhibit_number = exhibit
        # A synthetic identity cannot map safely to one unique download/detail URL.
        record.is_file = False
        if paper_only:
            record.kind = "Paper Only"
    return results

def parse_facet_catalog(html: str) -> dict[str, dict[str, str]]:
    """Parse every live facet category and option from Advanced Search."""
    soup = BeautifulSoup(html, SOUP_PARSER)
    catalog: dict[str, dict[str, str]] = {}
    for label_el in soup.find_all("label", attrs={"for": re.compile(r"^selectFilter\d+$")}):
        category = clean_text(label_el.get_text(" ", strip=True))
        select = soup.find("select", id=label_el.get("for"))
        if not category or not isinstance(select, Tag):
            continue
        options: dict[str, str] = {}
        for option in select.find_all("option"):
            filter_id = clean_text(option.get("value"))
            label = clean_text(option.get_text(" ", strip=True))
            if filter_id and label:
                options[filter_id] = label
        if options:
            catalog[category] = options
    return catalog

def add_field(fields: dict[str, list[str]], label: str, value: str) -> None:
    """Add a plausible label/value pair with conservative noise filtering."""
    label_text = clean_text(label).rstrip(":")
    value_text = clean_text(value)
    if not label_text or not value_text:
        return
    if is_boilerplate_field(label_text, value_text):
        return
    if label_text.casefold() == value_text.casefold():
        return
    if len(label_text) > 120 or len(value_text) > 5000:
        return
    if value_text not in fields[label_text]:
        fields[label_text].append(value_text)

def parse_detail_page(html: str, item_id: str, page_url: str) -> DetailData:
    """Extract broad detail metadata without assuming a fixed CER page layout."""
    soup = BeautifulSoup(html, SOUP_PARSER)
    fields: dict[str, list[str]] = defaultdict(list)

    page_title = clean_text(soup.title.get_text(" ", strip=True)) if soup.title else None
    title = None

    # REGDOCS item pages provide the authoritative item heading as #page-title.
    # Generic modal headings such as "Feedback" and "Add To Favourites"
    # can appear earlier in the HTML and must not become document metadata.
    preferred_heading = soup.find(id="page-title")
    if isinstance(preferred_heading, Tag):
        candidate = clean_text(preferred_heading.get_text(" ", strip=True))
        if candidate and not is_boilerplate_field("Page Heading", candidate):
            title = candidate

    if not title:
        main = soup.find("main")
        headings = main.find_all(["h1", "h2"]) if isinstance(main, Tag) else soup.find_all(["h1", "h2"])
        for heading in headings:
            if heading.find_parent(class_=re.compile(r"modal|overlay|popup", re.I)):
                continue
            candidate = clean_text(heading.get_text(" ", strip=True))
            if candidate and not is_boilerplate_field("Page Heading", candidate):
                title = candidate
                break
    html_tag = soup.find("html")
    language = clean_text(html_tag.get("lang")) if isinstance(html_tag, Tag) else None
    canonical = soup.find("link", rel=lambda value: value and "canonical" in value)
    canonical_url = (
        absolute_regdocs_url(canonical.get("href"))
        if isinstance(canonical, Tag) and canonical.get("href")
        else page_url
    )

    meta: dict[str, str] = {}
    for element in soup.find_all("meta"):
        key = clean_text(element.get("name") or element.get("property") or element.get("http-equiv"))
        value = clean_text(element.get("content"))
        if key and value:
            meta[key] = value
            add_field(fields, key, value)

    if not title:
        title = cleaned_page_title(page_title)

    # Definition lists are the clearest label/value structure.
    for term in soup.find_all("dt"):
        description = term.find_next_sibling("dd")
        if isinstance(description, Tag):
            add_field(
                fields,
                term.get_text(" ", strip=True),
                description.get_text(" ", strip=True),
            )

    # Tables: th/td and restrained two-column label/value layouts.
    for table_row in soup.find_all("tr"):
        headers = table_row.find_all("th")
        cells = table_row.find_all("td")
        if headers and cells:
            label = clean_text(" ".join(header.get_text(" ", strip=True) for header in headers))
            value = clean_text(" ".join(cell.get_text(" ", strip=True) for cell in cells))
            add_field(fields, label, value)
        elif len(cells) == 2:
            label = clean_text(cells[0].get_text(" ", strip=True)).rstrip(":")
            value = clean_text(cells[1].get_text(" ", strip=True))
            if label.endswith(":") or canonical_field_for_label(label) or len(label) <= 60:
                add_field(fields, label, value)

    # Common Bootstrap/Government-of-Canada field layouts.
    label_nodes = soup.find_all(
        ["label", "div", "span", "strong"],
        class_=re.compile(r"(^|[-_ ])(label|field-label|control-label|metadata-label)([-_ ]|$)", re.I),
    )
    for label_node in label_nodes:
        label = clean_text(label_node.get_text(" ", strip=True)).rstrip(":")
        if not label:
            continue
        value_node = label_node.find_next_sibling()
        if isinstance(value_node, Tag):
            add_field(fields, label, value_node.get_text(" ", strip=True))

    # JSON-LD and other embedded JSON may expose canonical metadata.
    for script in soup.find_all("script", attrs={"type": re.compile(r"application/(ld\+)?json", re.I)}):
        raw = script.string or script.get_text()
        if not clean_text(raw):
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        objects = payload if isinstance(payload, list) else [payload]
        for obj in objects:
            if not isinstance(obj, Mapping):
                continue
            for key, value in obj.items():
                if isinstance(value, (str, int, float, bool)):
                    add_field(fields, f"json:{key}", str(value))

    # Outbound links and navigation breadcrumbs are intentionally ignored.
    visible_text = clean_text(soup.get_text(" ", strip=True))
    return DetailData(
        item_id=item_id,
        title=title,
        page_title=page_title,
        language=language,
        canonical_url=canonical_url,
        fields={label: dedupe_strings(values) for label, values in fields.items()},
        meta=meta,
        visible_text_length=len(visible_text),
    )


@dataclass
class ScoutConfig:
    db_path: Path
    raw_dir: Path
    progress_file: Path
    start_date: str
    end_date: str
    date_mode: str
    page_size: int = 200
    limit: int | None = None
    facets: str = "all"
    expand_containers: bool = True
    repair_containers: bool = False
    container_max_depth: int = 20
    container_max_items: int = 10000
    include_details: bool = True
    detail_refresh_days: int = 30
    refresh_details: bool = False
    concurrency: int = 1
    min_delay: float = 2.0
    max_delay: float = 4.0
    max_retries: int = 4
    retry_backoff: float = 2.0
    dry_run: bool = False
    verbose: bool = False


class DocumentScout:
    """Collect date-search items, explicit container members, facets, and own detail pages."""
    def __init__(self, config: ScoutConfig, db: PipelineDB, run_id: int, progress: ProgressMonitor):
        self.config = config
        self.db = db
        self.run_id = run_id
        self.progress = progress
        self.raw_store = RawStore(config.raw_dir)
        self.base_records: dict[str, SearchRecord] = {}
        self.initial_base_record_count = 0
        self.base_complete = True
        self.container_containers = 0
        self.container_attempted = 0
        self.container_succeeded = 0
        self.container_failed = 0
        self.container_incomplete = 0
        self.container_members_parsed = 0
        self.container_members_added = 0
        self.container_members_already_known = 0
        self.container_empty = 0
        # Total explicit parent->child container relationships observed, including
        # child containers already known from the base date search.
        self.container_nested_relationships: set[tuple[str, str]] = set()
        # Newly encountered child containers added to the traversal queue. The
        # legacy summary key ``container_nested_discovered`` remains as an alias.
        self.container_nested_newly_queued = 0
        self.container_max_depth_reached = 0
        self.container_limit_reached = False
        self.expanded_container_ids: set[str] = set()
        self.detail_completed_this_run: set[str] = set()
        self.facet_catalog_ok = config.facets == "none"
        self.facet_categories_complete: dict[str, bool] = {}
        self.detail_attempted = 0
        self.detail_succeeded = 0
        self.detail_succeeded_from_container_shells = 0
        self.detail_succeeded_from_detail_requests = 0
        self.detail_failed = 0
        self.detail_skipped_fresh = 0
        self.detail_skipped_not_applicable = 0
        self.search_empty_shortcuts = 0
        self.search_short_page_shortcuts = 0
        self.search_tail_probes = 0
        self.base_search_pages_succeeded = 0
        self.base_search_pages_failed = 0
        self.base_search_reported_total: int | None = None
        self.http: PoliteHttpClient | None = None

    async def run(self) -> dict[str, Any]:
        cookies = {"RDI-NumberOfRecords": str(self.config.page_size)}
        async with httpx.AsyncClient(
            cookies=cookies, follow_redirects=True,
            limits=httpx.Limits(
                max_connections=max(2, self.config.concurrency * 2),
                max_keepalive_connections=max(1, self.config.concurrency),
            ),
        ) as client:
            self.http = PoliteHttpClient(
                client=client, db=self.db, raw_store=self.raw_store,
                run_id=self.run_id, progress=self.progress,
                concurrency=self.config.concurrency,
                min_delay=self.config.min_delay, max_delay=self.config.max_delay,
                max_retries=self.config.max_retries,
                retry_backoff=self.config.retry_backoff,
            )
            if self.config.repair_containers:
                seeds = self.db.load_container_records()
                self.base_records = {record.document_id: record for record in seeds}
                self.initial_base_record_count = 0
                self.base_complete = True
                logging.info("Container repair mode: loaded %d known container seed(s)", len(seeds))
                if self.config.expand_containers and self.base_records:
                    await self.expand_containers()
            else:
                await self.discover_base_records()
                if self.config.expand_containers and self.base_records:
                    await self.expand_containers()
                if self.config.facets != "none" and self.base_records:
                    await self.enrich_facets()
                if self.config.include_details and self.base_records:
                    await self.enrich_details()

        if not self.config.dry_run:
            if self.config.repair_containers:
                self.progress.set_phase(
                    "finalizing_container_repairs",
                    total=len(self.expanded_container_ids),
                    message="Finalizing repaired container metadata",
                )
                self.db.finalize_container_repairs(
                    sorted(self.expanded_container_ids), run_id=self.run_id
                )
                for document_id in sorted(self.expanded_container_ids):
                    self.progress.advance(1, message=f"Finalized container repair {document_id}")
            else:
                self.progress.set_phase(
                    "finalizing", total=len(self.base_records),
                    message="Finalizing document scout status",
                )
                self.db.finalize_documents(
                    list(self.base_records), run_id=self.run_id,
                    base_complete=self.base_complete,
                    containers_enabled=self.config.expand_containers,
                    facets_enabled=self.config.facets != "none",
                    facet_categories_complete=self.facet_categories_complete,
                    details_enabled=self.config.include_details,
                )
                for document_id in self.base_records:
                    self.progress.advance(1, message=f"Finalized {document_id}")
        assert self.http is not None
        return {
            "mode": "repair_containers" if self.config.repair_containers else "scout",
            "base_search_records": self.initial_base_record_count,
            "documents_scouted": len(self.base_records),
            "base_records": len(self.base_records),
            "base_complete": self.base_complete,
            "container_items": self.container_containers,
            "container_attempted": self.container_attempted,
            "container_succeeded": self.container_succeeded,
            "container_failed": self.container_failed,
            "container_incomplete": self.container_incomplete,
            "container_empty": self.container_empty,
            "container_nested_relationships_total": len(self.container_nested_relationships),
            "container_nested_newly_queued": self.container_nested_newly_queued,
            # Backward-compatible alias. This means newly queued, not every
            # parent->child container relationship observed.
            "container_nested_discovered": self.container_nested_newly_queued,
            "container_max_depth_reached": self.container_max_depth_reached,
            "container_limit_reached": self.container_limit_reached,
            "container_members_parsed": self.container_members_parsed,
            "container_members_added": self.container_members_added,
            "container_members_already_known": self.container_members_already_known,
            "facet_catalog_ok": self.facet_catalog_ok,
            "facet_categories": len(self.facet_categories_complete),
            "facet_categories_complete": sum(self.facet_categories_complete.values()),
            # ``detail_attempted`` counts direct detail requests only. Container
            # shell pages are fetched during expansion and reused as detail pages.
            "detail_attempted": self.detail_attempted,
            "detail_succeeded": self.detail_succeeded,
            "detail_succeeded_from_container_shells": self.detail_succeeded_from_container_shells,
            "detail_succeeded_from_detail_requests": self.detail_succeeded_from_detail_requests,
            "detail_failed": self.detail_failed,
            "detail_skipped_fresh": self.detail_skipped_fresh,
            "detail_skipped_not_applicable": self.detail_skipped_not_applicable,
            "logical_requests": self.http.logical_requests,
            "http_attempts": self.http.http_attempts,
            "search_empty_shortcuts": self.search_empty_shortcuts,
            "search_short_page_shortcuts": self.search_short_page_shortcuts,
            "search_tail_probes": self.search_tail_probes,
            "base_search_pages_succeeded": self.base_search_pages_succeeded,
            "base_search_pages_failed": self.base_search_pages_failed,
            "base_search_reported_total": self.base_search_reported_total,
        }

    async def crawl_search(
        self, *, params: Mapping[str, Any], source_kind: str,
        request_prefix: str, limit: int | None = None,
        progress_label: str | None = None,
    ) -> CrawlResult:
        assert self.http is not None
        base_params = {**params, "srt": SORT_OLDEST_FIRST}
        records: dict[str, SearchRecord] = {}
        snapshots: list[int] = []
        pages_succeeded = 0
        pages_failed = 0
        progress_pages_completed = 0
        progress_total_pages: int | None = None
        reported_total: int | None = None

        def report_page(*, offset: int, ok: bool) -> None:
            """Advance page-level progress after a page has been parsed."""
            nonlocal progress_pages_completed, progress_total_pages
            if progress_label is None:
                return
            prospective = progress_pages_completed + 1
            # REGDOCS' total is approximate. Grow the denominator if a tail
            # probe discovers an additional page so progress never exceeds 100%.
            if progress_total_pages is not None and prospective > progress_total_pages:
                progress_total_pages = prospective
                self.progress.set_total(progress_total_pages)
            progress_pages_completed = prospective
            page_text = (
                f"{progress_pages_completed:,}/{progress_total_pages:,}"
                if progress_total_pages is not None
                else f"{progress_pages_completed:,}"
            )
            record_text = f"{len(records):,}"
            if reported_total is not None:
                record_text += f"/about {reported_total:,}"
            failure_text = f"; failed pages={pages_failed:,}" if pages_failed else ""
            outcome_text = "ok" if ok else "failed"
            self.progress.advance(
                1,
                message=(
                    f"{progress_label}: pages {page_text}; unique records {record_text}; "
                    f"last offset={offset:,} ({outcome_text}){failure_text}"
                ),
            )

        async def fetch_page(offset: int) -> tuple[int, FetchResult, list[SearchRecord]]:
            result = await self.http.get(
                RESULTS_URL, source_kind=source_kind,
                request_key=f"{request_prefix}:sr={offset}",
                params={**base_params, "sr": offset}, ajax=True,
            )
            if result.ok and not search_response_recognized(result.text or ""):
                self.db.add_error(
                    run_id=self.run_id,
                    stage="base_search" if source_kind == "search" else "facet_search",
                    code="UNEXPECTED_SEARCH_RESPONSE", severity="ERROR", retryable=True,
                    message="HTTP 200 response did not contain a recognizable REGDOCS result structure",
                    context={"request_key": f"{request_prefix}:sr={offset}", "snapshot_id": result.snapshot_id},
                )
                result.ok = False
                result.error_type = "UnexpectedSearchResponse"
                result.error_detail = "Unrecognized HTTP-200 search page"
            return offset, result, parse_search_rows(result.text or "") if result.ok else []

        _, first, first_rows = await fetch_page(1)
        if not first.ok:
            pages_failed = 1
            if progress_label is not None:
                progress_total_pages = 1
                self.progress.set_total(progress_total_pages)
                report_page(offset=1, ok=False)
            self.db.add_error(
                run_id=self.run_id,
                stage="base_search" if source_kind == "search" else "facet_search",
                code="FIRST_PAGE_REQUEST_FAILED", severity="ERROR", retryable=True,
                message=first.error_detail or "The first search page could not be fetched",
                context={"params": dict(params), "request_prefix": request_prefix},
            )
            return CrawlResult({}, None, 0, 1, False, [])

        pages_succeeded = 1
        if first.snapshot_id:
            snapshots.append(first.snapshot_id)
        for record in first_rows:
            records.setdefault(record.document_id, record)
        reported_total = parse_total(first.text or "")
        if progress_label is not None:
            progress_total_pages = expected_search_pages(
                reported_total, page_size=self.config.page_size, limit=limit
            )
            self.progress.set_total(progress_total_pages)
            report_page(offset=1, ok=True)
        if not first_rows:
            self.search_empty_shortcuts += 1
            return CrawlResult(records, reported_total, 1, 0, True, snapshots)
        if limit and len(records) >= limit:
            return CrawlResult(dict(list(records.items())[:limit]), reported_total, 1, 0, True, snapshots)
        if len(first_rows) < self.config.page_size:
            self.search_short_page_shortcuts += 1
            return CrawlResult(records, reported_total, 1, 0, True, snapshots)

        target = min(reported_total, limit) if reported_total is not None and limit is not None else reported_total
        offsets = list(range(1 + self.config.page_size, target + 1, self.config.page_size)) if target and target > self.config.page_size else []
        last_rows_count = len(first_rows)
        last_offset = 1
        batch_size = max(1, self.config.concurrency * 4)
        for batch_start in range(0, len(offsets), batch_size):
            results = await asyncio.gather(*(fetch_page(offset) for offset in offsets[batch_start:batch_start + batch_size]))
            for offset, result, rows in sorted(results, key=lambda item: item[0]):
                last_offset = offset
                if result.ok:
                    pages_succeeded += 1
                    last_rows_count = len(rows)
                    if result.snapshot_id:
                        snapshots.append(result.snapshot_id)
                    for record in rows:
                        records.setdefault(record.document_id, record)
                else:
                    pages_failed += 1
                report_page(offset=offset, ok=result.ok)
            if limit and len(records) >= limit:
                break

        next_offset = last_offset + self.config.page_size
        while not pages_failed and last_rows_count == self.config.page_size and (not limit or len(records) < limit):
            self.search_tail_probes += 1
            _, result, rows = await fetch_page(next_offset)
            if not result.ok:
                pages_failed += 1
                report_page(offset=next_offset, ok=False)
                break
            pages_succeeded += 1
            if result.snapshot_id:
                snapshots.append(result.snapshot_id)
            last_rows_count = len(rows)
            if rows:
                before = len(records)
                for record in rows:
                    records.setdefault(record.document_id, record)
                report_page(offset=next_offset, ok=True)
                if len(records) == before:
                    break
            else:
                report_page(offset=next_offset, ok=True)
                break
            next_offset += self.config.page_size

        if limit and len(records) > limit:
            records = dict(list(records.items())[:limit])
        return CrawlResult(records, reported_total, pages_succeeded, pages_failed, pages_failed == 0, snapshots)

    async def discover_base_records(self) -> None:
        self.progress.set_phase(
            "base_discovery",
            message=f"Discovering REGDOCS items from {self.config.start_date} through {self.config.end_date}",
        )
        logging.info("Base discovery: %s through %s, page size %d", self.config.start_date, self.config.end_date, self.config.page_size)
        result = await self.crawl_search(
            params={"sd": self.config.start_date, "ed": self.config.end_date},
            source_kind="search",
            request_prefix=f"base:{self.config.start_date}:{self.config.end_date}",
            limit=self.config.limit,
            progress_label="Base search",
        )
        self.base_search_pages_succeeded = result.pages_succeeded
        self.base_search_pages_failed = result.pages_failed
        self.base_search_reported_total = result.reported_total
        self.base_records = result.records
        self.initial_base_record_count = len(result.records)
        self.base_complete = result.complete
        if result.reported_total is not None and not self.config.limit:
            discrepancy = abs(result.reported_total - len(result.records))
            if discrepancy > self.config.page_size and result.complete:
                self.db.add_error(
                    run_id=self.run_id, stage="base_search",
                    code="REPORTED_TOTAL_DISCREPANCY", severity="WARNING",
                    message=(f"REGDOCS reported approximately {result.reported_total:,} items "
                             f"but the crawl parsed {len(result.records):,} unique items"),
                    context={"reported_total": result.reported_total, "parsed": len(result.records)},
                )
        if result.pages_failed:
            self.db.add_error(
                run_id=self.run_id, stage="base_search",
                code="SEARCH_PAGES_INCOMPLETE", severity="ERROR", retryable=True,
                message=f"{result.pages_failed} base-search page(s) failed after retries",
                context={"pages_succeeded": result.pages_succeeded, "pages_failed": result.pages_failed},
            )
        self.progress.set_phase("saving_base_metadata", total=len(result.records), message="Saving base item metadata")
        for record in tqdm(result.records.values(), desc="Saving base metadata", unit=" item"):
            if not self.config.dry_run:
                self.db.upsert_base_document(
                    record, run_id=self.run_id, base_complete=result.complete,
                    start_date=self.config.start_date, end_date=self.config.end_date,
                )
            self.progress.advance(1, message=f"Saved item {record.document_id}")
        logging.info("Base discovery saved %d unique item(s); complete=%s", len(result.records), result.complete)

    async def expand_containers(self) -> None:
        """Traverse the explicit REGDOCS Folder/Compound containment tree.

        Each container shell must declare its own ``Item/LoadResult`` endpoint.
        Only rows returned by those endpoints are followed. A queue and a seen
        set allow nested Folder -> Folder -> Compound structures while preventing
        loops. Depth and item-count guards bound unexpectedly large trees.
        """
        assert self.http is not None
        seeds = [
            record for record in list(self.base_records.values())
            if is_container_kind(record.kind)
        ]
        if not seeds:
            return

        queue: deque[tuple[SearchRecord, int]] = deque((record, 0) for record in seeds)
        queued_ids = {record.document_id for record in seeds}
        seen_ids: set[str] = set()
        self.container_containers = len(queued_ids)
        self.progress.set_phase(
            "container_expansion", total=len(queued_ids),
            message="Traversing explicit Compound Document and Folder members",
        )
        logging.info(
            "Container traversal: %d seed(s), max_depth=%d, max_items=%d",
            len(seeds), self.config.container_max_depth, self.config.container_max_items,
        )

        with tqdm(total=len(queued_ids), desc="REGDOCS containers", unit=" container") as bar:
            while queue:
                if self.container_attempted >= self.config.container_max_items:
                    self.container_limit_reached = True
                    self.db.add_error(
                        run_id=self.run_id, stage="container",
                        code="CONTAINER_TRAVERSAL_LIMIT_REACHED", severity="ERROR",
                        message=("Container traversal stopped at the configured maximum "
                                 f"of {self.config.container_max_items:,} containers"),
                        context={
                            "queued_remaining": len(queue),
                            "seen_containers": len(seen_ids),
                            "max_items": self.config.container_max_items,
                        },
                    )
                    break

                container, depth = queue.popleft()
                if container.document_id in seen_ids:
                    continue
                seen_ids.add(container.document_id)
                self.expanded_container_ids.add(container.document_id)
                self.container_attempted += 1

                shell_url = DETAIL_URL_TEMPLATE.format(item_id=container.document_id)
                shell_result = await self.http.get(
                    shell_url, source_kind="detail",
                    request_key=f"container-shell:{container.document_id}",
                    document_id=container.document_id,
                )
                if not shell_result.ok:
                    self.container_failed += 1
                    self.db.add_error(
                        run_id=self.run_id, document_id=container.document_id,
                        stage="container", code="CONTAINER_SHELL_REQUEST_FAILED",
                        severity="ERROR", retryable=True,
                        message=shell_result.error_detail or "REGDOCS container page request failed",
                        context={"attempts": shell_result.attempts, "url": shell_url, "depth": depth},
                    )
                    bar.update(1)
                    self.progress.advance(1, message=f"Container shell failed: {container.document_id}")
                    continue

                shell_html = shell_result.text or ""
                shell_final_url = shell_result.final_url or shell_url

                # The shell is also this container's own detail page, so reuse it.
                if not self.config.dry_run and self.config.include_details:
                    detail = parse_detail_page(shell_html, container.document_id, shell_final_url)
                    self.db.apply_detail(
                        document_id=container.document_id, data=detail,
                        normalized=self.normalized_detail_values(detail),
                        snapshot_id=shell_result.snapshot_id, run_id=self.run_id,
                        fallback_title=container.name,
                    )
                    self.detail_completed_this_run.add(container.document_id)
                    self.detail_succeeded += 1
                    self.detail_succeeded_from_container_shells += 1

                result_endpoint = parse_container_result_endpoint(
                    shell_html, shell_final_url, container.document_id
                )
                if not result_endpoint:
                    self.container_failed += 1
                    self.db.add_error(
                        run_id=self.run_id, document_id=container.document_id,
                        stage="container", code="CONTAINER_RESULT_ENDPOINT_NOT_FOUND",
                        severity="ERROR", retryable=True,
                        message=("REGDOCS container shell did not declare a recognizable "
                                 "REGDOCS member-list endpoint"),
                        context={
                            "snapshot_id": shell_result.snapshot_id,
                            "url": shell_final_url,
                            "depth": depth,
                        },
                    )
                    bar.update(1)
                    self.progress.advance(1, message=f"Container endpoint missing: {container.document_id}")
                    continue

                parsed: dict[str, SearchRecord] = {}
                reported_total: int | None = None
                explicit_empty_seen = False
                fragment_snapshot_ids: list[int] = []
                fragment_urls_seen: set[str] = set()
                next_url: str | None = result_endpoint
                fragment_failed = False

                while next_url and next_url not in fragment_urls_seen:
                    fragment_urls_seen.add(next_url)
                    fragment_result = await self.http.get(
                        next_url, source_kind="container",
                        request_key=f"container-members:{container.document_id}:{len(fragment_urls_seen)}",
                        document_id=container.document_id, ajax=True, referer=shell_final_url,
                    )
                    if not fragment_result.ok:
                        fragment_failed = True
                        self.db.add_error(
                            run_id=self.run_id, document_id=container.document_id,
                            stage="container", code="CONTAINER_RESULT_REQUEST_FAILED",
                            severity="ERROR", retryable=True,
                            message=fragment_result.error_detail or "REGDOCS container member-list request failed",
                            context={"attempts": fragment_result.attempts, "url": next_url, "depth": depth},
                        )
                        break

                    fragment_html = fragment_result.text or ""
                    if fragment_result.snapshot_id is not None:
                        fragment_snapshot_ids.append(fragment_result.snapshot_id)
                    if not search_response_recognized(fragment_html):
                        fragment_failed = True
                        self.db.add_error(
                            run_id=self.run_id, document_id=container.document_id,
                            stage="container", code="CONTAINER_RESULT_LIST_NOT_RECOGNIZED",
                            severity="ERROR", retryable=True,
                            message=("REGDOCS container member endpoint did not return a "
                                     "recognizable result fragment"),
                            context={
                                "snapshot_id": fragment_result.snapshot_id,
                                "url": fragment_result.final_url or next_url,
                                "result_endpoint": result_endpoint,
                                "depth": depth,
                            },
                        )
                        break

                    page_total = parse_total(fragment_html)
                    if page_total is not None:
                        reported_total = max(reported_total or 0, page_total)
                    page_members = parse_search_rows(
                        fragment_html, container_id=container.document_id
                    )
                    if not page_members and explicit_empty_result(fragment_html):
                        explicit_empty_seen = True
                    for member in page_members:
                        if member.document_id != container.document_id:
                            parsed.setdefault(member.document_id, member)

                    explicit_next = parse_next_container_result_url(
                        fragment_html, fragment_result.final_url or next_url
                    )
                    if not explicit_next or explicit_next in fragment_urls_seen:
                        break
                    next_url = explicit_next

                if fragment_failed:
                    self.container_failed += 1
                    bar.update(1)
                    self.progress.advance(1, message=f"Container result failed: {container.document_id}")
                    continue

                if reported_total is None and not parsed and explicit_empty_seen:
                    reported_total = 0
                complete = reported_total is not None and len(parsed) >= reported_total
                if reported_total is None:
                    self.container_incomplete += 1
                    self.db.add_error(
                        run_id=self.run_id, document_id=container.document_id,
                        stage="container", code="CONTAINER_TOTAL_NOT_FOUND",
                        severity="ERROR", retryable=True,
                        message=("Container response did not provide a result total or an "
                                 "explicit no-results message; membership completeness is unknown"),
                        context={
                            "parsed_members": len(parsed),
                            "snapshot_ids": fragment_snapshot_ids,
                            "result_endpoint": result_endpoint,
                            "explicit_empty": explicit_empty_seen,
                            "depth": depth,
                        },
                    )
                elif len(parsed) < reported_total:
                    self.container_incomplete += 1
                    self.db.add_error(
                        run_id=self.run_id, document_id=container.document_id,
                        stage="container", code="CONTAINER_MEMBERSHIP_INCOMPLETE",
                        severity="ERROR", retryable=True,
                        message=(f"REGDOCS container reported about {reported_total:,} member(s) "
                                 f"but only {len(parsed):,} unique row(s) were parsed"),
                        context={
                            "reported_total": reported_total,
                            "parsed_members": len(parsed),
                            "snapshot_ids": fragment_snapshot_ids,
                            "result_endpoint": result_endpoint,
                            "pages_fetched": len(fragment_urls_seen),
                            "depth": depth,
                        },
                    )
                elif reported_total == 0:
                    self.container_empty += 1

                members = list(parsed.values())
                self.container_members_parsed += len(members)
                for member in members:
                    already_known = member.document_id in self.base_records
                    if already_known:
                        self.container_members_already_known += 1
                    else:
                        self.container_members_added += 1
                        self.base_records[member.document_id] = member
                    if not self.config.dry_run:
                        self.db.upsert_base_document(
                            member, run_id=self.run_id, base_complete=complete,
                            start_date=self.config.start_date, end_date=self.config.end_date,
                            discovery_source="container",
                            container_id=container.document_id,
                            container_kind=container.kind,
                            mark_running=not self.config.repair_containers,
                        )

                if not self.config.dry_run:
                    self.db.apply_container_membership(
                        container=container, members=members, run_id=self.run_id,
                        snapshot_ids=fragment_snapshot_ids,
                        result_endpoint=result_endpoint,
                        reported_total=reported_total, complete=complete,
                    )
                    if complete:
                        self.db.resolve_errors(
                            document_id=container.document_id, stage="container"
                        )
                        self.db.resolve_errors(
                            document_id=container.document_id, stage="compound"
                        )
                    else:
                        self.db.resolve_errors(
                            document_id=container.document_id, stage="container",
                            codes=[
                                "CONTAINER_REQUEST_FAILED",
                                "CONTAINER_SHELL_REQUEST_FAILED",
                                "CONTAINER_RESULT_ENDPOINT_NOT_FOUND",
                                "CONTAINER_RESULT_REQUEST_FAILED",
                                "CONTAINER_RESULT_LIST_NOT_RECOGNIZED",
                            ],
                        )
                        self.db.resolve_errors(
                            document_id=container.document_id, stage="compound",
                            codes=[
                                "COMPOUND_REQUEST_FAILED",
                                "COMPOUND_SHELL_REQUEST_FAILED",
                                "COMPOUND_RESULT_ENDPOINT_NOT_FOUND",
                                "COMPOUND_RESULT_REQUEST_FAILED",
                                "COMPOUND_RESULT_LIST_NOT_RECOGNIZED",
                            ],
                        )

                # Enqueue only explicit child containers. No generic links are followed.
                for member in members:
                    if not is_container_kind(member.kind):
                        continue
                    self.container_nested_relationships.add(
                        (container.document_id, member.document_id)
                    )
                    if member.document_id in seen_ids or member.document_id in queued_ids:
                        continue
                    child_depth = depth + 1
                    if child_depth > self.config.container_max_depth:
                        self.container_max_depth_reached += 1
                        self.db.add_error(
                            run_id=self.run_id, document_id=member.document_id,
                            stage="container", code="CONTAINER_MAX_DEPTH_REACHED",
                            severity="ERROR", retryable=False,
                            message=("Nested container was not expanded because it exceeded "
                                     f"--container-max-depth={self.config.container_max_depth}"),
                            context={
                                "parent_container_id": container.document_id,
                                "depth": child_depth,
                                "max_depth": self.config.container_max_depth,
                            },
                        )
                        continue
                    queue.append((member, child_depth))
                    queued_ids.add(member.document_id)
                    self.container_nested_newly_queued += 1
                    self.container_containers = len(queued_ids)
                    self.progress.set_total(len(queued_ids))
                    with contextlib.suppress(Exception):
                        bar.total = len(queued_ids)
                        bar.refresh()

                self.container_succeeded += 1
                bar.update(1)
                state = "empty" if complete and reported_total == 0 else (
                    "complete" if complete else "incomplete"
                )
                self.progress.advance(
                    1,
                    message=(f"Expanded container {container.document_id} at depth {depth}: "
                             f"{len(members)} member(s), {state}"),
                )

        self.container_containers = len(queued_ids)

    async def enrich_facets(self) -> None:
        assert self.http is not None
        self.progress.set_phase("facet_catalog", message="Discovering live REGDOCS facets")
        response = await self.http.get(ADVANCED_URL, source_kind="advanced", request_key="facet-catalog")
        if not response.ok:
            self.db.add_error(
                run_id=self.run_id, stage="facet_catalog",
                code="FACET_CATALOG_REQUEST_FAILED", severity="ERROR", retryable=True,
                message=response.error_detail or "Could not fetch Advanced Search",
            )
            return
        catalog = parse_facet_catalog(response.text or "")
        if not catalog:
            self.db.add_error(
                run_id=self.run_id, stage="facet_catalog",
                code="FACET_CATALOG_PARSE_EMPTY", severity="ERROR",
                message="Advanced Search returned no parseable facet categories",
                context={"snapshot_id": response.snapshot_id},
            )
            return
        self.facet_catalog_ok = True
        selected = self.select_facet_categories(catalog)
        total_values = sum(len(catalog[category]) for category in selected)
        self.progress.set_phase("facet_enrichment", total=total_values, message=f"Applying {total_values} facet values")
        logging.info("Facet enrichment: %d categories, %d values", len(selected), total_values)
        base_ids = set(self.base_records)
        for category in selected:
            matches: dict[str, list[tuple[str, str]]] = defaultdict(list)
            category_complete = True
            with tqdm(total=len(catalog[category]), desc=f"Facet: {category}", unit=" value") as bar:
                for filter_id, label in catalog[category].items():
                    result = await self.crawl_search(
                        params={"sd": self.config.start_date, "ed": self.config.end_date, "rds": filter_id},
                        source_kind="facet",
                        request_prefix=f"facet:{slugify(category)}:{filter_id}",
                    )
                    if not result.complete:
                        category_complete = False
                        self.db.add_error(
                            run_id=self.run_id, stage="facet_search",
                            code="FACET_VALUE_INCOMPLETE", severity="ERROR", retryable=True,
                            message=f"Facet search incomplete: {category} = {label}",
                            context={"category": category, "filter_id": filter_id, "label": label},
                        )
                    for document_id in result.records.keys() & base_ids:
                        matches[document_id].append((filter_id, label))
                    bar.update(1)
                    self.progress.advance(1, message=f"Completed facet {category} = {label}")
            self.facet_categories_complete[category] = category_complete
            if not self.config.dry_run:
                self.db.apply_facet_category(
                    document_ids=list(base_ids), category=category, matches=matches,
                    run_id=self.run_id, category_complete=category_complete,
                )

    def select_facet_categories(self, catalog: Mapping[str, Mapping[str, str]]) -> list[str]:
        if self.config.facets == "all":
            return list(catalog)
        wanted = [part.strip() for part in self.config.facets.split(",") if part.strip()]
        by_slug = {slugify(category): category for category in catalog}
        selected = []
        for requested in wanted:
            match = by_slug.get(slugify(requested))
            if match:
                selected.append(match)
            else:
                self.db.add_error(
                    run_id=self.run_id, stage="facet_catalog",
                    code="REQUESTED_FACET_NOT_FOUND", severity="WARNING",
                    message=f"Requested facet category '{requested}' was not present",
                    context={"available_categories": list(catalog)},
                )
        return dedupe_strings(selected)

    async def enrich_details(self) -> None:
        records = list(self.base_records.values())
        self.progress.set_phase("detail_enrichment", total=len(records), message="Fetching each item's own detail page")
        logging.info("Detail enrichment: %d item(s)", len(records))
        with tqdm(total=len(records), desc="Detail pages", unit=" item") as bar:
            batch_size = max(1, self.config.concurrency * 2)
            for start in range(0, len(records), batch_size):
                batch = records[start:start + batch_size]
                results = await asyncio.gather(*(self.fetch_one_detail(record) for record in batch))
                for record, outcome in zip(batch, results):
                    bar.update(1)
                    self.progress.advance(1, message=f"Detail {outcome}: {record.document_id}")

    async def fetch_one_detail(self, record: SearchRecord) -> str:
        assert self.http is not None
        item_id = record.document_id
        if record.is_synthetic_container_row:
            self.detail_skipped_not_applicable += 1
            return "NOT_APPLICABLE"
        if item_id in self.detail_completed_this_run:
            return "REUSED_CONTAINER_PAGE"
        if not self.config.refresh_details and self.db.detail_is_fresh(item_id, self.config.detail_refresh_days):
            self.detail_skipped_fresh += 1
            return "SKIPPED_FRESH"
        self.detail_attempted += 1
        if not self.config.dry_run:
            self.db.mark_detail_started(item_id)
        url = DETAIL_URL_TEMPLATE.format(item_id=item_id)
        result = await self.http.get(
            url, source_kind="detail", request_key=f"detail:{item_id}", document_id=item_id,
        )
        if not result.ok:
            self.detail_failed += 1
            if not self.config.dry_run:
                self.db.mark_detail_failed(item_id, "REQUEST_FAILED", self.run_id)
            self.db.add_error(
                run_id=self.run_id, document_id=item_id, stage="detail",
                code="DETAIL_REQUEST_FAILED", severity="WARNING", retryable=True,
                message=result.error_detail or "Detail page request failed",
                context={"attempts": result.attempts, "url": url},
            )
            return "REQUEST_FAILED"
        data = parse_detail_page(result.text or "", item_id, result.final_url or url)
        if not data.fields and data.visible_text_length < 100:
            self.detail_failed += 1
            if not self.config.dry_run:
                self.db.mark_detail_failed(item_id, "PARSE_EMPTY", self.run_id)
            self.db.add_error(
                run_id=self.run_id, document_id=item_id, stage="detail",
                code="DETAIL_PARSE_EMPTY", severity="WARNING",
                message="Detail page contained no usable item metadata",
                context={"snapshot_id": result.snapshot_id, "visible_text_length": data.visible_text_length},
            )
            return "PARSE_EMPTY"
        self.detail_succeeded += 1
        self.detail_succeeded_from_detail_requests += 1
        if not self.config.dry_run:
            self.db.apply_detail(
                document_id=item_id, data=data,
                normalized=self.normalized_detail_values(data),
                snapshot_id=result.snapshot_id, run_id=self.run_id,
                fallback_title=record.name,
            )
        return "SUCCEEDED"

    @staticmethod
    def normalized_detail_values(data: DetailData) -> dict[str, str]:
        output = {}
        for label, values in data.fields.items():
            canonical = canonical_field_for_label(label)
            if canonical and values and canonical not in output:
                output[canonical] = values[0]
        return output



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scout REGDOCS metadata and explicit nested members of Compound Document and Folder items into a five-table SQLite pipeline ledger.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--db", default="regdocs.db")
    parser.add_argument("--raw-dir", default="raw/regdocs")
    parser.add_argument("--progress-file", default="_audit/scout-progress.json")
    parser.add_argument("--log-file", default="_audit/scout.log")
    parser.add_argument("--start-date", help="YYYY-MM-DD")
    parser.add_argument("--end-date", help="YYYY-MM-DD")
    parser.add_argument("--page-size", type=int, choices=PAGE_SIZES, default=200)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--facets", default="all", help="all, none, or comma-separated categories")
    parser.add_argument(
        "--expand-containers", "--expand-compounds", dest="expand_containers",
        action=argparse.BooleanOptionalAction, default=True,
        help=("Traverse explicit result rows declared by Compound Document and Folder pages, "
              "including nested containers; arbitrary links are never followed"),
    )
    parser.add_argument(
        "--repair-containers", action="store_true",
        help=("Reprocess all known Folder and Compound Document rows already in SQLite, "
              "without rerunning the date search, facet searches, or every non-container detail page"),
    )
    parser.add_argument(
        "--container-max-depth", type=int, default=20,
        help="Maximum explicit Folder/Compound nesting depth (seed containers are depth 0)",
    )
    parser.add_argument(
        "--container-max-items", type=int, default=10000,
        help="Maximum number of unique containers expanded in one run",
    )
    parser.add_argument(
        "--details", dest="include_details", action=argparse.BooleanOptionalAction,
        default=True, help="Fetch each selected item's own detail page",
    )
    parser.add_argument("--detail-refresh-days", type=int, default=30)
    parser.add_argument("--refresh-details", action="store_true")
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--min-delay", type=float, default=2.0)
    parser.add_argument("--max-delay", type=float, default=4.0)
    parser.add_argument("--max-retries", type=int, default=4)
    parser.add_argument("--retry-backoff", type=float, default=2.0)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Fetch and parse without updating documents; runs, errors, and raw snapshots remain durable",
    )
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--force-lock", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--status-json", action="store_true")
    parser.add_argument("--show-defaults", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--version", action="store_true", help="Print script identity and exit")
    parser.add_argument("--check-schema", action="store_true", help="List database tables and verify the five-table schema")
    parser.add_argument(
        "--audit", action="store_true",
        help=("Run a read-only ledger audit: SQLite integrity, container counts and "
              "backlinks, snapshot references, raw gzip sizes, and SHA-256 hashes"),
    )
    return parser


def validate_args(args: argparse.Namespace) -> None:
    if args.limit is not None and args.limit < 0:
        raise ValueError("--limit cannot be negative")
    if args.detail_refresh_days < 0:
        raise ValueError("--detail-refresh-days cannot be negative")
    if args.container_max_depth < 0:
        raise ValueError("--container-max-depth cannot be negative")
    if args.container_max_items < 1:
        raise ValueError("--container-max-items must be at least 1")
    if args.repair_containers and not args.expand_containers:
        raise ValueError("--repair-containers requires --expand-containers")
    if args.concurrency < 1:
        raise ValueError("--concurrency must be at least 1")
    if args.min_delay < 0 or args.max_delay < 0:
        raise ValueError("request delays cannot be negative")
    if args.max_delay < args.min_delay:
        raise ValueError("--max-delay must be greater than or equal to --min-delay")
    if args.max_retries < 0:
        raise ValueError("--max-retries cannot be negative")
    if args.retry_backoff < 1:
        raise ValueError("--retry-backoff must be at least 1")


def resolve_relative_path(value: str | Path, *, base: Path) -> Path:
    path = Path(value).expanduser()
    return (base / path).resolve() if not path.is_absolute() else path.resolve()


def resolve_gold_dates(db: PipelineDB, start_arg: str | None, end_arg: str | None) -> tuple[str, str, str]:
    today = date.today()
    if start_arg or end_arg:
        end_date = validate_date(end_arg or today.isoformat())
        end_value = date.fromisoformat(end_date)
        start_date = validate_date(start_arg or f"{end_value.year:04d}-01-01")
        return start_date, end_date, "explicit"
    year_start = date(today.year, 1, 1)
    count = int(db.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0])
    if count == 0:
        return year_start.isoformat(), today.isoformat(), "auto_bootstrap_current_year"
    last_full = None
    for row in db.conn.execute(
        "SELECT started_at, parameters_json FROM runs WHERE stage='scout' AND status='SUCCEEDED' ORDER BY id DESC"
    ):
        params = parse_json_object(row["parameters_json"])
        if params.get("start_date") == year_start.isoformat() and str(params.get("date_mode", "")).startswith("auto_"):
            with contextlib.suppress(ValueError):
                last_full = datetime.fromisoformat(str(row["started_at"]))
            break
    now_utc = datetime.now(timezone.utc)
    if last_full is None or now_utc - last_full.astimezone(timezone.utc) >= timedelta(days=30):
        return year_start.isoformat(), today.isoformat(), "auto_current_year_refresh"
    newest_raw = db.latest_filing_date()
    newest = date.fromisoformat(newest_raw) if newest_raw else today
    overlap_start = max(year_start, newest - timedelta(days=7))
    return min(overlap_start, today).isoformat(), today.isoformat(), "auto_incremental_7_day_overlap"


def default_profile() -> dict[str, Any]:
    return {
        "database_tables": ["documents", "runs", "errors", "raw_snapshots", "files"],
        "date_policy": {
            "new_database": "January 1 of current year through today",
            "normal_repeat": "newest stored filing date minus 7 days through today",
            "full_refresh": "current year at least every 30 days",
        },
        "scope": ("date-search items, explicit nested Compound Document/Folder members, live facets, "
                  "and each scouted item's own detail page"),
        "expand_containers": True,
        "container_max_depth": 20,
        "container_max_items": 10000,
        "repair_mode": "--repair-containers reprocesses known containers without date/facet/non-container-detail searches",
        "follows_outbound_links": False,
        "facets": "all live categories",
        "page_size": 200,
        "concurrency": 1,
        "request_start_delay_seconds": [2.0, 4.0],
        "max_retries": 4,
        "detail_refresh_days": 30,
        "raw_snapshots": True,
        "audit_command": "python regdocs_1_scout.py --audit",
    }


def read_status(db_path: Path, progress_file: Path) -> dict[str, Any]:
    state = {}
    if progress_file.exists():
        with contextlib.suppress(OSError, json.JSONDecodeError):
            loaded = json.loads(progress_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                state.update(loaded)
    if db_path.exists():
        conn = None
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
            if row:
                latest = dict(row)
                state["database_run"] = latest
                state["summary"] = parse_json_object(latest.get("summary_json"))
                state["unresolved_errors"] = int(conn.execute("SELECT COUNT(*) FROM errors WHERE resolved_at IS NULL").fetchone()[0])
        except sqlite3.Error as exc:
            state["database_read_error"] = clean_text(exc)
        finally:
            if conn is not None:
                conn.close()
    heartbeat = state.get("heartbeat_at")
    if not heartbeat and isinstance(state.get("database_run"), Mapping):
        heartbeat = state["database_run"].get("heartbeat_at")
    if heartbeat:
        with contextlib.suppress(ValueError):
            parsed = datetime.fromisoformat(str(heartbeat))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            age = max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds())
            state["heartbeat_age_seconds"] = round(age, 1)
            status = state.get("status") or state.get("database_run", {}).get("status")
            state["heartbeat_stale"] = status == "RUNNING" and age > 120
    state.setdefault("database", str(db_path))
    state.setdefault("progress_file", str(progress_file))
    return state


def format_duration(seconds: Any) -> str:
    try:
        total = max(0, int(float(seconds)))
    except (TypeError, ValueError):
        return "unknown"
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}h {minutes:02d}m {secs:02d}s" if hours else f"{minutes}m {secs:02d}s"


def format_status_text(state: Mapping[str, Any]) -> str:
    run = state.get("database_run") if isinstance(state.get("database_run"), Mapping) else {}
    status = state.get("status") or run.get("status") or "NO RUN"
    run_id = state.get("run_id") or run.get("id") or "?"
    phase = state.get("phase") or run.get("current_phase") or "unknown"
    completed = state.get("completed_units", run.get("completed_units", 0))
    total = state.get("total_units", run.get("total_units"))
    percentage = state.get("percentage")
    if percentage is None and total:
        percentage = completed * 100.0 / total
    item_line = f"Items: {completed:,} / {total:,}  {percentage:.1f}%" if total else f"Items: {completed:,} completed"
    logical = state.get("logical_requests", run.get("logical_requests", 0))
    attempts = state.get("http_attempts", run.get("http_attempts", 0))
    retries = state.get("retries", run.get("retries", 0))
    failures = state.get("failed_requests", run.get("failed_requests", 0))
    lines = [
        f"Run {run_id} | {status} | {str(phase).replace('_', ' ').upper()}",
        item_line,
        f"Requests: {logical:,} logical | {attempts:,} attempts | retries {retries:,} | failed attempts {failures:,}",
        f"Elapsed: {format_duration(state.get('elapsed_seconds'))} | ETA: {format_duration(state.get('eta_seconds'))}",
        (f"Heartbeat: {state['heartbeat_age_seconds']}s ago" if state.get("heartbeat_age_seconds") is not None else "Heartbeat: unknown")
        + (" (STALE)" if state.get("heartbeat_stale") else ""),
        f"Unresolved errors: {state.get('unresolved_errors', 'unknown')}",
        f"Current: {state.get('message') or run.get('progress_message') or ''}",
        f"Database: {state.get('database')}",
    ]
    return "\n".join(lines)



def script_identity() -> dict[str, Any]:
    path = Path(__file__).resolve()
    return {
        "script": str(path),
        "script_version": SCRIPT_VERSION,
        "parser_version": PARSER_VERSION,
        "schema_version": SCHEMA_VERSION,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "expected_user_tables": sorted(EXPECTED_USER_TABLES),
    }


def check_schema(db_path: Path) -> tuple[bool, dict[str, Any]]:
    if not db_path.exists():
        return False, {"database": str(db_path), "exists": False, "tables": []}
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    try:
        tables = sorted(
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        )
        expected = sorted(EXPECTED_USER_TABLES)
        return tables == expected, {
            "database": str(db_path),
            "exists": True,
            "tables": tables,
            "expected_user_tables": expected,
            "unexpected_tables": sorted(set(tables) - EXPECTED_USER_TABLES),
            "missing_tables": sorted(EXPECTED_USER_TABLES - set(tables)),
            "user_version": int(conn.execute("PRAGMA user_version").fetchone()[0]),
        }
    finally:
        conn.close()


def audit_database(
    db_path: Path,
    *,
    verify_raw: bool = True,
    container_snapshots_only: bool = False,
    fail_on_unresolved: bool = True,
    max_issue_examples: int = 50,
) -> tuple[bool, dict[str, Any]]:
    """Run a read-only integrity audit of the pipeline ledger and raw archive.

    The audit verifies container manifests in both directions, reported/parsed
    counts, nested-container relationships, snapshot references, SQLite
    integrity, and (optionally) gzip size and SHA-256 evidence. It performs no
    network requests and never modifies the database or raw archive.
    """
    report: dict[str, Any] = {
        "database": str(db_path),
        "exists": db_path.exists(),
        "verify_raw": verify_raw,
        "container_snapshots_only": container_snapshots_only,
        "issues": [],
    }
    if not db_path.exists():
        report["issues"].append("database does not exist")
        return False, report

    issue_count_total = 0

    def add_issue(message: str) -> None:
        nonlocal issue_count_total
        issue_count_total += 1
        issues = report["issues"]
        if isinstance(issues, list) and len(issues) < max_issue_examples:
            issues.append(clean_text(message))

    def optional_int(value: Any) -> int | None:
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        tables = {
            str(row[0]) for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        schema_ok = tables == EXPECTED_USER_TABLES
        report.update(
            database_tables=sorted(tables),
            schema_ok=schema_ok,
            missing_tables=sorted(EXPECTED_USER_TABLES - tables),
            unexpected_tables=sorted(tables - EXPECTED_USER_TABLES),
        )
        if not schema_ok:
            add_issue("database tables do not match the five-table ledger schema")
            return False, report

        quick_check_rows = [str(row[0]) for row in conn.execute("PRAGMA quick_check")]
        foreign_key_violations = [tuple(row) for row in conn.execute("PRAGMA foreign_key_check")]
        report["sqlite_quick_check"] = quick_check_rows
        report["foreign_key_violations"] = len(foreign_key_violations)
        if quick_check_rows != ["ok"]:
            add_issue("SQLite quick_check did not return ok")
        if foreign_key_violations:
            add_issue(f"SQLite foreign-key violations: {len(foreign_key_violations)}")

        document_rows = conn.execute(
            "SELECT id, item_kind, metadata FROM documents ORDER BY id"
        ).fetchall()
        documents: dict[str, tuple[str, dict[str, Any]]] = {}
        for row in document_rows:
            documents[str(row["id"])] = (
                clean_text(row["item_kind"]), parse_json_object(row["metadata"])
            )
        report["documents_total"] = len(documents)

        containers_total = 0
        containers_complete = 0
        containers_empty = 0
        incomplete_containers = 0
        missing_container_metadata = 0
        duplicate_member_ids = 0
        missing_member_documents = 0
        missing_child_backlinks = 0
        orphan_child_backlinks = 0
        reported_count_mismatches = 0
        parsed_count_mismatches = 0
        effective_count_mismatches = 0
        missing_result_endpoints = 0
        endpoint_mismatches = 0
        containers_without_snapshot_references = 0
        missing_container_snapshot_rows = 0
        container_snapshot_wrong_kind = 0
        nested_relationships: set[tuple[str, str]] = set()
        referenced_container_snapshot_ids: set[int] = set()
        parent_members: dict[str, set[str]] = {}

        snapshot_rows = {
            int(row["id"]): row for row in conn.execute(
                "SELECT id, source_kind, source_url, content_sha256, size_bytes, "
                "compressed_size_bytes, relative_path FROM raw_snapshots"
            )
        }

        for document_id, (item_kind, metadata) in documents.items():
            if not is_container_kind(item_kind):
                continue
            containers_total += 1
            container = parse_json_object(metadata.get("container"))
            if not container:
                container = parse_json_object(metadata.get("compound"))
            if not container:
                missing_container_metadata += 1
                add_issue(f"container {document_id} has no membership metadata")
                parent_members[document_id] = set()
                continue

            raw_member_ids = container.get("member_ids", [])
            member_ids = [str(value) for value in raw_member_ids] if isinstance(raw_member_ids, list) else []
            unique_member_ids = set(member_ids)
            parent_members[document_id] = unique_member_ids
            duplicates_here = len(member_ids) - len(unique_member_ids)
            duplicate_member_ids += duplicates_here
            if duplicates_here:
                add_issue(f"container {document_id} has {duplicates_here} duplicate member id(s)")

            complete = container.get("membership_complete") is True
            if complete:
                containers_complete += 1
            else:
                incomplete_containers += 1
                add_issue(f"container {document_id} membership is incomplete")
            state = clean_text(container.get("membership_state")).upper()
            if state == "EMPTY":
                containers_empty += 1
                if member_ids:
                    add_issue(f"container {document_id} is marked EMPTY but has members")
                    effective_count_mismatches += 1

            parsed_count_raw = container.get("member_count_parsed")
            effective_count_raw = container.get("member_count_effective")
            reported_count_raw = container.get("member_count_reported")
            parsed_count = optional_int(parsed_count_raw)
            effective_count = optional_int(effective_count_raw)
            reported_count = optional_int(reported_count_raw)
            observed_ids = container.get("member_ids_observed_this_run", [])
            observed_count = len(observed_ids) if isinstance(observed_ids, list) else 0
            if parsed_count_raw not in (None, "") and parsed_count != observed_count:
                parsed_count_mismatches += 1
                add_issue(
                    f"container {document_id} parsed count {parsed_count_raw} != observed ids {observed_count}"
                )
            if effective_count_raw not in (None, "") and effective_count != len(unique_member_ids):
                effective_count_mismatches += 1
                add_issue(
                    f"container {document_id} effective count {effective_count_raw} != unique members {len(unique_member_ids)}"
                )
            if complete and reported_count_raw not in (None, "") and reported_count != observed_count:
                reported_count_mismatches += 1
                add_issue(
                    f"container {document_id} reported count {reported_count_raw} != parsed members {observed_count}"
                )

            endpoint = clean_text(container.get("result_endpoint"))
            if not endpoint:
                missing_result_endpoints += 1
                add_issue(f"container {document_id} has no result endpoint")
            elif not endpoint.rstrip("/").endswith(f"/REGDOCS/Item/LoadResult/{document_id}"):
                endpoint_mismatches += 1
                add_issue(f"container {document_id} result endpoint does not match its id")

            raw_snapshot_ids = container.get("snapshot_ids", [])
            ids = [int(value) for value in raw_snapshot_ids if str(value).isdigit()] \
                if isinstance(raw_snapshot_ids, list) else []
            primary = container.get("snapshot_id")
            if primary is not None and str(primary).isdigit():
                ids.append(int(primary))
            unique_snapshot_ids = set(ids)
            if verify_raw and not unique_snapshot_ids:
                containers_without_snapshot_references += 1
                add_issue(f"container {document_id} has no raw member-list snapshot reference")
            for snapshot_id in unique_snapshot_ids:
                referenced_container_snapshot_ids.add(snapshot_id)
                snapshot = snapshot_rows.get(snapshot_id)
                if snapshot is None:
                    missing_container_snapshot_rows += 1
                    add_issue(f"container {document_id} references missing snapshot {snapshot_id}")
                elif clean_text(snapshot["source_kind"]).casefold() != "container":
                    container_snapshot_wrong_kind += 1
                    add_issue(
                        f"container {document_id} snapshot {snapshot_id} has source kind {snapshot['source_kind']}"
                    )

            for member_id in unique_member_ids:
                child = documents.get(member_id)
                if child is None:
                    missing_member_documents += 1
                    add_issue(f"container {document_id} references missing document {member_id}")
                    continue
                child_kind, child_metadata = child
                if is_container_kind(child_kind):
                    nested_relationships.add((document_id, member_id))
                memberships = child_metadata.get("container_memberships")
                if not isinstance(memberships, list):
                    memberships = child_metadata.get("compound_memberships", [])
                has_backlink = any(
                    isinstance(value, Mapping)
                    and str(value.get("container_id") or value.get("compound_id")) == document_id
                    for value in memberships
                ) if isinstance(memberships, list) else False
                if not has_backlink:
                    missing_child_backlinks += 1
                    add_issue(f"document {member_id} lacks backlink to container {document_id}")

        # Check the reverse direction as well: every child backlink must exist in
        # the parent manifest.
        for child_id, (_, metadata) in documents.items():
            memberships = metadata.get("container_memberships")
            if not isinstance(memberships, list):
                memberships = metadata.get("compound_memberships", [])
            if not isinstance(memberships, list):
                continue
            for value in memberships:
                if not isinstance(value, Mapping):
                    continue
                parent_id = str(value.get("container_id") or value.get("compound_id") or "")
                if not parent_id:
                    continue
                if parent_id not in parent_members or child_id not in parent_members[parent_id]:
                    orphan_child_backlinks += 1
                    add_issue(f"document {child_id} has orphan backlink to container {parent_id}")

        membership_mismatches = sum([
            duplicate_member_ids,
            missing_member_documents,
            missing_child_backlinks,
            orphan_child_backlinks,
            reported_count_mismatches,
            parsed_count_mismatches,
            effective_count_mismatches,
        ])
        report.update(
            containers_total=containers_total,
            containers_complete=containers_complete,
            containers_empty=containers_empty,
            incomplete_containers=incomplete_containers,
            missing_container_metadata=missing_container_metadata,
            nested_container_relationships_total=len(nested_relationships),
            duplicate_member_ids=duplicate_member_ids,
            missing_member_documents=missing_member_documents,
            missing_child_backlinks=missing_child_backlinks,
            orphan_child_backlinks=orphan_child_backlinks,
            reported_count_mismatches=reported_count_mismatches,
            parsed_count_mismatches=parsed_count_mismatches,
            effective_count_mismatches=effective_count_mismatches,
            membership_mismatches=membership_mismatches,
            missing_result_endpoints=missing_result_endpoints,
            endpoint_mismatches=endpoint_mismatches,
            containers_without_snapshot_references=containers_without_snapshot_references,
            container_snapshot_references=len(referenced_container_snapshot_ids),
            missing_container_snapshot_rows=missing_container_snapshot_rows,
            container_snapshot_wrong_kind=container_snapshot_wrong_kind,
        )

        unresolved = conn.execute(
            "SELECT severity, COUNT(*) AS count FROM errors "
            "WHERE resolved_at IS NULL GROUP BY severity"
        ).fetchall()
        unresolved_by_severity = {
            clean_text(row["severity"]).upper(): int(row["count"]) for row in unresolved
        }
        unresolved_total = sum(unresolved_by_severity.values())
        report["unresolved_errors"] = unresolved_total
        report["unresolved_errors_by_severity"] = unresolved_by_severity
        if fail_on_unresolved and unresolved_total:
            add_issue(f"unresolved error-ledger entries: {unresolved_total}")

        raw_snapshot_failures = 0
        raw_snapshots_checked = 0
        raw_snapshots_verified = 0
        raw_files_checked = 0
        container_snapshots_verified = 0
        if verify_raw:
            selected_ids = (
                referenced_container_snapshot_ids
                if container_snapshots_only
                else set(snapshot_rows)
            )
            verification_cache: dict[tuple[str, str, int, int], bool] = {}
            for snapshot_id in sorted(selected_ids):
                row = snapshot_rows.get(snapshot_id)
                if row is None:
                    continue
                raw_snapshots_checked += 1
                relative = Path(clean_text(row["relative_path"]))
                raw_path = relative if relative.is_absolute() else db_path.parent / relative
                key = (
                    str(raw_path), clean_text(row["content_sha256"]),
                    int(row["size_bytes"]), int(row["compressed_size_bytes"]),
                )
                verified = verification_cache.get(key)
                if verified is None:
                    raw_files_checked += 1
                    verified = True
                    try:
                        if not raw_path.is_file():
                            raise FileNotFoundError(raw_path)
                        if raw_path.stat().st_size != int(row["compressed_size_bytes"]):
                            raise ValueError("compressed size mismatch")
                        with gzip.open(raw_path, "rb") as stream:
                            payload = stream.read()
                        if len(payload) != int(row["size_bytes"]):
                            raise ValueError("uncompressed size mismatch")
                        digest = hashlib.sha256(payload).hexdigest()
                        if digest != clean_text(row["content_sha256"]):
                            raise ValueError("SHA-256 mismatch")
                    except (OSError, EOFError, ValueError) as exc:
                        verified = False
                        add_issue(f"raw snapshot {snapshot_id} failed verification: {exc}")
                    verification_cache[key] = verified
                if verified:
                    raw_snapshots_verified += 1
                    if snapshot_id in referenced_container_snapshot_ids:
                        container_snapshots_verified += 1
                else:
                    raw_snapshot_failures += 1

        report.update(
            raw_snapshots_checked=raw_snapshots_checked,
            raw_snapshots_verified=raw_snapshots_verified,
            raw_files_checked=raw_files_checked,
            raw_snapshot_failures=raw_snapshot_failures,
            container_snapshots_verified=container_snapshots_verified,
        )

        structural_failures = sum([
            0 if quick_check_rows == ["ok"] else 1,
            len(foreign_key_violations),
            incomplete_containers,
            missing_container_metadata,
            membership_mismatches,
            missing_result_endpoints,
            endpoint_mismatches,
            containers_without_snapshot_references,
            missing_container_snapshot_rows,
            container_snapshot_wrong_kind,
            raw_snapshot_failures,
        ])
        report["structural_failures"] = structural_failures
        ok = schema_ok and structural_failures == 0
        if fail_on_unresolved:
            ok = ok and unresolved_total == 0
        report["ok"] = ok
        report["issue_count_total"] = issue_count_total
        report["issue_examples_truncated"] = issue_count_total > len(report["issues"])
        return ok, report
    finally:
        conn.close()


def run_self_test() -> int:
    search_html = """
    <div>Item(s) - 1 to 1 out of about 1</div><table><tbody><tr>
      <td><a href='/REGDOCS/File/Download/123'>C40283-4 Example - A9V8L2</a>
      <details><a href='/REGDOCS/Item/View/900'>See all Documents for this Filing: C40283</a>
      <hr/><div>Preamble: example snippet</div></details></td>
      <td>2026-08-05</td><td>Example Energy Ltd.</td>
    </tr></tbody></table>
    """
    rows = parse_search_rows(search_html)
    assert len(rows) == 1 and rows[0].document_id == "123"
    assert rows[0].filing_number == "C40283"
    assert search_response_recognized(search_html)
    assert not search_response_recognized("<html><h1>Service unavailable</h1></html>")
    container_shell_html = """
    <html><head><title>Canada Energy Regulator - REGDOCS - C99999 Filing</title></head><body>
      <section class="modal-content"><h1>Feedback</h1></section>
      <main><h1 id="page-title">C99999 Filing</h1>
      <input id="ParentId" value="200">
      <section id="section-items" class="section-ajax"
               data-ajax-replace="/REGDOCS/Item/LoadResult/200"></section></main>
    </body></html>
    """
    assert parse_container_result_endpoint(
        container_shell_html, "https://apps.cer-rec.gc.ca/REGDOCS/Item/View/200", "200"
    ) == "https://apps.cer-rec.gc.ca/REGDOCS/Item/LoadResult/200"

    assert parse_detail_page(
        container_shell_html, "200", "https://apps.cer-rec.gc.ca/REGDOCS/Item/View/200"
    ).title == "C99999 Filing"

    container_html = """
    <html><body><h1>C40396 Example Filing</h1>
    <div>Item(s) - 1 to 2 out of about 2</div><table><tbody>
      <tr><td><i title='PDF Document'></i><a href='/REGDOCS/File/Download/201'>C40396-1 Application - EN - A9V8G1</a></td>
          <td>2026-08-04</td><td>Example Energy Inc.</td></tr>
      <tr><td><span title='Html Document'></span><a href='/REGDOCS/File/Download/202'>C40396-2 Application - EN - HTML - A9V8G2</a></td>
          <td>2026-08-04</td><td>Example Energy Inc.</td></tr>
    </tbody></table></body></html>
    """
    container_rows = parse_search_rows(container_html)
    assert [row.document_id for row in container_rows] == ["201", "202"]
    assert [row.kind for row in container_rows] == ["PDF Document", "Html Document"]
    assert parse_total(container_html) == 2
    assert expected_search_pages(0, page_size=200) == 1
    assert expected_search_pages(1, page_size=200) == 1
    assert expected_search_pages(401, page_size=200) == 3
    assert expected_search_pages(10_000, page_size=200, limit=450) == 3
    assert expected_search_pages(None, page_size=200) is None

    paper_only_html = """
    <div>Item(s) - 1 to 3 out of about 3</div><table><tbody>
      <tr><td><a href='/REGDOCS/Item/View/40940'>C40420-2 Secretary of State Name Change - A9V8T5 (Paper Only)</a></td>
          <td>2026-08-05</td><td>Boardwalk Continuum Marketing, LLC</td></tr>
      <tr><td><a href='/REGDOCS/Item/View/40940'>C40420-3 Conversion Documents - A9V8T6 (Paper Only)</a></td>
          <td>2026-08-05</td><td>Boardwalk Continuum Marketing, LLC</td></tr>
      <tr><td><a href='/REGDOCS/Item/View/40940'>C40420-4 Additional Evidence - A9V8T7 (Paper Only)</a></td>
          <td>2026-08-05</td><td>Boardwalk Continuum Marketing, LLC</td></tr>
    </tbody></table>
    """
    paper_rows = parse_search_rows(paper_only_html, container_id="4710509")
    assert [row.document_id for row in paper_rows] == [
        "paper:4710509:A9V8T5",
        "paper:4710509:A9V8T6",
        "paper:4710509:A9V8T7",
    ]
    assert all(row.regdocs_item_id == "40940" for row in paper_rows)
    assert all(row.kind == "Paper Only" and not row.is_file for row in paper_rows)
    assert explicit_empty_result("<div class='alert'>No items were found</div>")
    assert search_response_recognized("<div class='alert'>No items were found</div>")
    assert not explicit_empty_result("<table><tbody></tbody></table>")

    assert is_container_kind("Compound Document")
    assert is_container_kind("Folder")
    assert not is_container_kind("PDF Document")
    facet_html = """
    <label for='selectFilter1'>Document Type</label>
    <select id='selectFilter1'><option value='10'>Application</option></select>
    """
    assert parse_facet_catalog(facet_html) == {"Document Type": {"10": "Application"}}
    detail_html = """
    <html lang='en'><head><title>Canada Energy Regulator - REGDOCS - Example Application</title>
    <meta name='viewport' content='width=device-width,initial-scale=1'/></head>
    <body><h1>Add To Favourites</h1><h2>Example Application</h2>
    <dl><dt>Filing Number</dt><dd>C40283</dd></dl>
    <table><tr><th>Company</th><td>Example Energy Ltd.</td></tr></table></body></html>
    """
    detail = parse_detail_page(detail_html, "123", DETAIL_URL_TEMPLATE.format(item_id="123"))
    assert detail.title == "Example Application"
    assert detail.fields["Filing Number"] == ["C40283"]
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        db = PipelineDB(root / "test.sqlite3")
        try:
            tables = {row[0] for row in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            assert tables == {"documents", "runs", "errors", "raw_snapshots", "files"}
            run_id = db.start_run({"self_test": True})
            progress = ProgressMonitor(db=db, run_id=run_id, progress_file=root / "progress.json", database_path=db.path, persist_interval=0.0)
            record = rows[0]
            db.upsert_base_document(record, run_id=run_id, base_complete=True, start_date="2026-08-01", end_date="2026-08-06")
            db.conn.execute("UPDATE documents SET status='DOWNLOADED', download_status='SUCCEEDED', file_path='downloads/123.pdf', hash='abc' WHERE id='123'")
            db.conn.commit()
            db.upsert_base_document(record, run_id=run_id, base_complete=True, start_date="2026-08-01", end_date="2026-08-06")
            state = db.conn.execute("SELECT status, download_status, file_path, hash FROM documents WHERE id='123'").fetchone()
            assert tuple(state) == ("DOWNLOADED", "SUCCEEDED", "downloads/123.pdf", "abc")
            for member in container_rows:
                db.upsert_base_document(
                    member, run_id=run_id, base_complete=True,
                    start_date="2026-08-01", end_date="2026-08-06",
                    discovery_source="container", container_id="200", container_kind="Compound Document",
                )
            container_record = SearchRecord(
                document_id="200", name="C40396 Example Filing",
                url=DETAIL_URL_TEMPLATE.format(item_id="200"), is_file=False,
                date_raw="2026-08-04", submitter="Example Energy Inc.",
                kind="Compound Document", filing_number="C40396",
            )
            db.upsert_base_document(container_record, run_id=run_id, base_complete=True, start_date="2026-08-01", end_date="2026-08-06")
            db.apply_container_membership(
                container=container_record, members=container_rows, run_id=run_id,
                snapshot_ids=[], result_endpoint="https://apps.cer-rec.gc.ca/REGDOCS/Item/LoadResult/200",
                reported_total=2, complete=True,
            )
            parent_metadata = parse_json_object(db.conn.execute("SELECT metadata FROM documents WHERE id='200'").fetchone()[0])
            child_metadata = parse_json_object(db.conn.execute("SELECT metadata FROM documents WHERE id='201'").fetchone()[0])
            assert parent_metadata["container"]["member_ids"] == ["201", "202"]
            assert child_metadata["container_memberships"][0]["container_id"] == "200"
            db.apply_container_membership(
                container=container_record, members=container_rows, run_id=run_id,
                snapshot_ids=[], result_endpoint="https://apps.cer-rec.gc.ca/REGDOCS/Item/LoadResult/200",
                reported_total=2, complete=True,
            )
            child_metadata = parse_json_object(db.conn.execute("SELECT metadata FROM documents WHERE id='201'").fetchone()[0])
            assert len(child_metadata["container_memberships"]) == 1

            paper_container = SearchRecord(
                document_id="4710509", name="C40420 Paper Filing",
                url=DETAIL_URL_TEMPLATE.format(item_id="4710509"), is_file=False,
                date_raw="2026-08-05", submitter="Boardwalk Continuum Marketing, LLC",
                kind="Compound Document", filing_number="C40420",
            )
            db.upsert_base_document(
                paper_container, run_id=run_id, base_complete=True,
                start_date="2026-08-01", end_date="2026-08-06",
            )
            for member in paper_rows:
                db.upsert_base_document(
                    member, run_id=run_id, base_complete=True,
                    start_date="2026-08-01", end_date="2026-08-06",
                    discovery_source="container", container_id="4710509",
                    container_kind="Compound Document", mark_running=False,
                )
            db.apply_container_membership(
                container=paper_container, members=paper_rows, run_id=run_id,
                snapshot_ids=[],
                result_endpoint="https://apps.cer-rec.gc.ca/REGDOCS/Item/LoadResult/4710509",
                reported_total=3, complete=True,
            )
            paper_parent = parse_json_object(
                db.conn.execute("SELECT metadata FROM documents WHERE id='4710509'").fetchone()[0]
            )
            assert len(paper_parent["container"]["member_ids"]) == 3
            assert paper_parent["container"]["membership_state"] == "COMPLETE"
            paper_state = db.conn.execute(
                "SELECT detail_status, download_status, scout_status FROM documents WHERE id=?",
                ("paper:4710509:A9V8T5",),
            ).fetchone()
            assert tuple(paper_state) == ("NOT_APPLICABLE", "NOT_APPLICABLE", "SUCCEEDED")

            empty_folder = SearchRecord(
                document_id="300", name="Empty Folder",
                url=DETAIL_URL_TEMPLATE.format(item_id="300"), is_file=False,
                date_raw="2026-08-05", submitter="Example", kind="Folder",
            )
            db.upsert_base_document(
                empty_folder, run_id=run_id, base_complete=True,
                start_date="2026-08-01", end_date="2026-08-06",
            )
            db.apply_container_membership(
                container=empty_folder, members=[], run_id=run_id, snapshot_ids=[],
                result_endpoint="https://apps.cer-rec.gc.ca/REGDOCS/Item/LoadResult/300",
                reported_total=0, complete=True,
            )
            empty_metadata = parse_json_object(
                db.conn.execute("SELECT metadata FROM documents WHERE id='300'").fetchone()[0]
            )
            assert empty_metadata["container"]["membership_state"] == "EMPTY"
            assert {record.document_id for record in db.load_container_records()} >= {"200", "300", "4710509"}
            audit_ok, audit_report = audit_database(
                db.path, verify_raw=False, fail_on_unresolved=False
            )
            assert audit_ok, audit_report
            assert audit_report["membership_mismatches"] == 0
            assert audit_report["containers_total"] == 3
            db.finalize_container_repairs(["300", "4710509"], run_id=run_id)

            db.finalize_documents(
                ["200"], run_id=run_id, base_complete=True,
                containers_enabled=True, facets_enabled=False,
                facet_categories_complete={}, details_enabled=False,
            )
            assert db.conn.execute("SELECT scout_status FROM documents WHERE id='200'").fetchone()[0] == "SUCCEEDED"
            db.apply_facet_category(document_ids=["123"], category="Document Type", matches={"123": [("10", "Application")]}, run_id=run_id, category_complete=True)
            db.apply_detail(document_id="123", data=detail, normalized={"filing_number": "C40283", "company": "Example Energy Ltd."}, snapshot_id=None, run_id=run_id, fallback_title=record.name)
            db.finalize_documents(
                ["123"], run_id=run_id, base_complete=True,
                containers_enabled=True, facets_enabled=True,
                facet_categories_complete={"Document Type": True}, details_enabled=True,
            )
            metadata = parse_json_object(db.conn.execute("SELECT metadata FROM documents WHERE id='123'").fetchone()[0])
            assert metadata["document_types"] == ["Application"]
            assert metadata["regdocs_detail_fields"]["Company"] == ["Example Energy Ltd."]
            assert db.conn.execute("SELECT scout_status FROM documents WHERE id='123'").fetchone()[0] == "SUCCEEDED"
            # A later date search must preserve previously successful facet/detail metadata.
            db.upsert_base_document(record, run_id=run_id, base_complete=True, start_date="2026-08-01", end_date="2026-08-06")
            metadata_after_repeat = parse_json_object(
                db.conn.execute("SELECT metadata FROM documents WHERE id='123'").fetchone()[0]
            )
            assert metadata_after_repeat["document_types"] == ["Application"]
            assert metadata_after_repeat["regdocs_detail_fields"]["Company"] == ["Example Energy Ltd."]
            stored = RawStore(root / "raw").save("detail", b"<html>ok</html>")
            snapshot_id = db.save_snapshot(run_id=run_id, document_id="123", source_kind="detail", source_url="https://example/detail/123", final_url="https://example/detail/123", fetched_at=utc_now(), http_status=200, content_type="text/html", stored=stored, response_headers={})
            assert snapshot_id > 0
            db.add_error(run_id=run_id, document_id="123", stage="self_test", code="TEST_WARNING", severity="WARNING", message="test")
            progress.finish("SUCCEEDED")
            db.finish_run(run_id, "SUCCEEDED", {"self_test": True})
            assert (root / "progress.json").exists()
        finally:
            db.close()
    print("Self-test passed: five-table schema, container AJAX discovery, paper-only synthetic identity, explicit empty membership, bidirectional membership audit, metadata/status preservation, raw snapshots, errors, page-count progress, and progress persistence.")
    return 0


async def async_main(args: argparse.Namespace, *, db_path: Path, raw_dir: Path, progress_file: Path, log_file: Path) -> int:
    db = PipelineDB(db_path)
    if args.repair_containers:
        start_date = end_date = date.today().isoformat()
        date_mode = "repair_containers"
    else:
        start_date, end_date, date_mode = resolve_gold_dates(db, args.start_date, args.end_date)
    config = ScoutConfig(
        db_path=db_path, raw_dir=raw_dir, progress_file=progress_file,
        start_date=start_date, end_date=end_date, date_mode=date_mode,
        page_size=args.page_size,
        limit=None if args.repair_containers else args.limit,
        facets="none" if args.repair_containers else args.facets.strip(),
        expand_containers=args.expand_containers,
        repair_containers=args.repair_containers,
        container_max_depth=args.container_max_depth,
        container_max_items=args.container_max_items,
        include_details=args.include_details,
        detail_refresh_days=args.detail_refresh_days,
        refresh_details=args.refresh_details, concurrency=args.concurrency,
        min_delay=args.min_delay, max_delay=args.max_delay,
        max_retries=args.max_retries, retry_backoff=args.retry_backoff,
        dry_run=args.dry_run, verbose=args.verbose,
    )
    if not args.repair_containers and start_date > end_date:
        db.close()
        raise ValueError("--start-date must not be after --end-date")
    parameters = asdict(config)
    parameters.update(
        db_path=str(db_path), raw_dir=str(raw_dir), progress_file=str(progress_file),
        log_file=str(log_file), requested_start_date=args.start_date,
        requested_end_date=args.end_date,
    )
    run_id = db.start_run(parameters)
    progress = ProgressMonitor(
        db=db, run_id=run_id, progress_file=progress_file, database_path=db_path
    )
    started = time.monotonic()
    summary: dict[str, Any] = {}
    try:
        if config.repair_containers:
            logging.info(
                "Container repair profile: recursive=%s max_depth=%d max_items=%d details_from_shell=%s concurrency=%d delay=%.1f..%.1fs",
                config.expand_containers, config.container_max_depth,
                config.container_max_items, config.include_details,
                config.concurrency, config.min_delay, config.max_delay,
            )
        else:
            logging.info(
                "Gold profile: date_mode=%s range=%s..%s containers=%s facets=%s details=%s concurrency=%d delay=%.1f..%.1fs",
                date_mode, start_date, end_date, config.expand_containers, config.facets,
                "own-item" if config.include_details else "disabled",
                config.concurrency, config.min_delay, config.max_delay,
            )
        scout = DocumentScout(config, db, run_id, progress)
        summary.update(await scout.run())
        summary.update(db.summary_counts(run_id))
        post_run_audit_ok, post_run_audit = audit_database(
            db_path, verify_raw=True, container_snapshots_only=True,
            fail_on_unresolved=False,
        )
        summary.update(
            post_run_audit_ok=post_run_audit_ok,
            membership_mismatches=post_run_audit.get("membership_mismatches", 0),
            container_snapshots_verified=post_run_audit.get("container_snapshots_verified", 0),
            container_snapshot_references=post_run_audit.get("container_snapshot_references", 0),
            post_run_structural_failures=post_run_audit.get("structural_failures", 0),
        )
        # The live traversal counter and persisted relationship audit should agree.
        summary["container_nested_relationships_total"] = post_run_audit.get(
            "nested_container_relationships_total",
            summary.get("container_nested_relationships_total", 0),
        )
        summary.update(
            elapsed_seconds=round(time.monotonic() - started, 2), database=str(db_path),
            raw_directory=str(raw_dir), progress_file=str(progress_file), log_file=str(log_file),
            start_date=None if config.repair_containers else start_date,
            end_date=None if config.repair_containers else end_date,
            date_mode=date_mode,
            database_tables=["documents", "runs", "errors", "raw_snapshots", "files"],
        )
        if config.repair_containers:
            partial = (
                summary.get("container_failed", 0) > 0
                or summary.get("container_incomplete", 0) > 0
                or summary.get("container_max_depth_reached", 0) > 0
                or bool(summary.get("container_limit_reached"))
                or summary.get("error_severity_this_run", 0) > 0
                or not summary.get("post_run_audit_ok", True)
            )
        else:
            partial = (
                not summary.get("base_complete", True)
                or (config.expand_containers and (
                    summary.get("container_failed", 0) > 0
                    or summary.get("container_incomplete", 0) > 0
                    or summary.get("container_max_depth_reached", 0) > 0
                    or bool(summary.get("container_limit_reached"))
                ))
                or (config.facets != "none" and not summary.get("facet_catalog_ok", False))
                or summary.get("facet_categories_complete", 0) < summary.get("facet_categories", 0)
                or (config.include_details and summary.get("detail_failed", 0) > 0)
                or summary.get("error_severity_this_run", 0) > 0
                or not summary.get("post_run_audit_ok", True)
            )
        status = "PARTIAL" if partial else "SUCCEEDED"
        progress.finish(status)
        db.finish_run(run_id, status, summary)
        print(json_dumps({"run_id": run_id, "status": status, **summary}, pretty=True))
        return 0 if status == "SUCCEEDED" else 2
    except KeyboardInterrupt:
        summary.update(db.summary_counts(run_id))
        summary.update(interrupted=True, elapsed_seconds=round(time.monotonic() - started, 2))
        progress.finish("PARTIAL", message="Scout interrupted; committed work is preserved")
        db.finish_run(run_id, "PARTIAL", summary)
        print("\nScout interrupted. Committed metadata and raw snapshots are preserved.")
        return 130
    except Exception as exc:
        summary.update(db.summary_counts(run_id))
        summary.update(
            fatal_error=f"{type(exc).__name__}: {exc}",
            elapsed_seconds=round(time.monotonic() - started, 2),
        )
        db.add_error(
            run_id=run_id, stage="scout", code="FATAL_SCOUT_ERROR",
            severity="ERROR", message=summary["fatal_error"],
        )
        progress.finish("FAILED", message=summary["fatal_error"])
        db.finish_run(run_id, "FAILED", summary)
        raise
    finally:
        db.close()


def main() -> int:
    args = build_parser().parse_args()
    try:
        validate_args(args)
        db_path = Path(args.db).expanduser().resolve()
        raw_dir = resolve_relative_path(args.raw_dir, base=db_path.parent)
        progress_file = resolve_relative_path(args.progress_file, base=db_path.parent)
        log_file = resolve_relative_path(args.log_file, base=db_path.parent)
        if args.version:
            print(json_dumps(script_identity(), pretty=True))
            return 0
        if args.check_schema:
            ok, report = check_schema(db_path)
            print(json_dumps({"ok": ok, **report}, pretty=True))
            return 0 if ok else 2
        if args.audit:
            ok, report = audit_database(
                db_path, verify_raw=True, container_snapshots_only=False,
                fail_on_unresolved=True,
            )
            print(json_dumps(report, pretty=True))
            return 0 if ok else 2
        if args.show_defaults:
            print(json_dumps(default_profile(), pretty=True))
            return 0
        if args.status or args.status_json:
            state = read_status(db_path, progress_file)
            print(json_dumps(state, pretty=True) if args.status_json else format_status_text(state))
            return 0
        if args.self_test:
            configure_logging(args.verbose)
            return run_self_test()
        configure_logging(args.verbose, log_file)
        identity = script_identity()
        logging.info(
            "Scout %s | script=%s | sha256=%s | database=%s",
            identity["script_version"], identity["script"], identity["sha256"][:12], db_path,
        )
        with StageLock(db_path.with_suffix(db_path.suffix + ".scout.lock"), force=args.force_lock):
            return asyncio.run(async_main(args, db_path=db_path, raw_dir=raw_dir, progress_file=progress_file, log_file=log_file))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

