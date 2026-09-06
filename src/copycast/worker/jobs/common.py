"""Helpers shared by the job modules: option merging, error mapping, archive job enqueueing."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from copycast.adapters.db.models import CatalogItem, Job
from copycast.adapters.db.uow import UnitOfWork
from copycast.adapters.engine.options import subtitle_languages
from copycast.adapters.sources.http import (
    BodyTooLarge,
    NotAFeed,
    SourceError,
    SourceRejected,
    SourceUnreachable,
)
from copycast.application.events import JobEvent
from copycast.application.models import JobRead
from copycast.application.ports import EngineError, PermanentError, TransientError
from copycast.application.services.items import (
    PRIORITY_BACKFILL,
    PRIORITY_FOLLOW,
    PRIORITY_MANUAL,
    archive_dedup_key,
)
from copycast.domain.engine_options import EngineOptions
from copycast.domain.enums import ArchiveState, JobKind, JobTrigger, WantedReason
from copycast.settings import Settings


def engine_options_for(
    settings: Settings, feed_options: Mapping[str, Any] | None, *, language: str | None
) -> dict[str, Any]:
    """config ``[engine.options]`` -> Feed options, with the subtitle languages defaulted.

    ``BASE_OPTIONS`` are overlaid by the engine itself; a Feed's own
    ``subtitleslangs`` wins over the ``[feed language, en]`` default.
    """
    merged = EngineOptions.merge(settings.engine.options, feed_options, None)
    merged.setdefault("subtitleslangs", subtitle_languages(language))
    return merged


def source_error_to_engine(exc: SourceError) -> EngineError:
    """HTTP-level Source failures as the retry classes the runner understands."""
    if isinstance(exc, SourceUnreachable):
        return TransientError(str(exc))
    if isinstance(exc, SourceRejected | BodyTooLarge | NotAFeed):
        return PermanentError(str(exc))
    return TransientError(str(exc))


def wanted_priority(reason: str | None) -> int:
    """50 manual/request, 100 follow, 200 backfill."""
    if reason == WantedReason.backfill:
        return PRIORITY_BACKFILL
    if reason == WantedReason.follow:
        return PRIORITY_FOLLOW
    return PRIORITY_MANUAL


async def enqueue_archive_jobs(
    uow: UnitOfWork,
    items: Iterable[CatalogItem],
    *,
    trigger: JobTrigger,
    request_id: Any = None,
    priority: int | None = None,
) -> list[Job]:
    """One ``archive_item`` job per wanted item lacking an active one (dedup by item)."""
    created: list[Job] = []
    for item in items:
        if item.archive_state != ArchiveState.wanted:
            continue
        job = await uow.jobs.enqueue(
            JobKind.archive_item,
            trigger,
            feed_id=item.feed_id,
            item_id=item.id,
            request_id=request_id,
            priority=priority if priority is not None else wanted_priority(item.wanted_reason),
            dedup=archive_dedup_key(item.id),
        )
        if job is not None:
            created.append(job)
            await uow.publish(JobEvent(job=JobRead.model_validate(job)))
    return created


__all__ = [
    "engine_options_for",
    "enqueue_archive_jobs",
    "source_error_to_engine",
    "wanted_priority",
]
