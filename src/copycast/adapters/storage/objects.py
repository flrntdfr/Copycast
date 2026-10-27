"""The Object store: every byte Copycast keeps, behind one small object-store API over obstore.

``ObjectStore`` wraps an ``obstore`` store with the handful of calls Copycast
makes: whole-object reads and writes (a single PUT is atomic in S3, so there is
no temp-and-rename), multipart uploads of local files, ``head``, listings,
deletes and ranged async reads for the media proxy. Keys are POSIX paths
relative to the bucket root, the same names the data directory used before
layout 2 (``feeds/{feed_id}/media/{item_id}.{ext}``, ``engine/defaults.json``).

:func:`open_store` builds the store from ``[storage]``: ``s3://bucket`` (the
only durable backend) or ``memory://name``, a process-local store the tests
and ``copycast openapi`` use; every ``memory://`` store of one name is the
same object, so an api and a worker container built in one test share it.
"""

from __future__ import annotations

import threading
from collections.abc import AsyncIterator, Callable, Iterable, Sequence
from datetime import UTC, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import urlsplit

import obstore
from obstore.exceptions import NotModifiedError, PreconditionError
from obstore.store import MemoryStore, S3Store

from copycast.application.ports import StoredObject
from copycast.settings import SettingsError, StorageSettings

if TYPE_CHECKING:
    from obstore import GetOptions, ListResult, ObjectMeta
    from obstore.store import ClientConfig

MULTIPART_CHUNK_BYTES: Final = 16 * 1024 * 1024
"""Parts of a multipart upload; uploads of smaller files are a single PUT."""
MULTIPART_CONCURRENCY: Final = 8
STREAM_CHUNK_BYTES: Final = 256 * 1024
"""What the media proxy hands the ASGI server per ``send``."""
DELETE_BATCH: Final = 1000
"""S3's DeleteObjects limit."""

# obstore's listings are overloaded on an optional pyarrow return; only the plain form is used.
_list: Callable[..., Iterable[Sequence[ObjectMeta]]] = obstore.list  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
_list_dirs: Callable[..., ListResult[Sequence[ObjectMeta]]] = obstore.list_with_delimiter  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]

_MEMORY: dict[str, MemoryStore] = {}
_MEMORY_LOCK = threading.Lock()


class StorageUnavailable(SettingsError):
    """The Object store cannot be reached or refuses the configured credentials."""


class NotModified(Exception):
    """A conditional read matched (``If-None-Match``)."""


class RangeNotSatisfiable(Exception):
    """A ranged read starts past the end of the object."""


def _info(meta: ObjectMeta, key: str | None = None) -> StoredObject:
    """``key`` overrides the path ``meta`` names: a prefixed S3 store's ``head`` reports
    the full path, prefix included, while its listings report it relative to the prefix."""
    modified = meta["last_modified"]
    if modified.tzinfo is None:
        modified = modified.replace(tzinfo=UTC)
    return StoredObject(
        key=key if key is not None else meta["path"],
        size=meta["size"],
        last_modified=modified,
        etag=meta["e_tag"],
    )


class ObjectStore:
    """Synchronous calls for the jobs and services (run off the loop), async ones for serving."""

    def __init__(self, store: Any, *, url: str, stream_store: Any | None = None) -> None:
        self._store = store
        self._stream_store = stream_store if stream_store is not None else store
        self.url = url

    # ------------------------------------------------------------------ reads

    def head(self, key: str) -> StoredObject | None:
        try:
            return _info(obstore.head(self._store, key), key)
        except FileNotFoundError:
            return None

    def exists(self, key: str) -> bool:
        return self.head(key) is not None

    def get_bytes(self, key: str) -> bytes | None:
        try:
            return bytes(obstore.get(self._store, key).bytes())
        except FileNotFoundError:
            return None

    def get_text(self, key: str) -> str | None:
        body = self.get_bytes(key)
        return body.decode("utf-8") if body is not None else None

    def download(self, key: str, path: Path) -> bool:
        """Copy ``key`` to the local file ``path`` (streamed); ``False`` when it is absent."""
        try:
            result = obstore.get(self._store, key)
        except FileNotFoundError:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            for chunk in result.stream(min_chunk_size=MULTIPART_CHUNK_BYTES):
                handle.write(chunk)
        return True

    def list(self, prefix: str) -> list[StoredObject]:
        """Every object under ``prefix`` (a key prefix ending in ``/``), sorted by key."""
        found = [_info(meta) for chunk in _list(self._store, prefix) for meta in chunk]
        return sorted(found, key=lambda o: o.key)

    def has_any(self, prefix: str) -> bool:
        """Whether at least one object lives under ``prefix`` (one small listing call)."""
        return any(chunk for chunk in _list(self._store, prefix, chunk_size=1))

    def list_dirs(self, prefix: str) -> list[str]:
        """The names of the "directories" right under ``prefix`` (common prefixes), sorted.

        Some servers (SeaweedFS) keep reporting a prefix whose objects were all deleted;
        callers that need live ones check :meth:`has_any`.
        """
        listed = _list_dirs(self._store, prefix.rstrip("/") or None)
        names = [p.rstrip("/").rsplit("/", 1)[-1] for p in listed["common_prefixes"]]
        return sorted(names)

    # ------------------------------------------------------------------ writes

    def put_bytes(self, key: str, data: bytes | str, *, content_type: str | None = None) -> int:
        body = data.encode("utf-8") if isinstance(data, str) else data
        attributes = {"Content-Type": content_type} if content_type else None
        obstore.put(self._store, key, body, attributes=attributes, use_multipart=False)
        return len(body)

    def put_file(self, key: str, path: Path, *, content_type: str | None = None) -> int:
        """Upload a local file (multipart when large); returns its size."""
        size = path.stat().st_size
        attributes = {"Content-Type": content_type} if content_type else None
        obstore.put(
            self._store,
            key,
            path,
            attributes=attributes,
            use_multipart=size > MULTIPART_CHUNK_BYTES,
            chunk_size=MULTIPART_CHUNK_BYTES,
            max_concurrency=MULTIPART_CONCURRENCY,
        )
        return size

    def copy(self, source: str, target: str) -> None:
        """A server-side copy (S3 CopyObject); raises ``FileNotFoundError`` when absent."""
        obstore.copy(self._store, source, target)

    def delete(self, keys: str | Iterable[str]) -> None:
        """Delete objects; absent keys are not an error."""
        batch = [keys] if isinstance(keys, str) else list(keys)
        for start in range(0, len(batch), DELETE_BATCH):
            chunk = batch[start : start + DELETE_BATCH]
            if chunk:
                obstore.delete(self._store, chunk)

    def delete_prefix(self, prefix: str) -> int:
        keys = [o.key for o in self.list(prefix)]
        self.delete(keys)
        return len(keys)

    def ping(self) -> None:
        """One small listing: a wrong endpoint, bucket or key pair fails here with one line."""
        try:
            next(iter(_list(self._store, "LAYOUT", chunk_size=1)), None)
        except Exception as exc:
            raise StorageUnavailable(
                f"storage {self.url} is unreachable or refused the keys "
                f"(check storage.endpoint, the bucket and the key pair): {_first_line(str(exc))}"
            ) from exc

    # ------------------------------------------------------------------ async (serving)

    async def head_async(self, key: str) -> StoredObject | None:
        try:
            return _info(await obstore.head_async(self._store, key), key)
        except FileNotFoundError:
            return None

    async def get_bytes_async(self, key: str) -> bytes | None:
        try:
            result = await obstore.get_async(self._store, key)
        except FileNotFoundError:
            return None
        return bytes(await result.bytes_async())

    async def put_bytes_async(self, key: str, data: bytes) -> None:
        await obstore.put_async(self._store, key, data, use_multipart=False)

    async def stream(
        self,
        key: str,
        *,
        start: int = 0,
        end: int | None = None,
        if_match: str | None = None,
    ) -> AsyncIterator[bytes]:
        """The bytes ``[start, end)`` of ``key``, chunked; ``if_match`` pins the version read.

        Reads through the stream client: an HTTP client's request timeout covers reading the
        body, and the media proxy reads at the podcast app's pace (backpressure), so one GET
        of a long Episode can stay open for many minutes. Raises ``FileNotFoundError`` when
        the object is gone and
        :class:`RangeNotSatisfiable` when ``start`` is past its end; an object
        replaced since ``head`` (``if_match`` fails) reads as gone.
        """
        options: GetOptions = {}
        if end is not None:
            options["range"] = (start, end)
        elif start:
            options["range"] = {"offset": start}
        if if_match is not None:
            options["if_match"] = if_match
        try:
            result = await obstore.get_async(self._stream_store, key, options=options)
        except PreconditionError as exc:
            raise FileNotFoundError(key) from exc
        except NotModifiedError as exc:  # pragma: no cover - no If-None-Match is sent
            raise NotModified(key) from exc
        except FileNotFoundError:
            raise
        except Exception as exc:
            if "range" in str(exc).lower():
                raise RangeNotSatisfiable(key) from exc
            raise
        async for chunk in result.stream(min_chunk_size=STREAM_CHUNK_BYTES):
            yield bytes(chunk)


# --------------------------------------------------------------------------- construction


def _memory(name: str) -> MemoryStore:
    with _MEMORY_LOCK:
        store = _MEMORY.get(name)
        if store is None:
            store = _MEMORY[name] = MemoryStore()
        return store


def forget_memory_store(name: str) -> None:
    """Drop a ``memory://`` store (tests call it when their store goes out of scope)."""
    with _MEMORY_LOCK:
        _MEMORY.pop(name, None)


def open_store(settings: StorageSettings) -> ObjectStore:
    """The Object store named by ``[storage] url``; raises ``SettingsError`` for a bad URL."""
    parts = urlsplit(settings.url)
    scheme = parts.scheme.lower()
    if scheme == "memory":
        name = (parts.netloc + parts.path).strip("/") or "default"
        return ObjectStore(_memory(name), url=settings.url)
    if scheme != "s3" or not parts.netloc:
        raise SettingsError(
            f"storage.url must be s3://<bucket>[/<prefix>] or memory://<name>, got {settings.url!r}"
        )
    allow_http = settings.endpoint.startswith("http://")
    client: ClientConfig = {
        "allow_http": allow_http,
        "timeout": timedelta(seconds=settings.request_timeout_seconds),
    }
    stream_client: ClientConfig = {
        "allow_http": allow_http,
        "timeout": timedelta(seconds=settings.stream_timeout_seconds),
    }
    config: dict[str, Any] = {"region": settings.region}
    if settings.endpoint:
        config["endpoint"] = settings.endpoint
    if settings.access_key_id:
        config["access_key_id"] = settings.access_key_id
    if settings.secret_access_key:
        config["secret_access_key"] = settings.secret_access_key
    config["virtual_hosted_style_request"] = settings.virtual_hosted_style
    prefix = parts.path.strip("/") or None
    store = S3Store(parts.netloc, prefix=prefix, client_options=client, **config)
    stream_store = S3Store(parts.netloc, prefix=prefix, client_options=stream_client, **config)
    return ObjectStore(store, url=settings.url, stream_store=stream_store)


def _first_line(text: str) -> str:
    return text.strip().splitlines()[0] if text.strip() else "unknown error"


__all__ = [
    "DELETE_BATCH",
    "MULTIPART_CHUNK_BYTES",
    "STREAM_CHUNK_BYTES",
    "NotModified",
    "ObjectStore",
    "RangeNotSatisfiable",
    "StorageUnavailable",
    "forget_memory_store",
    "open_store",
]
