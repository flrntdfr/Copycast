#!/usr/bin/env python3
"""Print the yt-dlp version pinned in uv.lock.

Standard library only, so it runs without the project environment (CI meta jobs,
the Makefile's ``image`` target, ``scripts/smoke.sh``).

    python3 scripts/engine_version.py            # uses ./uv.lock next to this repository
    python3 scripts/engine_version.py path/to/uv.lock
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path
from typing import cast

ENGINE_PACKAGE = "yt-dlp"
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOCK = REPO_ROOT / "uv.lock"


class LockError(Exception):
    """The lockfile is unreadable or does not pin the engine."""


def locked_version(lock_path: Path = DEFAULT_LOCK, package: str = ENGINE_PACKAGE) -> str:
    """The version of ``package`` recorded in the uv lockfile at ``lock_path``."""
    try:
        data = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise LockError(f"lockfile not found: {lock_path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise LockError(f"lockfile is not valid TOML: {lock_path}: {exc}") from exc
    packages: object = data.get("package", [])
    if not isinstance(packages, list):
        raise LockError(f"lockfile has no [[package]] table: {lock_path}")
    for raw in cast(list[object], packages):
        if not isinstance(raw, dict):
            continue
        entry = cast(dict[str, object], raw)
        if entry.get("name") == package:
            version = entry.get("version")
            if isinstance(version, str) and version:
                return version
            raise LockError(f"{package} has no version in {lock_path}")
    raise LockError(f"{package} is not pinned in {lock_path}")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) > 1 or any(arg in {"-h", "--help"} for arg in args):
        print((__doc__ or "").strip(), file=sys.stderr)
        return 2
    lock_path = Path(args[0]) if args else DEFAULT_LOCK
    try:
        print(locked_version(lock_path))
    except LockError as exc:
        print(f"engine_version: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
