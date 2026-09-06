"""The yt-dlp engine adapter.

``build_engine(settings)`` returns the :class:`copycast.application.ports.Engine`
implementation; ``engine_info()`` reports the yt-dlp and ffmpeg versions.
Both are imported lazily here so ``copycast.app`` can resolve them by name
without pulling ``yt_dlp`` into processes that never fetch.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from copycast.adapters.engine.ytdlp import YtDlpEngine
    from copycast.application.ports import EngineVersion
    from copycast.settings import Settings


def build_engine(settings: Settings) -> YtDlpEngine:
    from copycast.adapters.engine.ytdlp import build_engine as _build

    return _build(settings)


def engine_info() -> EngineVersion:
    from copycast.adapters.engine.ytdlp import engine_info as _info

    return _info()


def __getattr__(name: str) -> Any:
    if name == "YtDlpEngine":
        from copycast.adapters.engine.ytdlp import YtDlpEngine

        return YtDlpEngine
    raise AttributeError(name)


__all__ = ["YtDlpEngine", "build_engine", "engine_info"]
