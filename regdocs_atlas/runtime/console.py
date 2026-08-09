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
    """Text stream with durable lines plus an in-place Scout phase dashboard."""

    def __init__(self, stream: TextIO):
        self.stream = stream
        self._buffers: dict[int, str] = {}
        self._lock = threading.Lock()
        self._scout = ScoutDashboard()
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
        if self._scout.consume_tqdm(value):
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

        if self._scout.consume_tqdm(raw):
            self._draw_dashboard()
            return

        log_match = _LOG_RE.match(text)
        if log_match:
            level = log_match.group("level")
            message = log_match.group("message").strip()

            if level == "INFO" and message.startswith("Scout ") and "script=" in message:
                self._scout.active = True
                self._draw_dashboard()
                return

            if self._scout.consume_progress(message):
                self._draw_dashboard()
                return

            # Once Scout has declared its progress stream, routine INFO records
            # are implementation detail. Warnings/errors remain permanent lines,
            # and the full raw log remains in workspace/pipeline.log.
            if self._scout.active and level in {"DEBUG", "INFO"}:
                return

        normalized = normalize_console_line(raw)
        if normalized is None:
            return
        self._write_permanent(normalized)

    def _clear_dashboard(self) -> None:
        if not self._dashboard_visible:
            return
        self.stream.write("\r" + (" " * self._dashboard_width) + "\r")
        self.stream.flush()
        self._dashboard_visible = False

    def _draw_dashboard(self) -> None:
        if not self._scout.active:
            return
        line = self._scout.render()
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
            if self._scout.active:
                line = self._scout.render()
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
