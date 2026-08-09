"""Consistent human-facing presentation for REGDOCS Atlas commands."""

from __future__ import annotations

from typing import Any

from ..version import release_version


def progress_width(total: int | None) -> int:
    return max(3, len(str(max(int(total or 0), 0))))


def format_progress(completed: int | None, total: int | None) -> str:
    done = max(int(completed or 0), 0)
    count = max(int(total or 0), 0)
    width = progress_width(count)
    return f"{done:0{width}d}/{count:0{width}d}"


def stage_label(stage: str, provider: str | None = None) -> str:
    value = stage.strip().upper().replace("_", "/")
    if provider:
        provider_value = provider.strip().upper().replace("_", "/")
        if provider_value not in value:
            value = f"{value}/{provider_value}"
    return value


def banner(stage: str, *, provider: str | None = None, log_path: str | None = None) -> str:
    label = stage_label(stage, provider)
    lines = [
        f"REGDOCS ATLAS {release_version()}",
        f"MODE  {label}",
    ]
    if log_path:
        lines.append(f"LOG   {log_path}")
    return "\n".join(lines)


def run_line(run: dict[str, Any]) -> str:
    stage = str(run.get("stage") or "unknown")
    provider = run.get("provider")
    label = stage_label(stage, str(provider) if provider else None)
    progress = format_progress(run.get("completed_units"), run.get("total_units"))
    status = str(run.get("status") or "UNKNOWN")
    return f"RUN {int(run.get('id') or 0):04d}  {label:<18} {status:<22} {progress}"
