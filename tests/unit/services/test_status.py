"""System status: how each component turns what it observed into ok, warn, error or unknown."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from copycast.application.models import CrawlerStats, QueueStats, QuotaStatus, Totals
from copycast.application.ports import EngineVersion
from copycast.application.services import status
from copycast.version import APP_VERSION

NOW = datetime(2026, 10, 27, 12, tzinfo=UTC)
ENGINE = EngineVersion(
    name="yt-dlp",
    version="2026.10.20",
    channel="nightly",
    release_date=date(2026, 10, 20),
    ffmpeg_version="7.1",
)


def snapshot(seen: timedelta | None = timedelta(seconds=10), **worker: Any) -> Any:
    beat = (
        None
        if seen is None
        else SimpleNamespace(
            worker_id="w1",
            seen_at=NOW - seen,
            status={"app_version": APP_VERSION, "concurrency": 2, "running_jobs": 0, **worker},
        )
    )
    return status._Snapshot(heartbeat=beat, queue=QueueStats())  # pyright: ignore[reportPrivateUsage]


def test_worst_ignores_unknown() -> None:
    assert status.worst(["ok", "unknown"]) == "ok"
    assert status.worst(["ok", "warn", "unknown"]) == "warn"
    assert status.worst(["warn", "error"]) == "error"


def test_worker_states() -> None:
    worker = status._worker  # pyright: ignore[reportPrivateUsage]
    assert worker(snapshot(), NOW).summary == "Idle"
    assert worker(snapshot(running_jobs=2), NOW).summary == "Running 2 jobs"
    never = worker(snapshot(seen=None), NOW)
    assert never.state == "error" and "Never seen" in never.summary
    stale = worker(snapshot(seen=timedelta(minutes=5)), NOW)
    assert stale.state == "error" and stale.summary.startswith("Not seen for 5 min")
    full = worker(snapshot(storage_full_until=(NOW + timedelta(hours=1)).isoformat()), NOW)
    assert full.state == "error" and "Storage is full" in full.summary
    quarantined = snapshot(quarantined_feeds=["a", "b"])
    quarantined.quarantined = ["a", "b"]  # what _snapshot keeps of the heartbeat's list
    assert worker(quarantined, NOW).state == "warn"
    assert worker(snapshot(quarantined_feeds=["a"]), NOW).state == "ok", "none visible"
    assert worker(snapshot(app_version="0.0.1"), NOW).state == "warn"
    down = status._Snapshot(error="connection refused")  # pyright: ignore[reportPrivateUsage]
    assert worker(down, NOW).state == "unknown"


def test_a_narrow_caller_sees_its_own_running_jobs_and_no_slots() -> None:
    worker = status._worker  # pyright: ignore[reportPrivateUsage]
    busy = snapshot(running_jobs=3, concurrency=4)
    assert {"label": "Jobs", "value": "3 of 4 slots busy"} in [
        fact.model_dump(include={"label", "value"}) for fact in worker(busy, NOW).facts
    ]
    busy.instance = False
    busy.queue = QueueStats(running=1)
    narrow = worker(busy, NOW)
    assert narrow.summary == "Running 1 job"
    assert all(fact.label != "Jobs" for fact in narrow.facts)


def test_scheduler_states() -> None:
    scheduler = status._scheduler  # pyright: ignore[reportPrivateUsage]
    assert scheduler(snapshot(), NOW).state == "unknown"  # an older worker reports no tick
    assert scheduler(snapshot(scheduler_last_tick_at=None), NOW).state == "warn"
    starting = snapshot(scheduler_last_tick_at=None, started_at=NOW.isoformat())
    assert scheduler(starting, NOW).summary == "Starting with the worker"
    fresh = scheduler(snapshot(scheduler_last_tick_at=NOW.isoformat()), NOW)
    assert fresh.state == "ok"
    old = (NOW - timedelta(minutes=10)).isoformat()
    assert scheduler(snapshot(scheduler_last_tick_at=old), NOW).state == "error"
    assert scheduler(snapshot(seen=timedelta(hours=1)), NOW).state == "unknown"


def test_queue_backlog() -> None:
    queue = status._queue  # pyright: ignore[reportPrivateUsage]
    quiet = snapshot()
    assert queue(quiet, NOW).summary == "Empty"
    quiet.queue = QueueStats(queued=3, running=1, oldest_queued_at=NOW - timedelta(minutes=1))
    assert (queue(quiet, NOW).state, queue(quiet, NOW).summary) == ("ok", "3 queued, 1 running")
    quiet.queue = QueueStats(queued=1, oldest_queued_at=NOW - timedelta(minutes=40))
    backlog = queue(quiet, NOW)
    assert backlog.state == "warn" and "40 min" in backlog.summary


def test_engine_and_ffmpeg() -> None:
    engine = status._engine  # pyright: ignore[reportPrivateUsage]
    ffmpeg = status._ffmpeg  # pyright: ignore[reportPrivateUsage]
    assert engine(ENGINE, snapshot(engine_version="2026.10.20"), NOW).state == "ok"
    mismatch = engine(ENGINE, snapshot(engine_version="2026.9.1"), NOW)
    assert mismatch.state == "warn" and "restart" in mismatch.summary
    old = EngineVersion(name="yt-dlp", version="2026.6.1", channel="nightly",
                        release_date=date(2026, 6, 1))  # fmt: skip
    assert engine(old, snapshot(), NOW).state == "warn"
    assert ffmpeg(ENGINE, snapshot()).summary == "7.1"  # the api's when the worker is silent
    assert ffmpeg(ENGINE, snapshot(ffmpeg_version=None)).state == "error"


def test_crawlers_by_service() -> None:
    crawlers = status._crawlers  # pyright: ignore[reportPrivateUsage]
    observed = snapshot()
    assert crawlers(observed, set()).state == "unknown"
    observed.crawlers = [
        CrawlerStats(service="RSS", runs=10, failed=0, last_success_at=NOW),
        CrawlerStats(service="YouTube", runs=4, failed=1, last_error="HTTP 429"),
    ]
    report = crawlers(observed, {"youtube"})
    assert report.state == "warn" and report.summary == "1 of 14 Refreshes failed in 24 h"
    facts = {fact.label: fact for fact in report.facts}
    assert facts["RSS"].value == "10 ok" and facts["RSS"].detail is None
    assert facts["YouTube"].value == "3 ok, 1 failed · cookies"
    assert facts["YouTube"].detail == "HTTP 429"
    observed.crawlers = [CrawlerStats(service="YouTube", runs=3, failed=3, last_error="bot")]
    down = crawlers(observed, set())
    assert down.state == "error" and down.summary == "YouTube failing on every Refresh"


def quota(name: str, used: int, limit: int | None, *, removed: bool = False) -> QuotaStatus:
    from copycast.domain.quota import quota_state

    return QuotaStatus(
        username=name,
        used_bytes=used,
        quota_bytes=limit,
        state=quota_state(used, limit),
        removed=removed,
    )


def test_quota_summary_and_meter() -> None:
    assert status.quota_summary(quota("a", 3_200_000_000, 10_000_000_000)) == (
        "3.2 GB of 10.0 GB (32 %)"
    )
    assert status.quota_summary(quota("a", 3_200_000_000, None)) == "3.2 GB used (unlimited)"
    meter = status.quota_meter(quota("a", 250, 1000))
    assert (meter.used_bytes, meter.quota_bytes, meter.ratio) == (250, 1000, 0.25)
    assert status.quota_meter(quota("a", 5, None)).ratio is None
    assert status.quota_meter(quota("a", 0, 0)).ratio == 1.0  # 0 of 0 is full (over)


def test_the_quota_component_for_a_member_and_an_admin() -> None:
    component = status._quota  # pyright: ignore[reportPrivateUsage]
    observed = snapshot()
    assert component(observed, False).state == "unknown"
    observed.quota = quota("alice", 950, 1000)
    observed.quotas = [
        quota("root", 10, None),
        quota("alice", 950, 1000),
        quota("bob", 1000, 1000),
        quota("gone", 7, 1000, removed=True),
    ]
    member = component(observed, False)
    assert (member.id, member.name, member.state) == ("quota", "Storage", "warn")
    assert member.summary == "950 B of 1.0 kB (95 %)"
    assert member.meter is not None and member.meter.ratio == 0.95
    assert member.facts == [], "a Member sees only their own"

    admin = component(observed, True)
    assert [(f.label, f.state) for f in admin.facts] == [
        ("root", "ok"),
        ("alice", "warn"),
        ("bob", "error"),
        ("gone (removed)", "ok"),
    ]
    assert admin.facts[2].value == "1.0 kB of 1.0 kB (100 %)"
    assert admin.facts[0].meter is not None and admin.facts[0].meter.ratio is None
    observed.quota = quota("bob", 1000, 1000)
    assert component(observed, False).state == "error"
    observed.quota = quota("root", 10, None)
    assert component(observed, True).state == "ok"


def test_the_object_store_keeps_its_instance_facts_for_admins() -> None:
    storage = status._storage  # pyright: ignore[reportPrivateUsage]
    ctx: Any = SimpleNamespace(
        settings=SimpleNamespace(
            storage=SimpleNamespace(url="s3://copycast", endpoint="http://seaweedfs:8333")
        ),
        layout=SimpleNamespace(read_version=lambda: "2"),
        expected_layout_version="2",
    )
    observed = snapshot(free_bytes=50 * 1024**3)
    observed.totals = Totals(storage_bytes=123)
    admin = storage(ctx, observed, True)
    assert (admin.id, admin.name, admin.state) == ("storage", "Object store", "ok")
    labels = [fact.label for fact in admin.facts]
    assert labels == ["Backend", "Bucket", "Endpoint", "Used by Feeds", "Worker Scratch free"]
    member = storage(ctx, observed, False)
    assert [fact.label for fact in member.facts] == ["Backend", "Worker Scratch free"]
    assert member.name == "Object store" and member.state == "ok"
