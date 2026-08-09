"""Facet-aware Scout console parsing.

Scout's facet progress uses descriptions such as ``Facet: Application Type``.
Those descriptions contain an internal colon, and a tqdm redraw may be followed
immediately by a logging record before the child process emits a newline.  This
module extends the shared console adapter so the operator dashboard can consume
both forms without changing Scout's crawl, pacing, or persistence behavior.
"""

from __future__ import annotations

import re

from . import console as _console


_TQDM_RE = re.compile(
    r"^(?P<label>.+):\s*"
    r"(?P<pct>\d+)%\|.*\|\s*"
    r"(?P<done>[\d,]+)/(?P<total>[\d,]+)"
    r"(?:\s|$)"
)

_EMBEDDED_LOG_START_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\s+"
    r"\[(?:DEBUG|INFO|WARNING|ERROR|CRITICAL)\]\s+"
)


class FacetAwareScoutDashboard(_console.ScoutDashboard):
    """Track per-category tqdm bars as one overall Scout FACETS counter."""

    def __init__(self) -> None:
        super().__init__()
        self._facet_label: str | None = None
        self._facet_base_done = 0
        self._facet_sub_done = 0

    def consume_tqdm(self, value: str) -> bool:
        text = _console._without_ansi(value).strip()
        match = _TQDM_RE.match(text)
        if not match:
            return False

        label = match.group("label").strip()
        done = _console._integer(match.group("done"))
        total = _console._integer(match.group("total"))

        if label.casefold().startswith("facet:"):
            self.active = True
            current = self.counts.get("FACETS")
            overall_total = current[1] if current is not None else None

            if label != self._facet_label:
                # The previous category's displayed overall count is exactly the
                # offset for the next category.  The new tqdm bar starts at zero.
                if current is not None:
                    self._facet_base_done = current[0]
                self._facet_label = label

            self._facet_sub_done = done
            overall_done = self._facet_base_done + done
            if overall_total is not None:
                overall_done = min(overall_done, overall_total)
            self._set("FACETS", overall_done, overall_total)
            return True

        return super().consume_tqdm(value)

    def consume_progress(self, message: str) -> bool:
        match = _console._PROGRESS_RE.match(message)
        if match is None:
            match = _console._PROGRESS_OPEN_RE.match(message)

        consumed = super().consume_progress(message)
        if not consumed or match is None:
            return consumed

        phase = match.group("phase")
        if self._bucket(phase) != "FACETS":
            return consumed

        # ProgressMonitor is the authoritative overall counter.  Re-anchor the
        # current category offset whenever one of its heartbeats arrives so the
        # per-category tqdm accumulation cannot drift.
        authoritative_done = _console._integer(match.group("done"))
        if self._facet_label is None:
            self._facet_base_done = authoritative_done
        else:
            self._facet_base_done = max(
                0, authoritative_done - self._facet_sub_done
            )
        return True


class EmbeddedLogStageConsoleStream(_console.StageConsoleStream):
    """Separate a logger record appended directly after a tqdm redraw."""

    def _emit(self, value: str) -> None:
        raw = _console._without_ansi(value)
        text = raw.strip()
        embedded = _EMBEDDED_LOG_START_RE.search(text)
        if embedded is not None and embedded.start() > 0:
            prefix = text[: embedded.start()].rstrip()
            suffix = text[embedded.start() :].lstrip()
            if prefix:
                super()._emit(prefix)
            if suffix:
                super()._emit(suffix)
            return
        super()._emit(value)


def install() -> None:
    """Install the facet-aware parser into the shared console adapter."""
    _console._TQDM_RE = _TQDM_RE
    _console.ScoutDashboard = FacetAwareScoutDashboard
    _console.StageConsoleStream = EmbeddedLogStageConsoleStream
