"""Normalize noisy child-stage console output into stable terminal status.

The root CLI owns stage stdout/stderr. Stage implementations may use logging or
``tqdm`` internally, but operators should see one consistent terminal style.
Raw child output is still written by ``regdocs_atlas.cli`` to
``workspace/pipeline.log`` before this adapter sees it.
"""

from __future__ import annotations

import re
import sys
import threading
from contextlib import contextmanager, redirect_stdout
from typing import Iterator, TextIO

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_LOG_RE = re.compile(
    r"^\s*\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\s+"
    r"\[(?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL)\]\s+(?P<message>.*)$"
)
_PROGRESS_RE = re.compile(
    r"^Progress \[(?P<phase>[^\]]+)\]:\s*(?P<message>.*?);\s*"
    r"(?P<done>[\d,]+)/(?P<total>[\d,]+)\s*\((?P<pct>[\d.]+)%\);\s*"
    r"requests=(?P<requests>[\d,]+)\s+attempts=(?P<attempts>[\d,]+)\s+"
    r"retries=(?P<retries>[\d,]+)\s+failures=(?P<failures>[\d,]+)"
    r"(?:,\s*ETA\s+(?P<eta>[\d.]+)s)?$"
)
_PROGRESS_OPEN_RE = re.compile(
    r"^Progress \[(?P<phase>[^\]]+)\]:\s*(?P<message>.*?);\s*"
    r"(?P<done>[\d,]+)\s+completed;\s*"
    r"requests=(?P<requests>[\d,]+)\s+attempts=(?P<attempts>[\d,]+)\s+"
    r"retries=(?P<retries>[\d,]+)\s+failures=(?P<failures>[\d,]+)"
    r"(?:,\s*ETA\s+(?P<eta>[\d.]+)s)?$"
)
_TQDM_RE = re.compile(
    r"^(?P<label>[^:\r\n]+):\s*"
    r"(?P<pct>\d+)%\|.*\|\s*"
    r"(?P<done>[\d,]+)/(?P<total>[\d,]+)"
    r"(?:\s|$)"
)
_DOWNLOADED_RE = re.compile(
    r"^Downloaded\s+(?P<document>\S+)\s+->\s+(?P<file>\S+)\s+"
    r"\((?P<mime>.+),\s*(?P<bytes>[\d,]+)\s+bytes\)$"
)
_FAILED_DOWNLOAD_RE = re.compile(r"^Failed\s+(?P<document>\S+):\s*(?P<message>.*)$")
_RETRY_DOWNLOAD_RE = re.compile(
    r"^Retrying\s+(?P<document>\S+)\s+after\s+(?P<seconds>[\d.]+)s:\s*(?P<message>.*)$"
)
_NORMALIZE_CONCURRENCY_RE = re.compile(r"^Concurrency:\s+(?P<count>\d+)\s+isolated child")
_NORMALIZE_LAUNCH_RE = re.compile(
    r"^\[(?P<index>\d+)/(?P<total>\d+)\]\s+(?P<document>\S+)(?:\s+.*)?$"
)
_NORMALIZE_OK_RE = re.compile(
    r"^\s*OK\s+(?P<document>\S+)\s+pages=(?P<pages>\d+)\s+"
    r"chunks=(?P<chunks>\d+)\s+tables=(?P<tables>\d+)\s+elapsed=(?P<elapsed>[\d.]+)s$"
)
_NORMALIZE_FAILED_RE = re.compile(
    r"^\s*FAILED\s+(?P<document>\S+)\s+(?P<message>.*)$"
)
_NORMALIZE_FINAL_RE = re.compile(
    r"^Run\s+\d+\s+(?P<status>SUCCEEDED|COMPLETED_WITH_ERRORS|FAILED|INTERRUPTED):"
)
_INDEX_VALIDATED_RE = re.compile(
    r"^Validated\s+(?P<selected>[\d,]+)\s+selected chunk\(s\) from\s+"
    r"(?P<documents>[\d,]+)\s+REGDOCS document\(s\);\s+"
    r"(?P<scanned>[\d,]+)\s+total normalized chunk/provenance pairs checked\.$"
)
_INDEX_READY_RE = re.compile(r"^(?:created\s+\S+|using existing\s+\S+)$")
_INDEX_UPLOAD_RE = re.compile(
    r"^Uploaded batch\s+(?P<batch>\d+):\s+(?P<count>[\d,]+)\s+chunks\s+"
    r"\((?P<uploaded>[\d,]+)/(?P<total>[\d,]+)\)$"
)
_INDEX_FINAL_RE = re.compile(
    r"^Indexed\s+(?P<uploaded>[\d,]+)\s+chunk\(s\) into\s+.+\s+in\s+"
    r"(?P<batches>[\d,]+)\s+batch\(es\)\.$"
)
_INDEX_REJECTED_RE = re.compile(r"Azure rejected\s+(?P<count>[\d,]+)\s+document\(s\)")

_SCOUT_PHASE_ORDER = ("BASE", "CONTAINERS", "FACETS", "DETAILS")


def _without_ansi(value: str) -> str:
    return _ANSI_RE.sub("", value).rstrip()


def _looks_like_progress_bar(value: str) -> bool:
    text = _without_ansi(value).strip()
    if not text:
        return True
    if "%|" in text and "[" in text and "]" in text:
        return True
    if any(
        text.startswith(prefix)
        for prefix in (
            "REGDOCS containers:",
            "Downloading:",
            "Reconciling:",
            "Saving base metadata:",
            "Detail pages:",
        )
    ):
        return True
    return False


def _integer(value: str) -> int:
    return int(value.replace(",", ""))


def _format_counter(done: int, total: int | None, *, minimum_width: int = 2) -> str:
    if total is None:
        width = max(minimum_width, len(str(max(done, 0))))
        return f"[{done:0{width}d}/{'?' * width}]"
    width = max(minimum_width, len(str(max(done, 0))), len(str(max(total, 0))))
    return f"[{done:0{width}d}/{total:0{width}d}]"


def _format_scalar(value: int, *, minimum_width: int = 2) -> str:
    width = max(minimum_width, len(str(max(value, 0))))
    return f"[{value:0{width}d}]"


def _format_progress(message: str) -> str | None:
    match = _PROGRESS_RE.match(message)
    if not match:
        return None
    phase = match.group("phase").replace("_", " ").upper()
    counter = _format_counter(
        _integer(match.group("done")),
        _integer(match.group("total")),
        minimum_width=3,
    )
    details = (
        f"requests={match.group('requests')} attempts={match.group('attempts')} "
        f"retries={match.group('retries')} failures={match.group('failures')}"
    )
    eta = match.group("eta")
    if eta is not None:
        details += f" ETA={eta}s"
    return f"{counter} {phase:<20} {match.group('message')}  {details}"


class ScoutDashboard:
    """Compact in-place Scout phase counters fed by existing child output."""

    def __init__(self) -> None:
        self.active = False
        self.terminal_status: str | None = None
        self.counts: dict[str, tuple[int, int | None] | None] = {
            phase: None for phase in _SCOUT_PHASE_ORDER
        }

    @staticmethod
    def _bucket(value: str) -> str | None:
        text = value.strip().casefold().replace("-", "_").replace(" ", "_")
        if "container" in text or "compound" in text:
            return "CONTAINERS"
        if "facet" in text:
            return "FACETS"
        if "detail" in text:
            return "DETAILS"
        if "base" in text or "saving_base_metadata" in text:
            return "BASE"
        return None

    def _set(self, bucket: str, done: int, total: int | None) -> None:
        self.active = True
        self.counts[bucket] = (max(done, 0), None if total is None else max(total, 0))

    def consume_tqdm(self, value: str) -> bool:
        text = _without_ansi(value).strip()
        match = _TQDM_RE.match(text)
        if not match:
            return False
        bucket = self._bucket(match.group("label"))
        if bucket is None:
            return False
        self._set(bucket, _integer(match.group("done")), _integer(match.group("total")))
        return True

    def consume_progress(self, message: str) -> bool:
        match = _PROGRESS_RE.match(message)
        if match:
            self.active = True
            phase = match.group("phase")
            bucket = self._bucket(phase)
            if bucket is not None:
                self._set(
                    bucket,
                    _integer(match.group("done")),
                    _integer(match.group("total")),
                )
            self._consume_terminal_phase(phase)
            return True

        match = _PROGRESS_OPEN_RE.match(message)
        if not match:
            return False
        self.active = True
        phase = match.group("phase")
        bucket = self._bucket(phase)
        if bucket is not None:
            self._set(bucket, _integer(match.group("done")), None)
        self._consume_terminal_phase(phase)
        return True

    def _consume_terminal_phase(self, phase: str) -> None:
        normalized = phase.strip().upper()
        if normalized in {
            "SUCCEEDED",
            "FAILED",
            "PARTIAL",
            "INTERRUPTED",
            "COMPLETED_WITH_ERRORS",
        }:
            self.terminal_status = normalized

    def render(self) -> str:
        parts = ["SCOUT"]
        for phase in _SCOUT_PHASE_ORDER:
            value = self.counts[phase]
            counter = "[--/--]" if value is None else _format_counter(value[0], value[1])
            parts.append(f"{phase} {counter}")
        if self.terminal_status:
            parts.append(self.terminal_status)
        return "  ".join(parts)


class DownloadDashboard:
    """Compact Stage 2 reconciliation/download counters."""

    def __init__(self) -> None:
        self.active = False
        self.reconcile: tuple[int, int | None] | None = None
        self.files: tuple[int, int | None] | None = None
        self.ok = 0
        self.failed = 0
        self.retries = 0
        self.terminal_status: str | None = None

    def consume_tqdm(self, value: str) -> bool:
        text = _without_ansi(value).strip()
        match = _TQDM_RE.match(text)
        if not match:
            return False
        label = match.group("label").strip().casefold()
        done = _integer(match.group("done"))
        total = _integer(match.group("total"))
        if label == "reconciling":
            self.active = True
            self.reconcile = (done, total)
            return True
        if label == "downloading":
            self.active = True
            self.files = (done, total)
            downloaded = re.search(r"downloaded=(\d+)", text)
            failed = re.search(r"failed=(\d+)", text)
            if downloaded:
                self.ok = max(self.ok, int(downloaded.group(1)))
            if failed:
                self.failed = max(self.failed, int(failed.group(1)))
            return True
        return False

    def consume_log(self, level: str, message: str) -> tuple[bool, bool]:
        if _DOWNLOADED_RE.match(message):
            self.active = True
            self.ok += 1
            return True, False
        if _FAILED_DOWNLOAD_RE.match(message):
            self.active = True
            self.failed += 1
            return True, True
        if _RETRY_DOWNLOAD_RE.match(message):
            self.active = True
            self.retries += 1
            return True, True
        if self.active and level in {"DEBUG", "INFO"}:
            return True, False
        return False, False

    def render(self) -> str:
        reconcile = "[--/--]" if self.reconcile is None else _format_counter(*self.reconcile)
        files = "[--/--]" if self.files is None else _format_counter(*self.files)
        parts = [
            "DOWNLOAD",
            f"RECONCILE {reconcile}",
            f"FILES {files}",
            f"OK {_format_scalar(self.ok)}",
            f"FAILED {_format_scalar(self.failed)}",
            f"RETRIES {_format_scalar(self.retries)}",
        ]
        if self.terminal_status:
            parts.append(self.terminal_status)
        return "  ".join(parts)


class NormalizeDashboard:
    """Compact Stage 4 worker and merge counters."""

    def __init__(self) -> None:
        self.active = False
        self.total: int | None = None
        self.completed = 0
        self.ok = 0
        self.failed = 0
        self.concurrency: int | None = None
        self.merge: tuple[int, int | None] | None = None
        self.terminal_status: str | None = None

    def observe_header(self, text: str) -> None:
        match = _NORMALIZE_CONCURRENCY_RE.match(text)
        if match:
            self.concurrency = int(match.group("count"))

    def consume_line(self, text: str) -> tuple[bool, bool]:
        launch = _NORMALIZE_LAUNCH_RE.match(text)
        if launch:
            self.active = True
            self.total = int(launch.group("total"))
            return True, False

        succeeded = _NORMALIZE_OK_RE.match(text)
        if succeeded:
            self.active = True
            self.completed += 1
            self.ok += 1
            self._maybe_start_merge()
            return True, False

        failed = _NORMALIZE_FAILED_RE.match(text)
        if failed:
            self.active = True
            self.completed += 1
            self.failed += 1
            self._maybe_start_merge()
            return True, True

        final = _NORMALIZE_FINAL_RE.match(text)
        if final and self.active:
            self.terminal_status = final.group("status")
            if self.total is not None:
                self.completed = max(self.completed, self.total)
            self.merge = (5, 5)
            return True, True

        return False, False

    def _maybe_start_merge(self) -> None:
        if self.total is not None and self.completed >= self.total:
            self.merge = (0, 5)

    def render(self) -> str:
        workers = _format_counter(self.completed, self.total)
        merge = "[--/--]" if self.merge is None else _format_counter(*self.merge)
        concurrency = "[--]" if self.concurrency is None else _format_scalar(self.concurrency, minimum_width=1)
        parts = [
            "NORMALIZE",
            f"WORKERS {workers}",
            f"OK {_format_scalar(self.ok)}",
            f"FAILED {_format_scalar(self.failed)}",
            f"CONCURRENCY {concurrency}",
            f"MERGE {merge}",
        ]
        if self.terminal_status:
            parts.append(self.terminal_status)
        return "  ".join(parts)


class IndexDashboard:
    """Compact Stage 5 publish counters while leaving plan/query output normal."""

    def __init__(self) -> None:
        self.active = False
        self.scanned: tuple[int, int | None] | None = None
        self.chunks: tuple[int, int | None] | None = None
        self.batch: tuple[int, int | None] | None = None
        self.failed = 0
        self.terminal_status: str | None = None
        self._validated = False

    def observe_line(self, text: str) -> tuple[bool, bool]:
        validated = _INDEX_VALIDATED_RE.match(text)
        if validated:
            scanned = _integer(validated.group("scanned"))
            selected = _integer(validated.group("selected"))
            self.scanned = (scanned, scanned)
            self.chunks = (0, selected)
            self.batch = (0, None)
            self._validated = True
            # Do not activate yet: this same validation happens during `index plan`.
            return False, False

        if self._validated and _INDEX_READY_RE.match(text):
            self.active = True
            return True, False

        uploaded = _INDEX_UPLOAD_RE.match(text)
        if uploaded:
            self.active = True
            self.batch = (int(uploaded.group("batch")), None)
            self.chunks = (
                _integer(uploaded.group("uploaded")),
                _integer(uploaded.group("total")),
            )
            return True, False

        final = _INDEX_FINAL_RE.match(text)
        if final:
            self.active = True
            uploaded_count = _integer(final.group("uploaded"))
            batches = _integer(final.group("batches"))
            self.chunks = (uploaded_count, uploaded_count)
            self.batch = (batches, batches)
            self.terminal_status = "SUCCEEDED"
            return True, True

        rejected = _INDEX_REJECTED_RE.search(text)
        if rejected and self.active:
            self.failed += _integer(rejected.group("count"))
            self.terminal_status = "FAILED"
            return False, True

        return False, False

    def render(self) -> str:
        scan = "[--/--]" if self.scanned is None else _format_counter(*self.scanned)
        batches = "[--/--]" if self.batch is None else _format_counter(*self.batch)
        chunks = "[--/--]" if self.chunks is None else _format_counter(*self.chunks)
        parts = [
            "INDEX",
            f"SCAN {scan}",
            f"BATCHES {batches}",
            f"CHUNKS {chunks}",
            f"FAILED {_format_scalar(self.failed)}",
        ]
        if self.terminal_status:
            parts.append(self.terminal_status)
        return "  ".join(parts)


def normalize_console_line(value: str) -> str | None:
    """Return the normal operator-facing representation of one logical child line."""
    raw = _without_ansi(value)
    text = raw.strip()
    if not text or _looks_like_progress_bar(text):
        return None

    log_match = _LOG_RE.match(text)
    if not log_match:
        return raw

    level = log_match.group("level")
    message = log_match.group("message").strip()

    if level == "DEBUG":
        return None

    progress = _format_progress(message)
    if progress is not None:
        return progress

    downloaded = _DOWNLOADED_RE.match(message)
    if downloaded:
        return (
            f"DOWNLOAD  {downloaded.group('document')} SUCCEEDED  "
            f"file={downloaded.group('file')} mime={downloaded.group('mime')} "
            f"bytes={downloaded.group('bytes')}"
        )

    failed = _FAILED_DOWNLOAD_RE.match(message)
    if failed:
        return f"DOWNLOAD  {failed.group('document')} FAILED  {failed.group('message')}"

    retry = _RETRY_DOWNLOAD_RE.match(message)
    if retry:
        return (
            f"DOWNLOAD  {retry.group('document')} RETRY  "
            f"after={retry.group('seconds')}s {retry.group('message')}"
        )

    if level == "INFO":
        return f"INFO      {message}"
    if level == "WARNING":
        return f"WARNING   {message}"
    return f"ERROR     {message}"


class StageConsoleStream:
    """Text stream with permanent lines plus in-place high-volume dashboards."""

    def __init__(self, stream: TextIO):
        self.stream = stream
        self._buffers: dict[int, str] = {}
        self._lock = threading.Lock()
        self._mode: str | None = None
        self._scout = ScoutDashboard()
        self._download = DownloadDashboard()
        self._normalize = NormalizeDashboard()
        self._index = IndexDashboard()
        self._dashboard_visible = False
        self._dashboard_width = 0

    def write(self, text: str) -> int:
        if not text:
            return 0
        thread_id = threading.get_ident()
        with self._lock:
            self._buffers[thread_id] = self._buffers.get(thread_id, "") + text
            self._drain(thread_id)
        return len(text)

    def _drain(self, thread_id: int) -> None:
        buffer = self._buffers.get(thread_id, "")
        while True:
            newline = buffer.find("\n")
            carriage = buffer.find("\r")
            positions = [position for position in (newline, carriage) if position >= 0]
            if not positions:
                break
            position = min(positions)
            separator = buffer[position]
            piece = buffer[:position]
            buffer = buffer[position + 1 :]
            if separator == "\r":
                self._handle_carriage(piece)
            else:
                self._emit(piece)
        self._buffers[thread_id] = buffer

    def _handle_carriage(self, value: str) -> None:
        if self._mode == "SCOUT" and self._scout.consume_tqdm(value):
            self._draw_dashboard()
            return
        if self._mode == "DOWNLOAD" and self._download.consume_tqdm(value):
            self._draw_dashboard()
            return
        if _looks_like_progress_bar(value):
            return
        self._emit(value)

    def _emit(self, value: str) -> None:
        raw = _without_ansi(value)
        text = raw.strip()
        if not text:
            return

        if text.startswith("MODE  "):
            self._mode = text[6:].strip().upper()
            self._write_permanent(raw)
            return

        if self._mode == "SCOUT" and self._handle_scout(raw, text):
            return
        if self._mode == "DOWNLOAD" and self._handle_download(raw, text):
            return
        if self._mode == "NORMALIZE" and self._handle_normalize(raw, text):
            return
        if self._mode == "INDEX" and self._handle_index(raw, text):
            return

        normalized = normalize_console_line(raw)
        if normalized is None:
            return
        self._write_permanent(normalized)

    def _handle_scout(self, raw: str, text: str) -> bool:
        if self._scout.consume_tqdm(raw):
            self._draw_dashboard()
            return True

        log_match = _LOG_RE.match(text)
        if not log_match:
            return False
        level = log_match.group("level")
        message = log_match.group("message").strip()

        if level == "INFO" and message.startswith("Scout ") and "script=" in message:
            self._scout.active = True
            self._draw_dashboard()
            return True
        if self._scout.consume_progress(message):
            self._draw_dashboard()
            return True
        if self._scout.active and level in {"DEBUG", "INFO"}:
            return True
        return False

    def _handle_download(self, raw: str, text: str) -> bool:
        if self._download.consume_tqdm(raw):
            self._draw_dashboard()
            return True

        log_match = _LOG_RE.match(text)
        if not log_match:
            return False
        level = log_match.group("level")
        message = log_match.group("message").strip()
        consumed, permanent = self._download.consume_log(level, message)
        if not consumed:
            return False
        self._draw_dashboard()
        if permanent:
            normalized = normalize_console_line(raw)
            if normalized is not None:
                self._write_permanent(normalized)
        return True

    def _handle_normalize(self, raw: str, text: str) -> bool:
        self._normalize.observe_header(text)
        consumed, permanent = self._normalize.consume_line(text)
        if not consumed:
            return False
        self._draw_dashboard()
        if permanent:
            self._write_permanent(raw)
        return True

    def _handle_index(self, raw: str, text: str) -> bool:
        consumed, permanent = self._index.observe_line(text)
        if self._index.active:
            self._draw_dashboard()
        if permanent:
            self._write_permanent(raw)
            return True
        return consumed

    def _active_dashboard(self) -> ScoutDashboard | DownloadDashboard | NormalizeDashboard | IndexDashboard | None:
        if self._mode == "SCOUT" and self._scout.active:
            return self._scout
        if self._mode == "DOWNLOAD" and self._download.active:
            return self._download
        if self._mode == "NORMALIZE" and self._normalize.active:
            return self._normalize
        if self._mode == "INDEX" and self._index.active:
            return self._index
        return None

    def _clear_dashboard(self) -> None:
        if not self._dashboard_visible:
            return
        self.stream.write("\r" + (" " * self._dashboard_width) + "\r")
        self.stream.flush()
        self._dashboard_visible = False

    def _draw_dashboard(self) -> None:
        dashboard = self._active_dashboard()
        if dashboard is None:
            return
        line = dashboard.render()
        width = max(self._dashboard_width, len(line))
        self.stream.write("\r" + line.ljust(width))
        self.stream.flush()
        self._dashboard_width = width
        self._dashboard_visible = True

    def _write_permanent(self, value: str) -> None:
        redraw = self._dashboard_visible
        if redraw:
            self._clear_dashboard()
        self.stream.write(value + "\n")
        self.stream.flush()
        if redraw:
            self._draw_dashboard()

    def flush(self) -> None:
        # Do not emit partial lines here. Azure/Docling intentionally print
        # ``[n/N] id ... `` and flush before appending the terminal result.
        with self._lock:
            self.stream.flush()

    def finish(self) -> None:
        with self._lock:
            for thread_id, buffer in list(self._buffers.items()):
                if buffer:
                    self._emit(buffer)
                self._buffers[thread_id] = ""
            dashboard = self._active_dashboard()
            if dashboard is not None:
                line = dashboard.render()
                width = max(self._dashboard_width, len(line))
                self.stream.write("\r" + line.ljust(width) + "\n")
                self._dashboard_visible = False
                self._dashboard_width = 0
            self.stream.flush()

    def isatty(self) -> bool:
        return bool(getattr(self.stream, "isatty", lambda: False)())

    def fileno(self) -> int:
        return self.stream.fileno()

    @property
    def encoding(self) -> str | None:
        return getattr(self.stream, "encoding", None)


@contextmanager
def standardized_stage_console(stream: TextIO | None = None) -> Iterator[StageConsoleStream]:
    """Route root-CLI stage stdout through the standard operator presentation."""
    target = stream or sys.stdout
    adapter = StageConsoleStream(target)
    with redirect_stdout(adapter):
        try:
            yield adapter
        finally:
            adapter.finish()
