#!/usr/bin/env python3
"""Unified REGDOCS Atlas command entry point."""

from __future__ import annotations

import sys


def _main() -> int:
    if len(sys.argv) >= 3 and sys.argv[1:3] == ["rebuild", "prepare"]:
        from regdocs_atlas.rebuild_prepare_cli import main as rebuild_prepare_main
        return rebuild_prepare_main(sys.argv[3:])
    if len(sys.argv) >= 3 and sys.argv[1:3] == ["rebuild", "create"]:
        from regdocs_atlas.rebuild_create_cli import main as rebuild_create_main
        return rebuild_create_main(sys.argv[3:])
    if len(sys.argv) >= 4 and sys.argv[1:3] == ["recover", "scout"] and "--execute" in sys.argv[3:]:
        from regdocs_atlas.scout_recovery_cli import main as recovery_main
        return recovery_main(sys.argv[3:])
    from regdocs_atlas.cli import main
    return main()


if __name__ == "__main__":
    raise SystemExit(_main())
