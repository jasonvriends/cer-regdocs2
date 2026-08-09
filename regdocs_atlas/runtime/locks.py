"""PID-aware process locks shared by long-running pipeline supervisors."""

from __future__ import annotations

import contextlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..version import release_version


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_lock_pid(path: Path) -> int | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    pid = payload.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return None
    return pid


def pid_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True


class ProcessLock:
    def __init__(
        self,
        path: Path,
        *,
        role: str,
        component_version: str | None = None,
        force: bool = False,
    ):
        self.path = path
        self.role = role
        self.component_version = component_version
        self.force = force
        self.owned = False

    def __enter__(self) -> "ProcessLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.force and self.path.exists():
            self.path.unlink()
        elif self.path.exists():
            pid = read_lock_pid(self.path)
            if pid is not None and not pid_is_running(pid):
                with contextlib.suppress(FileNotFoundError):
                    self.path.unlink()
                print(
                    f"Removing stale lock: {self.path} (PID {pid} is not running).",
                    file=sys.stderr,
                )

        payload: dict[str, Any] = {
            "pid": os.getpid(),
            "created_at": utcnow(),
            "role": self.role,
            "release_version": release_version(),
        }
        if self.component_version:
            payload["component_version"] = self.component_version

        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            detail = ""
            with contextlib.suppress(OSError):
                detail = self.path.read_text(encoding="utf-8")
            raise RuntimeError(
                f"Lock already exists: {self.path}. Confirm the owning process is not running "
                "before forcing lock removal."
                + (f"\nLock contents: {detail}" if detail else "")
            ) from exc

        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
        self.owned = True
        return self

    def __exit__(self, *_: Any) -> None:
        if self.owned:
            with contextlib.suppress(FileNotFoundError):
                self.path.unlink()
        self.owned = False
