"""The Source gateway hands the acting owner to ``probe()``, which the cache can key on."""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from copycast.adapters.sources import probe as probe_module
from copycast.app import SourceGatewayAdapter
from copycast.application.ports import CancelToken
from copycast.settings import get_settings
from tests.support.fake_engine import FakeEngine


def test_probe_takes_an_owner_keyword() -> None:
    parameter = inspect.signature(probe_module.probe).parameters["owner"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is None


def test_the_gateway_passes_the_owner_and_their_jar_to_probe(
    monkeypatch: pytest.MonkeyPatch, layout: Any
) -> None:
    seen: dict[str, Any] = {}

    def fake_probe(url: str, **kwargs: Any) -> probe_module.ProbeResult:
        seen.update(kwargs)
        return probe_module.ProbeResult(input_url=url, candidates=[])

    monkeypatch.setattr(probe_module, "probe", fake_probe)
    gateway = SourceGatewayAdapter(get_settings(), FakeEngine(), layout)
    assert (
        gateway.probe("https://x.example/feed", options={}, cancel=CancelToken(), owner="bob") == []
    )
    assert seen["owner"] == "bob"
    assert seen["cookie_jar"] == gateway._jar("bob")  # pyright: ignore[reportPrivateUsage]
