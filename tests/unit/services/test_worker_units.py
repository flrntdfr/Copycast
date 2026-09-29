"""Worker helpers without Postgres: backoff, live waits, log cap, progress throttle, option
merging, Request listings."""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from copycast.adapters.sources.http import (
    BodyTooLarge,
    NotAFeed,
    SourceError,
    SourceRejected,
    SourceUnreachable,
)
from copycast.application.ports import PermanentError, Progress, TransientError
from copycast.domain.enums import EnginePhase, LiveStatus, LogLevel, ProgressPhase, WantedReason
from copycast.settings import get_settings
from copycast.worker.constants import BACKOFF_MAX_SECONDS, LIVE_RETRY, LIVE_WAIT_MAX
from copycast.worker.joblog import JobLog
from copycast.worker.jobs.common import (
    engine_options_for,
    source_error_to_engine,
    wanted_priority,
)
from copycast.worker.jobs.expand_request import direct_listing, want_streams
from copycast.worker.progress import ProgressTracker, to_job_progress
from copycast.worker.runner import backoff_seconds, live_retry_delay, psycopg_conninfo
from tests.support.factories import listing, listing_item


def test_backoff_doubles_with_jitter_and_caps() -> None:
    rng = random.Random(1)
    first = backoff_seconds(1, rng=rng)
    assert 120 * 0.8 <= first <= 120 * 1.2
    assert backoff_seconds(30, rng=rng) <= BACKOFF_MAX_SECONDS * 1.2
    assert backoff_seconds(0, rng=rng) >= 60 * 0.8


def test_live_retry_delay_waits_the_interval_or_the_streams_start() -> None:
    now = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
    fresh = now - timedelta(minutes=1)
    assert live_retry_delay(None, created_at=fresh, now=now) == LIVE_RETRY
    assert live_retry_delay(timedelta(minutes=2), created_at=fresh, now=now) == LIVE_RETRY, (
        "an estimate shorter than LIVE_RETRY is not honoured"
    )
    assert live_retry_delay(timedelta(hours=3), created_at=fresh, now=now) == timedelta(hours=3)


def test_live_retry_delay_never_schedules_past_the_jobs_ceiling() -> None:
    """The budget is measured from the job's creation, not from now (review finding)."""
    now = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
    ten_hours_in = now - timedelta(hours=10)
    # A start 40 h away would land 50 h after creation: cut to the 38 h left.
    assert live_retry_delay(timedelta(hours=40), created_at=ten_hours_in, now=now) == (
        LIVE_WAIT_MAX - timedelta(hours=10)
    )
    # Even the plain interval is cut when less than that is left: the last try runs at
    # the ceiling, where the archive job fails it for good if still not published.
    nearly_over = now - LIVE_WAIT_MAX + timedelta(minutes=10)
    assert live_retry_delay(None, created_at=nearly_over, now=now) == timedelta(minutes=10)
    # Past the ceiling (the runner's clock ran ahead of the job's) the try is immediate.
    over = now - LIVE_WAIT_MAX - timedelta(seconds=1)
    assert live_retry_delay(timedelta(hours=1), created_at=over, now=now) == timedelta(0)


def test_psycopg_conninfo_drops_the_driver_suffix() -> None:
    assert (
        psycopg_conninfo("postgresql+psycopg://u:p@h:5432/db?sslmode=require")
        == "postgresql://u:p@h:5432/db?sslmode=require"
    )


def test_joblog_caps_lines_then_keeps_warnings_and_continues_the_sequence() -> None:
    log = JobLog(uuid.uuid4(), cap=3, start=10)
    for n in range(5):
        log.info(f"line {n}")
    log.debug("   ")
    log.warning("careful")
    log.error("bad")
    lines = log.drain()
    assert [seq for seq, *_ in lines] == [11, 12, 13, 14, 15]
    assert [level for _, _, level, _ in lines] == [
        LogLevel.info,
        LogLevel.info,
        LogLevel.info,
        LogLevel.warning,
        LogLevel.error,
    ]
    assert log.kept == 5 and log.dropped == 2
    assert log.drain() == []


def test_progress_tracker_throttles_flushes() -> None:
    clock = [100.0]
    tracker = ProgressTracker(item_id="abc", min_interval=0.5, clock=lambda: clock[0])
    assert tracker.take_due() is None
    tracker.on_progress(Progress(EnginePhase.downloading, bytes_done=10, bytes_total=100))
    first = tracker.take_due()
    assert first is not None and first.percent == 10.0 and first.item_id == "abc"
    tracker.on_progress(Progress(EnginePhase.downloading, bytes_done=20, bytes_total=100))
    assert tracker.take_due() is None, "within the throttle interval"
    clock[0] += 0.6
    second = tracker.take_due()
    assert second is not None and second.downloaded_bytes == 20
    tracker.set_phase(ProgressPhase.assets)
    final = tracker.take_final()
    assert final is not None and final.phase is ProgressPhase.assets
    assert final.downloaded_bytes == 20 and tracker.updates == 3
    assert tracker.take_due() is None


def test_to_job_progress_maps_finished_to_hundred_percent() -> None:
    done = to_job_progress(Progress(EnginePhase.finished, bytes_done=5, bytes_total=5))
    assert done.phase is ProgressPhase.postprocessing and done.percent == 100.0
    unknown = to_job_progress(Progress(EnginePhase.downloading, speed_bps=12.5, eta_s=3))
    assert unknown.percent is None and unknown.speed_bps == 12.5 and unknown.eta_seconds == 3


def test_engine_options_for_layers_config_and_feed() -> None:
    settings = get_settings(engine={"options": {"ratelimit": 100, "subtitleslangs": ["fr"]}})
    merged = engine_options_for(settings, {"ratelimit": 5}, language="de-CH")
    assert merged["ratelimit"] == 5 and merged["subtitleslangs"] == ["fr"]
    defaulted = engine_options_for(get_settings(), None, language="de-CH")
    assert defaulted["subtitleslangs"] == ["de-ch", "de", "en", "-live_chat"]


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (SourceUnreachable("down"), TransientError),
        (SourceRejected("u", 404), PermanentError),
        (BodyTooLarge("u", 1), PermanentError),
        (NotAFeed("nope"), PermanentError),
        (SourceError("other"), TransientError),
    ],
)
def test_source_error_mapping(exc: SourceError, kind: type[Exception]) -> None:
    assert isinstance(source_error_to_engine(exc), kind)


def test_wanted_priority() -> None:
    assert wanted_priority(WantedReason.backfill) == 200
    assert wanted_priority(WantedReason.follow) == 100
    assert wanted_priority(WantedReason.manual) == 50
    assert wanted_priority(None) == 50


def test_direct_listing_synthesizes_one_leaf() -> None:
    direct = direct_listing("https://x.example/dir/talk.mp3?x=1", "audio/mpeg")
    assert direct.service == "Direct" and direct.title == "talk.mp3"
    (item,) = direct.items
    assert item.source_key == "https://x.example/dir/talk.mp3?x=1"
    assert item.enclosure_type == "audio/mpeg" and item.archivable


def test_want_streams_makes_upcoming_and_live_streams_archivable_for_a_request() -> None:
    """A Request wants a stream whatever its status; the archive job waits for the
    recording, since an Inbox has no Refresh to flip ``archivable`` later."""
    scripted = listing(0, service="YouTube").model_copy(
        update={
            "items": [
                listing_item(
                    1, key="youtube:live", live_status=LiveStatus.is_live, archivable=False
                ),
                listing_item(
                    2, key="youtube:soon", live_status=LiveStatus.is_upcoming, archivable=False
                ),
                listing_item(3, key="youtube:done", live_status=LiveStatus.was_live),
                listing_item(4, key="youtube:plain"),
            ]
        }
    )
    wanted = want_streams(scripted)
    assert [item.archivable for item in wanted.items] == [True, True, True, True]
    assert [item.live_status for item in wanted.items] == [
        LiveStatus.is_live,
        LiveStatus.is_upcoming,
        LiveStatus.was_live,
        None,
    ], "the status is kept so the Catalog says why the item waits"
    assert wanted.title == scripted.title and wanted.service == scripted.service
    assert want_streams(wanted) is wanted, "nothing to do when everything is archivable"
    # Only a stream is flipped: an item that is not archivable for another reason stays so.
    other = scripted.model_copy(
        update={"items": [listing_item(5, key="k", archivable=False, live_status=None)]}
    )
    assert want_streams(other).items[0].archivable is False
