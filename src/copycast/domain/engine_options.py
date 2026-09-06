"""Raw yt-dlp option pass-through with the keys Copycast owns fenced off."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final, Literal

from copycast.domain.exceptions import DomainError

Scope = Literal["global", "feed"]

ENGINE_OWNED_OPTIONS: Final = frozenset(
    {
        "outtmpl",
        "outtmpl_na_placeholder",
        "paths",
        "download_archive",
        "progress_hooks",
        "postprocessor_hooks",
        "post_hooks",
        "logger",
        "postprocessors",
        "format",
        "extract_flat",
        "writeinfojson",
        "writethumbnail",
        "writesubtitles",
        "skip_download",
        "simulate",
        "quiet",
        "noprogress",
        "daterange",
        "playlist_items",
        "noplaylist",
        "exec",
        "exec_cmd",
        "external_downloader",
        "external_downloader_args",
        "ffmpeg_location",
        "cookiesfrombrowser",
        "load_info_filename",
        "batchfile",
        "daemonize",
    }
)

GLOBAL_ONLY_OPTIONS: Final = frozenset({"cookiefile", "proxy", "geo_verification_proxy"})


class EngineOptionRejected(DomainError):
    """An option Copycast owns (any scope) or an operator-only option (feed scope) was given."""

    slug = "engine-option-rejected"
    status = 422

    def __init__(self, key: str, scope: Scope, *, keys: Sequence[str] | None = None) -> None:
        self.key = key
        self.scope: Scope = scope
        self.keys: list[str] = list(keys) if keys else [key]
        if scope == "global":
            where = "[engine.options] in the configuration"
        else:
            where = "a Feed's engine_options"
        listed = ", ".join(sorted(self.keys))
        super().__init__(
            f"engine option{'s' if len(self.keys) > 1 else ''} not allowed in {where}: {listed}",
            keys=sorted(self.keys),
            scope=scope,
        )


class EngineOptions:
    """Validation and layering of raw yt-dlp options."""

    @staticmethod
    def rejected_keys(data: Mapping[str, Any], scope: Scope) -> list[str]:
        forbidden = (
            ENGINE_OWNED_OPTIONS
            if scope == "global"
            else ENGINE_OWNED_OPTIONS | GLOBAL_ONLY_OPTIONS
        )
        return sorted(key for key in data if key in forbidden)

    @staticmethod
    def validate(data: Mapping[str, Any] | None, scope: Scope = "global") -> dict[str, Any]:
        """Return a plain dict copy of ``data`` or raise :class:`EngineOptionRejected`.

        ``global`` rejects the engine-owned keys; ``feed`` additionally rejects
        ``GLOBAL_ONLY_OPTIONS``.
        """
        if data is None:
            return {}
        rejected = EngineOptions.rejected_keys(data, scope)
        if rejected:
            raise EngineOptionRejected(rejected[0], scope, keys=rejected)
        return dict(data)

    @staticmethod
    def merge(
        global_options: Mapping[str, Any] | None,
        feed_options: Mapping[str, Any] | None,
        base_options: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Layer ``[engine.options]`` -> ``feeds.engine_options`` -> ``BASE_OPTIONS``; last wins."""
        merged: dict[str, Any] = {}
        for layer in (global_options, feed_options, base_options):
            if layer:
                merged.update(layer)
        return merged
