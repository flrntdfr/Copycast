"""Atomic file writes: sibling temp file, fsync, ``os.replace``.

Every non-yt-dlp write in the data directory goes through here so a crash
never leaves a half-written descriptor, listing or Source XML behind.
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
from pathlib import Path
from typing import Any

TMP_SUFFIX = ".tmp"


def write_atomic(path: Path, data: bytes | str, *, mode: int = 0o644) -> Path:
    """Write ``data`` to ``path`` atomically and durably; returns ``path``."""
    payload = data.encode("utf-8") if isinstance(data, str) else data
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}{TMP_SUFFIX}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
        raise
    _fsync_dir(path.parent)
    return path


def write_json_atomic(path: Path, payload: Any, *, indent: int | None = 2) -> Path:
    text = json.dumps(payload, indent=indent, ensure_ascii=False, sort_keys=False, default=str)
    return write_atomic(path, text + "\n")


def _fsync_dir(directory: Path) -> None:
    """Best effort: some filesystems refuse to open a directory for fsync."""
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def is_temp_file(path: Path) -> bool:
    """A leftover from an interrupted :func:`write_atomic`."""
    return path.name.startswith(".") and path.name.endswith(TMP_SUFFIX)


__all__ = ["TMP_SUFFIX", "is_temp_file", "write_atomic", "write_json_atomic"]
