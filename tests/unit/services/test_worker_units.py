"""Worker helpers without Postgres: backoff, log cap, progress throttle, option merging."""

from __future__ import annotations

import random
import uuid

import pytest

from copycast.adapters.sources.http import (
    BodyTooLarge,
    NotAFeed,
    SourceError,
    SourceRejected,
    SourceUnreachable,
)
from copycast.application.ports import PermanentError, Progress, TransientError
from copycast.domain.enums import EnginePhase, LogLevel, ProgressPhase, WantedReason
from copycast.settings import get_settings
from copycast.worker.constants import BACKOFF_MAX_SECONDS
from copycast.worker.joblog import JobLog
from copycast.worker.jobs.common import (
    engine_options_for,
    source_error_to_engine,
    wanted_priority,
)
from copycast.worker.jobs.expand_request import direct_listing
from copycast.worker.progress import ProgressTracker, to_job_progress
from copycast.worker.runner import backoff_seconds, psycopg_conninfo


def test_backoff_doubles_with_jitter_and_caps() -> None:
    rng = random.Random(1)
    first = backoff_seconds(1, rng=rng)
    assert 120 * 0.8 <= first <= 120 * 1.2
    assert backoff_seconds(30, rng=rng) <= BACKOFF_MAX_SECONDS * 1.2
    assert backoff_seconds(0, rng=rng) >= 60 * 0.8


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
    listing = direct_listing("https://x.example/dir/talk.mp3?x=1", "audio/mpeg")
    assert listing.service == "Direct" and listing.title == "talk.mp3"
    (item,) = listing.items
    assert item.source_key == "https://x.example/dir/talk.mp3?x=1"
    assert item.enclosure_type == "audio/mpeg" and item.archivable
