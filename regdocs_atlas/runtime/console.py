"""Normalize noisy child-stage console output into durable line-oriented status.

The root CLI owns stage stdout/stderr. Stage implementations may use logging or
``tqdm`` internally, but operators should see one stable console style. Raw child
output is still written by ``regdocs_atlas.cli`` to ``workspace/pipeline.log``
before this adapter sees it.
"""

from __future__ import annotations

import re
import sys
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
_DOWNLOADED_RE = re.compile(
    r"^Downloaded\s+(?P<document>\S+)\s+->\s+(?P<file>\S+)\s+"
    r"\((?P<mime>.+),\s*(?P<bytes>[\d,]+)\s+bytes\)$"
)
_FAILED_DOWNLOAD_RE = re.compile(r"^Failed\s+(?P<document>\S+):\s*(?P<message>.*)$")
_RETRY_DOWNLOAD_RE = re.compile(
    r"^Retrying\s+(?P<document>\S+)\s+after\s+(?P<seconds>[\d.]+)s:\s*(?P<message>.*)$"
)


def _clean(value: str) -> str:
    return _ANSI_RE.sub("", value).strip()


def _looks_like_progress_bar(value: str) -> bool:
    text = _clean(value)
    if not text:
        return True
    # tqdm's standard bar contains both a percentage separator and timing/rate
    # brackets. Keep this intentionally structural rather than tied to one label.
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


def _format_counter(done: str, total: str) -> str:
    done_value = int(done.replace(",", ""))
    total_value = int(total.replace(",", ""))
    width = max(3, len(str(max(total_value, 0))))
    return f"[{done_value:0{width}d}/{total_value:0{width}d}]"


def _format_progress(message: str) -> str | None:
    match = _PROGRESS_RE.match(message)
    if not match:
        return None
    phase = match.group("phase").replace("_", " ").upper()
    counter = _format_counter(match.group("done"), match.group("total"))
    details = (
        f"requests={match.group('requests')} attempts={match.group('attempts')} "
        f"retries={match.group('retries')} failures={match.group('failures')}"
    )
    eta = match.group("eta")
    if eta is not None:
        details += f" ETA={eta}s"
    return f"{counter} {phase:<20} {match.group('message')}  {details}"


def normalize_console_line(value: str) -> str | None:
    """Return the operator-facing representation of one logical child line."""
    text = _clean(value)
    if not text or _looks_like_progress_bar(text):
        return None

    log_match = _LOG_RE.match(text)
    if not log_match:
        return text

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
    """Text stream that removes transient bars and emits stable logical lines."""

    def __init__(self, stream: TextIO):
        self.stream = stream
        self._buffer = ""

    def write(self, text: str) -> int:
        if not text:
            return 0
        self._buffer += text
        self._drain()
        return len(text)

    def _drain(self) -> None:
        while True:
            newline = self._buffer.find("\n")
            carriage = self._buffer.find("\r")
            positions = [position for position in (newline, carriage) if position >= 0]
            if not positions:
                return
            position = min(positions)
            separator = self._buffer[position]
            piece = self._buffer[:position]
            self._buffer = self._buffer[position + 1 :]
            if separator == "\r":
                # Carriage-return records are transient terminal redraws. If a
                # stage ever emits a non-progress CR record, keep it rather than
                # silently losing diagnostics.
                if not _looks_like_progress_bar(piece):
                    self._emit(piece)
                continue
            self._emit(piece)

    def _emit(self, value: str) -> None:
        normalized = normalize_console_line(value)
        if normalized is None:
            return
        self.stream.write(normalized + "\n")
        self.stream.flush()

    def flush(self) -> None:
        # Do not emit a partial line here. Azure/Docling intentionally print
        # ``[n/N] id ... `` and flush before appending the terminal result.
        self.stream.flush()

    def finish(self) -> None:
        if self._buffer:
            self._emit(self._buffer)
            self._buffer = ""
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
