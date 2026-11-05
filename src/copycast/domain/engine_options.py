"""Raw yt-dlp options: the operator's deny-list and the per-Mirror allowlist.

Two validations, kept apart on purpose:

- ``[engine.options]`` in the configuration file (scope ``global``): the operator
  controls that file, so any ``YoutubeDL`` param passes except the ones Copycast owns
  (:data:`ENGINE_OWNED_OPTIONS`). :meth:`EngineOptions.validate_global`.
- A Mirror's ``engine_options`` (scope ``feed``), set through the API and MCP by any
  write credential: only the params of :data:`FEED_OPTIONS`, which cannot run a
  command, read or write a path, change where traffic goes or leak a credential, with
  values of the expected shape (SEC-1: the old deny-list let ``netrc_cmd`` run a shell
  command). :meth:`EngineOptions.validate_feed` refuses at create and update, and
  :meth:`EngineOptions.sanitize_feed` drops what a stored row still holds before every
  engine call, so a row written before 2.3.0 is never executed.

Reviewed against the ``YoutubeDL`` docstring of yt-dlp 2026.10.29; a param a later
release adds stays refused until it is listed here.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from types import MappingProxyType
from typing import Any, Final, Literal, cast

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
"""Keys Copycast sets itself: refused in both scopes, stripped from both layers."""

ValueCheck = Callable[[Any], bool]


def _count(low: int, high: int) -> ValueCheck:
    def check(value: Any) -> bool:
        return type(value) is int and low <= value <= high

    return check


def _seconds(high: float) -> ValueCheck:
    def check(value: Any) -> bool:
        return type(value) in (int, float) and 0 <= value <= high

    return check


def _flag(value: Any) -> bool:
    return type(value) is bool


def _text(max_len: int) -> ValueCheck:
    def check(value: Any) -> bool:
        return type(value) is str and 0 < len(value) <= max_len

    return check


def _texts(max_items: int, max_len: int) -> ValueCheck:
    item = _text(max_len)

    def check(value: Any) -> bool:
        if type(value) is not list:
            return False
        items = cast(list[Any], value)
        return len(items) <= max_items and all(item(entry) for entry in items)

    return check


_LANGUAGE_PATTERN: Final = re.compile(r"-?(?:all|[A-Za-z0-9]{1,8}(?:[-_][A-Za-z0-9]{1,8})*)")


def _subtitle_languages(value: Any) -> bool:
    """Language codes, ``all`` and ``-`` exclusions (``-live_chat``); no regex syntax.

    yt-dlp compiles each entry as a regular expression and matches it against every
    subtitle language the Source offers, holding the GIL: a backtracking pattern would
    stall the whole worker, an invalid one fails every fetch of the Mirror.
    """
    return _texts(32, 64)(value) and all(
        _LANGUAGE_PATTERN.fullmatch(entry) for entry in cast(list[str], value)
    )


def _extractor_args(value: Any) -> bool:
    """Only the outer shape; entries are checked one by one against FEED_EXTRACTOR_ARGS."""
    return isinstance(value, Mapping)


_BYTES_PER_SECOND: Final = _count(1, 10**12)
_RETRIES: Final = _count(0, 100)
_SLEEP: Final = _seconds(3600)

FEED_OPTIONS: Final[Mapping[str, ValueCheck]] = MappingProxyType(
    {
        # Format choice among what the Source offers: ``format`` itself is Copycast's
        # (engine-owned); sorting only reorders the formats the extractor returned.
        "format_sort": _texts(32, 64),
        "format_sort_force": _flag,
        "prefer_free_formats": _flag,
        # Pacing and retries: numbers only, bounded so a Mirror cannot park a worker for
        # days. retries, fragment_retries and socket_timeout are overlaid by
        # BASE_OPTIONS on a fetch, so they count for listings only.
        "ratelimit": _BYTES_PER_SECOND,
        "throttledratelimit": _BYTES_PER_SECOND,
        "sleep_interval": _SLEEP,
        "max_sleep_interval": _SLEEP,
        "sleep_interval_requests": _SLEEP,
        "sleep_interval_subtitles": _SLEEP,
        "retries": _RETRIES,
        "fragment_retries": _RETRIES,
        "extractor_retries": _RETRIES,
        "file_access_retries": _RETRIES,
        "socket_timeout": _seconds(600),
        "concurrent_fragment_downloads": _count(1, 16),
        "skip_unavailable_fragments": _flag,
        # Subtitle choice: language and format codes, never a file name nor a pattern
        # (yt-dlp compiles subtitleslangs entries as regexes). writesubtitles is
        # engine-owned; writeautomaticsub and subtitlesformat are overlaid by BASE_OPTIONS
        # on a fetch.
        "writeautomaticsub": _flag,
        "subtitleslangs": _subtitle_languages,
        "subtitlesformat": _text(64),
        # Geo bypass fakes an X-Forwarded-For header on requests to the same hosts; it
        # routes nothing elsewhere (``proxy`` and ``geo_verification_proxy`` do).
        "geo_bypass": _flag,
        "geo_bypass_country": _text(8),
        "geo_bypass_ip_block": _text(64),
        # A content filter: it only skips items.
        "age_limit": _count(0, 99),
        "extractor_args": _extractor_args,
    }
)
"""The per-Mirror allowlist: each key with the check its value must pass.

Everything else is refused, notably: credentials and where they come from
(``username``, ``password``, ``videopassword``, ``ap_*``, ``usenetrc``,
``netrc_location``, ``netrc_cmd``, which runs a shell command, ``cookiefile``,
``client_certificate*``); paths and files (``outtmpl``, ``paths``, ``download_archive``,
``cachedir``, ``load_info_filename``, ``print_to_file``, ``write*`` sidecars,
``enable_file_urls``); executables and their arguments (``external_downloader*``,
``postprocessors``, ``postprocessor_args``, ``ffmpeg_location``, ``js_runtimes``,
``remote_components``, ``exec*``); network egress (``proxy``, ``geo_verification_proxy``,
``source_address``, ``impersonate``, ``nocheckcertificate``, ``prefer_insecure``,
``legacyserverconnect``); ``http_headers``, since yt-dlp takes a ``Ytdl-Request-Proxy``
header as the request's proxy and a ``Cookie`` or ``Authorization`` header would follow
redirects to other hosts; hooks, callbacks and the logger; ``verbose`` and
``debug_printtraffic``, which print request headers (cookies included) into the logs;
``mark_watched``, which acts on the account behind the Cookie files; and every
engine-owned key.
"""

FEED_EXTRACTOR_ARGS: Final[Mapping[str, frozenset[str]]] = MappingProxyType(
    {
        # Metadata language, client choice and what to skip: names yt-dlp knows, never
        # a host, a token or a file.
        "youtube": frozenset({"lang", "player_client", "player_skip", "skip", "formats"}),
        "youtubetab": frozenset({"approximate_date", "skip"}),
        # A TLS fingerprint to present; the requests still go where the URL says.
        "generic": frozenset({"impersonate"}),
    }
)
"""The ``extractor_args`` a Mirror may set, by extractor key (lower case, as yt-dlp reads them).

Refused because they redirect traffic, fetch or run code, or carry an identity:
``youtube`` ``innertube_host``, ``po_token``, ``visitor_data``, ``data_sync_id``,
``player_js_version``; ``youtube-ejs`` (a custom repository of JavaScript the Engine
runs); ``youtubepot-*`` (PO token providers, whose plugins take base URLs and script
paths); ``generic`` ``hls_key`` and ``key_query`` (keys fetched from URLs); and every
other extractor, unreviewed.
"""

EXTRACTOR_ARG_VALUE: Final = _texts(16, 64)
"""yt-dlp wants every extractor argument as a list of strings (it iterates a bare string)."""


class EngineOptionRejected(DomainError):
    """An option Copycast owns (any scope) or one off the per-Mirror allowlist (feed scope)."""

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


def _label(key: object) -> str:
    return key if isinstance(key, str) else repr(key)


def _sanitize_extractor_args(
    value: Mapping[object, Any],
) -> tuple[dict[str, dict[str, list[str]]], list[str]]:
    kept: dict[str, dict[str, list[str]]] = {}
    refused: list[str] = []
    for extractor, args in value.items():
        where = f"extractor_args.{_label(extractor)}"
        allowed = FEED_EXTRACTOR_ARGS.get(extractor) if isinstance(extractor, str) else None
        if allowed is None or not isinstance(args, Mapping):
            refused.append(where)
            continue
        entries: dict[str, list[str]] = {}
        for arg, arg_value in cast(Mapping[object, Any], args).items():
            if isinstance(arg, str) and arg in allowed and EXTRACTOR_ARG_VALUE(arg_value):
                entries[arg] = list(cast(list[str], arg_value))
            else:
                refused.append(f"{where}.{_label(arg)}")
        if entries:
            kept[cast(str, extractor)] = entries
    return kept, refused


def _merge_extractor_args(below: Mapping[str, Any], above: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(below)
    for extractor, args in above.items():
        under = merged.get(extractor)
        if isinstance(under, Mapping) and isinstance(args, Mapping):
            merged[extractor] = {**cast(Mapping[str, Any], under), **cast(Mapping[str, Any], args)}
        else:
            merged[extractor] = args
    return merged


class EngineOptions:
    """Validation and layering of raw yt-dlp options."""

    @staticmethod
    def validate_global(data: Mapping[str, Any] | None) -> dict[str, Any]:
        """``[engine.options]``: a plain copy of ``data``, refusing the engine-owned keys."""
        if data is None:
            return {}
        rejected = sorted(key for key in data if key in ENGINE_OWNED_OPTIONS)
        if rejected:
            raise EngineOptionRejected(rejected[0], "global", keys=rejected)
        return dict(data)

    @staticmethod
    def sanitize_feed(data: Mapping[Any, Any] | None) -> tuple[dict[str, Any], list[str]]:
        """A Mirror's options split into what the allowlist keeps and what it refuses.

        Refusals are named by path (``netrc_cmd``, ``extractor_args.youtube.po_token``)
        and sorted; a value of the wrong shape refuses its key. Kept values are copies.
        """
        if not data:
            return {}, []
        kept: dict[str, Any] = {}
        refused: list[str] = []
        for key, value in data.items():
            check = FEED_OPTIONS.get(key) if isinstance(key, str) else None
            if check is None or not check(value):
                refused.append(_label(key))
            elif key == "extractor_args":
                extractor_args, nested = _sanitize_extractor_args(value)
                refused.extend(nested)
                if extractor_args:
                    kept[key] = extractor_args
            else:
                kept[key] = list(cast(list[Any], value)) if isinstance(value, list) else value
        return kept, sorted(refused)

    @staticmethod
    def validate_feed(data: Mapping[Any, Any] | None) -> dict[str, Any]:
        """A Mirror's ``engine_options`` to store, refusing every key off the allowlist."""
        kept, refused = EngineOptions.sanitize_feed(data)
        if refused:
            raise EngineOptionRejected(refused[0], "feed", keys=refused)
        return kept

    @staticmethod
    def merge(
        global_options: Mapping[str, Any] | None,
        feed_options: Mapping[str, Any] | None,
        base_options: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Layer ``[engine.options]`` -> ``feeds.engine_options`` -> ``BASE_OPTIONS``; last wins.

        ``extractor_args`` merge per extractor, then per argument: a Mirror that sets
        ``youtube.player_client`` keeps the operator's ``youtube.po_token`` and
        ``youtubepot-*`` providers, which only ``[engine.options]`` may set. A later
        layer still replaces an argument it names.
        """
        merged: dict[str, Any] = {}
        for layer in (global_options, feed_options, base_options):
            if not layer:
                continue
            for key, value in layer.items():
                below = merged.get(key)
                if (
                    key == "extractor_args"
                    and isinstance(below, Mapping)
                    and isinstance(value, Mapping)
                ):
                    merged[key] = _merge_extractor_args(
                        cast(Mapping[str, Any], below), cast(Mapping[str, Any], value)
                    )
                else:
                    merged[key] = value
        return merged
