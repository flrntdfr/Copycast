from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from copycast.application.ports import (
    Cancelled,
    CancelToken,
    Engine,
    EngineError,
    FetchSpec,
    NullEngineLog,
    Progress,
    SynthItem,
    TransientError,
)
from copycast.domain.enums import EnginePhase, FetchKind
from tests.support.factories import listing
from tests.support.fake_engine import FakeEngine, part_files


def _spec(root: Path, item_id: str = "item1", kind: FetchKind = FetchKind.ytdlp) -> FetchSpec:
    synth = (
        SynthItem(id=item_id, title="T", url="https://p.example/1.mp3")
        if kind is FetchKind.direct
        else None
    )
    return FetchSpec(
        kind=kind,
        url="https://p.example/1.mp3",
        item_id=item_id,
        home_dir=root / "media",
        temp_dir=root / "tmp",
        synth=synth,
    )


def test_fake_engine_satisfies_the_port() -> None:
    engine: Engine = FakeEngine()
    assert engine.version().name == "fake-engine"


def test_scripted_listing_and_records(engine: FakeEngine) -> None:
    served = listing(2)
    engine.script_listing("https://p.example/feed.xml", served)
    got = engine.list_source(
        "https://p.example/feed.xml/", {"ratelimit": 1}, CancelToken(), NullEngineLog()
    )
    assert got == served
    assert engine.records.listed_urls == ["https://p.example/feed.xml/"]
    assert engine.records.options_seen == [{"ratelimit": 1}]
    with pytest.raises(EngineError, match="no scripted listing"):
        engine.list_source("https://other.example", {}, CancelToken(), NullEngineLog())


def test_fail_next_applies_once(engine: FakeEngine, tmp_path: Path) -> None:
    engine.fail_next(TransientError("boom"))
    with pytest.raises(TransientError):
        engine.fetch_item(_spec(tmp_path), {}, CancelToken(), lambda _: None, NullEngineLog())
    result = engine.fetch_item(_spec(tmp_path), {}, CancelToken(), lambda _: None, NullEngineLog())
    assert result.audio_path.exists()


def test_fetch_writes_media_and_sidecar_and_reports_progress(
    engine: FakeEngine, tmp_path: Path
) -> None:
    seen: list[Progress] = []
    result = engine.fetch_item(
        _spec(tmp_path, kind=FetchKind.direct), {}, CancelToken(), seen.append, NullEngineLog()
    )
    assert result.audio_path == tmp_path / "media" / "item1.m4a"
    assert result.audio_path.read_bytes() == engine.audio_bytes
    assert result.size_bytes == len(engine.audio_bytes)
    assert result.ext == "m4a" and result.mime == "audio/mp4"
    assert result.info_json_path is not None
    info = json.loads(result.info_json_path.read_text())
    assert info["extractor"] == "copycast:rss" and info["id"] == "item1"
    assert [p.phase for p in seen] == [
        EnginePhase.downloading,
        EnginePhase.postprocessing,
        EnginePhase.finished,
    ]
    assert seen[-1].percent == 100.0
    assert engine.records.fetched_item_ids == ["item1"]


def test_direct_fetch_requires_synth(engine: FakeEngine, tmp_path: Path) -> None:
    spec = FetchSpec(
        kind=FetchKind.direct,
        url="u",
        item_id="i",
        home_dir=tmp_path,
        temp_dir=tmp_path,
        synth=None,
    )
    with pytest.raises(EngineError, match="synthesized"):
        engine.fetch_item(spec, {}, CancelToken(), lambda _: None, NullEngineLog())


def test_blocked_fetch_leaves_a_part_file_and_honours_cancel(
    engine: FakeEngine, tmp_path: Path
) -> None:
    gate = threading.Event()
    engine.block_fetches(gate)
    token = CancelToken()
    errors: list[BaseException] = []

    def run() -> None:
        try:
            engine.fetch_item(_spec(tmp_path), {}, token, lambda _: None, NullEngineLog())
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    deadline = threading.Event()
    for _ in range(100):
        if part_files(tmp_path / "tmp"):
            break
        deadline.wait(0.02)
    assert part_files(tmp_path / "tmp") == [tmp_path / "tmp" / "item1.m4a.part"]
    token.cancel()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], Cancelled)
    assert part_files(tmp_path / "tmp"), ".part must survive cancellation"
    assert not (tmp_path / "media" / "item1.m4a").exists()


def test_blocked_fetch_completes_once_released(engine: FakeEngine, tmp_path: Path) -> None:
    gate = threading.Event()
    engine.block_fetches(gate)
    results: list[Path] = []
    thread = threading.Thread(
        target=lambda: results.append(
            engine.fetch_item(
                _spec(tmp_path), {}, CancelToken(), lambda _: None, NullEngineLog()
            ).audio_path
        )
    )
    thread.start()
    gate.set()
    thread.join(timeout=5)
    assert results == [tmp_path / "media" / "item1.m4a"]
    assert part_files(tmp_path / "tmp") == []


def test_reset_clears_everything(engine: FakeEngine) -> None:
    engine.script_listing("u", listing(1))
    engine.fail_next(EngineError("x"))
    engine.reset()
    assert engine.records.listings == []
    with pytest.raises(EngineError, match="no scripted listing"):
        engine.list_source("u", {}, CancelToken(), NullEngineLog())
