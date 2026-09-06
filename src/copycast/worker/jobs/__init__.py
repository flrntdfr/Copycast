"""Job modules and the context the runner hands them.

A handler is ``async def run(ctx: JobContext) -> JobOutcome``. It does its
own bookkeeping of Catalog and Request state (the runner only knows jobs) and
lets Engine errors escape so the runner can retry, fail or re-queue.
"""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Awaitable, Callable
from concurrent.futures import Executor
from dataclasses import dataclass, field
from typing import Any

from copycast.adapters.db.models import Job
from copycast.adapters.db.uow import UnitOfWork, uow_of
from copycast.app import Container
from copycast.application.ports import Cancelled, CancelToken
from copycast.domain.enums import JobKind
from copycast.worker.joblog import JobLog
from copycast.worker.progress import ProgressTracker


@dataclass(frozen=True, slots=True)
class JobOutcome:
    """What a successful handler reports back for ``jobs.result`` and ``jobs.engine_version``."""

    result: dict[str, Any] = field(default_factory=dict[str, Any])
    engine_version: str | None = None


@dataclass(slots=True)
class JobContext:
    """Everything one running job needs."""

    container: Container
    job: Job
    cancel: CancelToken
    log: JobLog
    progress: ProgressTracker
    executor: Executor
    stopping: Callable[[], bool]

    def uow(self) -> UnitOfWork:
        return uow_of(self.container.uow_factory())

    async def run_blocking[T](self, fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
        """Run ``fn`` on the worker's thread pool (Engine calls, file and network IO)."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self.executor, functools.partial(fn, *args, **kwargs))

    def check_cancelled(self, what: str) -> None:
        if self.cancel.cancelled:
            raise Cancelled(f"{what} cancelled")

    @property
    def exhausted(self) -> bool:
        """The claim that started this run was the last attempt allowed."""
        return self.job.attempt >= self.job.max_attempts


Handler = Callable[[JobContext], Awaitable[JobOutcome]]


def handler_for(kind: JobKind) -> Handler:
    from copycast.worker.jobs import archive_item, expand_request, prune, rebuild, refresh

    handlers: dict[JobKind, Handler] = {
        JobKind.refresh: refresh.run,
        JobKind.archive_item: archive_item.run,
        JobKind.expand_request: expand_request.run,
        JobKind.prune: prune.run,
        JobKind.rebuild: rebuild.run,
    }
    return handlers[kind]


__all__ = ["Handler", "JobContext", "JobOutcome", "handler_for"]
