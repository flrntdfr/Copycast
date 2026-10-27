"""Scratch: the local partition the Engine downloads, transcodes and tags in.

Nothing in Scratch is ever served or kept: a job works in its own directory
(``{scratch_dir}/jobs/{name}/home`` for finished files, ``.../tmp`` for yt-dlp's
partials), uploads the finished files to the Object store and removes the directory.
A worker that starts sweeps whatever a crash left behind. The api uses Scratch for
the short-lived private copy of the cookie file a listing needs, and for an Upload
while its tags are read, before it goes to the Object store.
"""

from __future__ import annotations

import contextlib
import secrets
import shutil
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Final

JOBS_DIR: Final = "jobs"
TEMP_DIR: Final = "tmp"


@dataclass(frozen=True, slots=True)
class JobDir:
    """One job's private directories."""

    root: Path

    @property
    def home(self) -> Path:
        return self.root / "home"

    @property
    def temp(self) -> Path:
        return self.root / "tmp"


class Scratch:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def free_bytes(self) -> int | None:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            return shutil.disk_usage(self.root).free
        except OSError:
            return None

    @contextlib.contextmanager
    def job(self, name: str) -> Generator[JobDir]:
        """A fresh directory for one job, removed on exit whatever happened."""
        job = JobDir(self.root / JOBS_DIR / f"{name}.{secrets.token_hex(4)}")
        job.home.mkdir(parents=True, exist_ok=True)
        job.temp.mkdir(parents=True, exist_ok=True)
        try:
            yield job
        finally:
            shutil.rmtree(job.root, ignore_errors=True)

    def temp_file(self, suffix: str) -> Path:
        """A unique path under ``tmp/`` (the caller removes it)."""
        directory = self.root / TEMP_DIR
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{secrets.token_hex(8)}{suffix}"

    def sweep(self) -> int:
        """Remove every leftover job directory and temp file; returns how many entries went."""
        removed = 0
        for sub in (JOBS_DIR, TEMP_DIR):
            directory = self.root / sub
            if not directory.is_dir():
                continue
            for entry in directory.iterdir():
                if entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    entry.unlink(missing_ok=True)
                removed += 1
        return removed


__all__ = ["JobDir", "Scratch"]
